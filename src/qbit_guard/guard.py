#!/usr/bin/env python3
"""
Main guard orchestrator and CLI entrypoint.
"""

from __future__ import annotations

import sys
import time
import os
from typing import List

from .clients import HttpClient, QbitClient, RadarrClient, SonarrClient
from .config import Config
from .internet import InternetDates
from .logging_setup import get_logger
from .preair import PreAirGate, PreAirMovieGate
from .processing import IsoCleaner, MetadataFetcher
from .runtime import domain_from_url, log_stage_result, short_error
from .version import VERSION


log = get_logger("qbit-guard")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log.info("qbit-guard version %s starting (log level %s)", VERSION, LOG_LEVEL)


class TorrentGuard:
    """Main orchestrator that wires qB, Sonarr/Radarr, pre-air, metadata, and ISO/Extension cleaner together."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.http = HttpClient(cfg.ignore_tls, cfg.user_agent)
        self.qbit = QbitClient(cfg, self.http)
        self.sonarr = SonarrClient(cfg, self.http)
        self.radarr = RadarrClient(cfg, self.http)
        self.internet = InternetDates(cfg, self.http, self.sonarr, self.radarr)
        self.preair = PreAirGate(cfg, self.sonarr, self.internet)
        self.preair_movie = PreAirMovieGate(cfg, self.radarr, self.internet)
        self.metadata = MetadataFetcher(cfg, self.qbit)
        self.iso = IsoCleaner(cfg, self.qbit, self.sonarr, self.radarr)

    def run(self, torrent_hash: str, passed_category: str) -> None:
        outcome = "UNKNOWN"
        outcome_details = "hash=%s" % torrent_hash[:8]

        try:
            self.qbit.login()
        except Exception as e:
            outcome = "FAILED"
            outcome_details = "hash=%s stage=login error=%s" % (torrent_hash[:8], short_error(e))
            log_stage_result("Guard Outcome", outcome, outcome_details)
            raise RuntimeError("qBittorrent login failed: %s" % short_error(e)) from e

        try:
            info = self.qbit.info(torrent_hash)
        except Exception as e:
            outcome = "FAILED"
            outcome_details = "hash=%s stage=info error=%s" % (torrent_hash[:8], short_error(e))
            log_stage_result("Guard Outcome", outcome, outcome_details)
            raise RuntimeError("qB torrent info lookup failed: %s" % short_error(e)) from e

        if not info:
            log.info("No torrent found for hash; exiting.")
            log_stage_result("Guard Outcome", "SKIP", "hash=%s reason=not-found" % torrent_hash[:8])
            return

        category = (passed_category or info.get("category") or "").strip()
        category_norm = category.lower()
        name = info.get("name") or ""
        log.info("Processing: hash=%s category='%s' name='%s'", torrent_hash, category, name)

        if category_norm not in self.cfg.allowed_categories:
            log.info("Category '%s' not in allowed list %s - skipping.", category, sorted(self.cfg.allowed_categories))
            log_stage_result("Guard Outcome", "SKIP", "hash=%s reason=category-not-allowed" % torrent_hash[:8])
            return

        if not self.qbit.stop(torrent_hash):
            outcome = "FAILED"
            outcome_details = "hash=%s stage=initial-stop error=qB failed to stop torrent before checks" % torrent_hash[:8]
            log_stage_result("Guard Outcome", outcome, outcome_details)
            raise RuntimeError("qB failed to stop torrent before checks")
        self.qbit.add_tags(torrent_hash, "guard:stopped")
        self.qbit.remove_tags(torrent_hash, "guard:metadata-pending")

        if self.cfg.min_torrent_age_minutes > 0:
            creation_date = info.get("creation_date")
            if creation_date:
                torrent_age_seconds = time.time() - creation_date
                torrent_age_minutes = torrent_age_seconds / 60.0
                if torrent_age_minutes < self.cfg.min_torrent_age_minutes:
                    log.info(
                        "Torrent age check: BLOCKED (age=%.1f mins < minimum=%d mins). Likely fake torrent.",
                        torrent_age_minutes,
                        self.cfg.min_torrent_age_minutes,
                    )
                    if category_norm in self.cfg.sonarr_categories and self.sonarr.enabled:
                        try:
                            self.sonarr.blocklist_download(torrent_hash)
                        except Exception as e:
                            log.error("Sonarr blocklist error: %s", e)
                    if category_norm in self.cfg.radarr_categories and self.radarr.enabled:
                        try:
                            self.radarr.blocklist_download(torrent_hash)
                        except Exception as e:
                            log.error("Radarr blocklist error: %s", e)
                    self.qbit.add_tags(torrent_hash, "trash:too-new")
                    if not self.cfg.dry_run:
                        try:
                            self.qbit.delete(torrent_hash, self.cfg.delete_files)
                            log.info("Removed torrent %s (too new/fake, age=%.1f mins).", torrent_hash, torrent_age_minutes)
                        except Exception as e:
                            log.error("qB delete failed: %s", e)
                    else:
                        log.info("DRY-RUN: would remove torrent %s (too new, age=%.1f mins).", torrent_hash, torrent_age_minutes)
                    log_stage_result("Guard Outcome", "DELETE", "hash=%s reason=too-new" % torrent_hash[:8])
                    return
                else:
                    log.info(
                        "Torrent age check: PASSED (age=%.1f mins >= minimum=%d mins).",
                        torrent_age_minutes,
                        self.cfg.min_torrent_age_minutes,
                    )
            else:
                log.warning("Torrent age check: creation_date not available, skipping age validation.")

        try:
            trackers = self.qbit.trackers(torrent_hash) or []
        except Exception as e:
            outcome = "FAILED"
            outcome_details = "hash=%s stage=trackers error=%s" % (torrent_hash[:8], short_error(e))
            log_stage_result("Guard Outcome", outcome, outcome_details)
            raise RuntimeError("qB tracker lookup failed: %s" % short_error(e)) from e
        tracker_hosts = {domain_from_url(tracker.get("url", "")) for tracker in trackers if tracker.get("url")}

        preair_applied = False
        tv_should_apply = self.preair.should_apply(category_norm)
        movie_should_apply = self.preair_movie.should_apply(category_norm)

        if tv_should_apply and movie_should_apply:
            log.warning(
                "Category '%s' matches both Sonarr (%s) and Radarr (%s) pre-air categories. This may lead to unexpected behavior. Consider using distinct categories.",
                category,
                sorted(self.cfg.sonarr_categories),
                sorted(self.cfg.radarr_preair_categories),
            )

        if tv_should_apply:
            preair_applied = True
            allow, reason, _history_rows = self.preair.decision(self.qbit, torrent_hash, tracker_hosts)
            if not allow:
                log_stage_result("Pre-air TV", "BLOCK", "reason=%s" % reason)
                if not self.cfg.dry_run:
                    try:
                        self.sonarr.blocklist_download(torrent_hash)
                    except Exception as e:
                        log.error("Sonarr blocklist error: %s", e)
                    self.qbit.add_tags(torrent_hash, "trash:preair")
                    try:
                        self.qbit.delete(torrent_hash, self.cfg.delete_files)
                        log.info("Pre-air TV: deleted torrent %s (reason=%s).", torrent_hash, reason)
                    except Exception as e:
                        log.error("qB delete failed: %s", e)
                else:
                    log.info("DRY-RUN: would delete torrent %s due to TV pre-air (reason=%s).", torrent_hash, reason)
                log_stage_result("Guard Outcome", "DELETE", "hash=%s reason=preair-tv:%s" % (torrent_hash[:8], reason))
                return
            log_stage_result("Pre-air TV", "PASS", "reason=%s" % reason)

        if movie_should_apply:
            preair_applied = True
            allow, reason, _history_rows = self.preair_movie.decision(self.qbit, torrent_hash, tracker_hosts)
            if not allow:
                log_stage_result("Pre-air Movie", "BLOCK", "reason=%s" % reason)
                if not self.cfg.dry_run:
                    try:
                        self.radarr.blocklist_download(torrent_hash)
                    except Exception as e:
                        log.error("Radarr blocklist error: %s", e)
                    self.qbit.add_tags(torrent_hash, "trash:preair-movie")
                    try:
                        self.qbit.delete(torrent_hash, self.cfg.delete_files)
                        log.info("Pre-air Movie: deleted torrent %s (reason=%s).", torrent_hash, reason)
                    except Exception as e:
                        log.error("qB delete failed: %s", e)
                else:
                    log.info("DRY-RUN: would delete torrent %s due to movie pre-air (reason=%s).", torrent_hash, reason)
                log_stage_result("Guard Outcome", "DELETE", "hash=%s reason=preair-movie:%s" % (torrent_hash[:8], reason))
                return
            log_stage_result("Pre-air Movie", "PASS", "reason=%s" % reason)

        if not preair_applied:
            log_stage_result("Pre-air", "SKIP", "category=%s" % category)

        if self.cfg.enable_iso_check:
            try:
                files = self.metadata.fetch(torrent_hash)
            except Exception as e:
                outcome = "FAILED"
                outcome_details = "hash=%s stage=metadata error=%s" % (torrent_hash[:8], short_error(e))
                log_stage_result("Guard Outcome", outcome, outcome_details)
                raise RuntimeError("metadata fetch failed: %s" % short_error(e)) from e
            if not files:
                self.qbit.add_tags(torrent_hash, "guard:metadata-pending")
                log.warning("Metadata not available; keeping torrent stopped for retry.")
                log_stage_result("File/ISO/Ext Check", "SKIP", "reason=metadata-unavailable")
                log_stage_result("Guard Outcome", "RETRY", "hash=%s reason=metadata-unavailable" % torrent_hash[:8])
                raise RuntimeError("metadata unavailable; torrent kept stopped for retry")
            if self.iso.evaluate_and_act(torrent_hash, category_norm):
                log_stage_result("Guard Outcome", "DELETE", "hash=%s reason=file-check-delete" % torrent_hash[:8])
                return
        else:
            log_stage_result("File/ISO/Ext Check", "SKIP", "reason=disabled")

        self.qbit.remove_tags(torrent_hash, "guard:metadata-pending")
        self.qbit.add_tags(torrent_hash, "guard:allowed")
        if not self.cfg.dry_run and not self.qbit.start(torrent_hash):
            outcome = "FAILED"
            outcome_details = "hash=%s stage=final-start error=qB failed to start torrent after checks" % torrent_hash[:8]
            log_stage_result("Guard Outcome", outcome, outcome_details)
            raise RuntimeError("qB failed to start torrent after checks")
        log_stage_result("Final Start", "PASS", "hash=%s name=%s" % (torrent_hash[:8], name[:60]))
        log_stage_result("Guard Outcome", "ALLOW", "hash=%s name=%s" % (torrent_hash[:8], name[:60]))


def main(argv: List[str]) -> None:
    if len(argv) < 2:
        print("Usage: qbit-guard.py <INFO_HASH> [<CATEGORY>]")
        log.critical("Fatal: Missing required torrent hash argument")
        log.critical("Terminating guard process (exit code 1)")
        sys.exit(1)
    torrent_hash = argv[1].strip()
    passed_category = (argv[2] if len(argv) >= 3 else "").strip()

    cfg = Config.from_env()
    guard = TorrentGuard(cfg)
    try:
        guard.run(torrent_hash, passed_category)
    except Exception as e:
        log.critical("Fatal: Unhandled error occurred - %s", e)
        log.critical("Terminating guard process (exit code 1)")
        sys.exit(1)


def cli() -> None:
    main(sys.argv)


__all__ = [
    "Config",
    "HttpClient",
    "QbitClient",
    "SonarrClient",
    "RadarrClient",
    "MetadataFetcher",
    "IsoCleaner",
    "InternetDates",
    "PreAirGate",
    "PreAirMovieGate",
    "TorrentGuard",
    "main",
    "cli",
]


if __name__ == "__main__":
    main(sys.argv)
