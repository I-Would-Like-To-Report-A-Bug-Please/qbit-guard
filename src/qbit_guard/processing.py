from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Sequence

from .clients import QbitClient, RadarrClient, SonarrClient
from .config import Config
from .extensions import _generate_detailed_extension_summary
from .logging_setup import get_logger
from .runtime import compute_backoff_delay, is_connection_error, log_stage_result, short_error


log = get_logger("qbit-guard")


class MetadataFetcher:
    def __init__(self, cfg: Config, qbit: QbitClient):
        self.cfg = cfg
        self.qbit = qbit

    def _safe_stop(self, torrent_hash: str, context: str) -> None:
        stop_attempts = max(1, self.cfg.qbit_request_retries)
        for attempt in range(stop_attempts):
            if self.qbit.stop(torrent_hash):
                if attempt > 0:
                    log.info("Metadata fetch: stop recovered for torrent %s after %d retry attempt(s).", torrent_hash[:8], attempt)
                return
            if attempt == stop_attempts - 1:
                break
            delay = compute_backoff_delay(
                attempt,
                self.cfg.qbit_request_initial_backoff_sec,
                self.cfg.qbit_request_max_backoff_sec,
            )
            log.warning(
                "Metadata fetch: failed to stop torrent %s after %s; retrying stop in %.1f seconds (attempt %d/%d)",
                torrent_hash[:8],
                context,
                delay,
                attempt + 1,
                stop_attempts,
            )
            time.sleep(delay)
        log.error("Metadata fetch: unable to confirm stop for torrent %s after %s.", torrent_hash[:8], context)

    def fetch(self, torrent_hash: str) -> List[Dict[str, Any]]:
        files = []
        try:
            files = self.qbit.files(torrent_hash) or []
        except Exception as e:
            log.warning(
                "Metadata fetch: initial file probe failed for torrent %s: %s. Continuing with transient error budget.",
                torrent_hash[:8],
                short_error(e),
            )
        if files:
            return files

        if not self.qbit.start(torrent_hash):
            raise RuntimeError("qB failed to start torrent for metadata fetch")
        start_ts = time.time()
        ticks = 0
        base_downloaded = None
        consecutive_errors = 0
        last_error = None

        try:
            while True:
                try:
                    if ticks % max(1, int(15.0 / max(self.cfg.metadata_poll_interval, 0.5))) == 0:
                        self.qbit.reannounce(torrent_hash)

                    files = self.qbit.files(torrent_hash) or []
                    if files:
                        break

                    info = self.qbit.info(torrent_hash) or {}
                    if info:
                        state = (info.get("state") or "").lower()
                        if state in ("pauseddl", "pausedup", "stalleddl"):
                            if not self.qbit.start(torrent_hash):
                                log.warning("Metadata fetch: failed to resume paused torrent %s while waiting for files.", torrent_hash[:8])
                        cur_downloaded = int(info.get("downloaded_session") or info.get("downloaded") or 0)
                        if base_downloaded is None:
                            base_downloaded = cur_downloaded
                        delta = cur_downloaded - base_downloaded
                        if self.cfg.metadata_download_budget_bytes > 0 and delta > self.cfg.metadata_download_budget_bytes:
                            log.warning("Metadata wait exceeded budget (%s > %s); aborting wait.", delta, self.cfg.metadata_download_budget_bytes)
                            files = []
                            break

                    consecutive_errors = 0
                except Exception as e:
                    last_error = e
                    consecutive_errors += 1
                    if consecutive_errors > self.cfg.metadata_max_transient_errors or not is_connection_error(e):
                        raise RuntimeError(
                            "metadata fetch qB failure after %d transient error(s): %s"
                            % (consecutive_errors, short_error(e))
                        ) from e
                    log.warning(
                        "Metadata fetch transient qB error for torrent %s (attempt %d/%d): %s",
                        torrent_hash[:8],
                        consecutive_errors,
                        self.cfg.metadata_max_transient_errors,
                        short_error(e),
                    )

                if self.cfg.metadata_max_wait_sec > 0 and (time.time() - start_ts) >= self.cfg.metadata_max_wait_sec:
                    log_stage_result("Metadata Fetch", "TIMEOUT", "hash=%s wait_sec=%d" % (torrent_hash[:8], self.cfg.metadata_max_wait_sec))
                    break

                time.sleep(self.cfg.metadata_poll_interval)
                ticks += 1
        finally:
            stop_context = "metadata resolution"
            if last_error is not None:
                stop_context = "metadata error (%s)" % short_error(last_error, 80)
            self._safe_stop(torrent_hash, stop_context)

        if files:
            log_stage_result("Metadata Fetch", "PASS", "hash=%s files=%d" % (torrent_hash[:8], len(files)))
        else:
            log_stage_result("Metadata Fetch", "EMPTY", "hash=%s" % torrent_hash[:8])
        return files or []


class IsoCleaner:
    VIDEO_RE = re.compile(r"\.(mkv|mp4|m4v|avi|ts|m2ts|mov|webm)$", re.I)

    def __init__(self, cfg: Config, qbit: QbitClient, sonarr: SonarrClient, radarr: RadarrClient):
        self.cfg = cfg
        self.qbit = qbit
        self.sonarr = sonarr
        self.radarr = radarr
        self.min_bytes = int(cfg.min_keepable_video_mb * 1024 * 1024)
        disc_pat = r"\.(" + "|".join(sorted(map(re.escape, self.cfg.disc_exts))) + r")$"
        self.disc_re = re.compile(disc_pat, re.I)

    def _is_disc_path(self, name: str) -> bool:
        normalized = (name or "").replace("\\", "/").lower()
        return bool(self.disc_re.search(normalized) or "/bdmv/" in normalized or "/video_ts/" in normalized)

    def has_keepable_video(self, files: Sequence[Dict[str, Any]]) -> bool:
        for file_info in files:
            name = file_info.get("name", "")
            size = int(file_info.get("size", 0))
            if self.VIDEO_RE.search(name) and size >= self.min_bytes and self.cfg.is_path_allowed(name):
                return True
        return False

    def _blocklist_arr_if_applicable(self, category_norm: str, torrent_hash: str) -> None:
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

    def evaluate_and_act(self, torrent_hash: str, category_norm: str) -> bool:
        all_files = self.qbit.files(torrent_hash) or []
        relevant = [file_info for file_info in all_files if int(file_info.get("size", 0)) > 0]
        disallowed = [file_info for file_info in relevant if not self.cfg.is_path_allowed(file_info.get("name", ""))]

        if disallowed:
            total = len(relevant)
            bad = len(disallowed)
            allowed = [file_info for file_info in relevant if self.cfg.is_path_allowed(file_info.get("name", ""))]
            good = len(allowed)
            sample = disallowed[0].get("name", "") if disallowed else ""
            log.info("Ext policy: %d/%d file(s) disallowed. e.g., %s", bad, total, sample)
            if self.cfg.detailed_logging:
                log.detailed("Extension policy details: %s", _generate_detailed_extension_summary(disallowed))

            should_delete = self.cfg.ext_delete_if_any_blocked or (self.cfg.ext_delete_if_all_blocked and bad == total)
            if should_delete:
                self.qbit.add_tags(torrent_hash, self.cfg.ext_violation_tag)
                self._blocklist_arr_if_applicable(category_norm, torrent_hash)
                if not self.cfg.dry_run:
                    try:
                        self.qbit.delete(torrent_hash, self.cfg.delete_files)
                        log.info("Removed torrent %s due to extension policy.", torrent_hash)
                        log_stage_result("File/ISO/Ext Check", "DELETE", "reason=extension-policy disallowed=%d/%d" % (bad, total))
                    except Exception as e:
                        log.error("Failed to delete torrent %s from qBittorrent: %s", torrent_hash, e)
                else:
                    log.info("DRY-RUN: would remove torrent %s due to extension policy.", torrent_hash)
                    log_stage_result("File/ISO/Ext Check", "DELETE", "reason=extension-policy dry-run disallowed=%d/%d" % (bad, total))
                return True
            elif self.cfg.uncheck_blocked_files and good > 0:
                disallowed_ids = []
                for index, file_info in enumerate(all_files):
                    if not self.cfg.is_path_allowed(file_info.get("name", "")) and int(file_info.get("size", 0)) > 0:
                        disallowed_ids.append(index)
                if disallowed_ids:
                    log.info("Unchecking %d disallowed file(s), keeping %d allowed file(s)", bad, good)
                    if not self.cfg.dry_run:
                        try:
                            self.qbit.set_file_priority(torrent_hash, disallowed_ids, 0)
                            self.qbit.add_tags(torrent_hash, "guard:partial")
                            log.info("Unchecked %d file(s) from torrent %s due to extension policy.", len(disallowed_ids), torrent_hash)
                            log_stage_result("File/ISO/Ext Check", "PARTIAL", "unchecked=%d kept=%d" % (len(disallowed_ids), good))
                        except Exception as e:
                            log.error("Failed to uncheck files: %s", e)
                    else:
                        log.info("DRY-RUN: would uncheck %d file(s) from torrent %s due to extension policy.", len(disallowed_ids), torrent_hash)
                        log_stage_result("File/ISO/Ext Check", "PARTIAL", "dry-run unchecked=%d kept=%d" % (len(disallowed_ids), good))

        all_discish = (len(relevant) > 0) and all(self._is_disc_path(file_info.get("name", "")) for file_info in relevant)
        keepable = self.has_keepable_video(relevant)
        if all_discish and not keepable:
            log.info("ISO cleaner: disc-image content detected (no keepable video).")
            self.qbit.add_tags(torrent_hash, "trash:iso")
            self._blocklist_arr_if_applicable(category_norm, torrent_hash)
            if not self.cfg.dry_run:
                try:
                    self.qbit.delete(torrent_hash, self.cfg.delete_files)
                    log.info("Removed torrent %s (ISO/BDMV-only).", torrent_hash)
                    log_stage_result("File/ISO/Ext Check", "DELETE", "reason=iso-only")
                except Exception as e:
                    log.error("qB delete failed: %s", e)
            else:
                log.info("DRY-RUN: would remove torrent %s (ISO/BDMV-only).", torrent_hash)
                log_stage_result("File/ISO/Ext Check", "DELETE", "reason=iso-only dry-run")
            return True

        log.info(
            "ISO/Ext check: keepable=%s, files=%d (disallowed=%d, action=%s).",
            keepable,
            len(relevant),
            len(disallowed),
            "partial" if (disallowed and self.cfg.uncheck_blocked_files and len([f for f in relevant if self.cfg.is_path_allowed(f.get('name', ''))]) > 0) else "passed",
        )
        log_stage_result("File/ISO/Ext Check", "PASS", "files=%d disallowed=%d keepable=%s" % (len(relevant), len(disallowed), keepable))
        return False
