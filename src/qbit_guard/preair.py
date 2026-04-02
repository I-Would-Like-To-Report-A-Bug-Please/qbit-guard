from __future__ import annotations

import time
from typing import Dict, List, Set, Tuple

from .clients import QbitClient, RadarrClient, SonarrClient
from .config import Config
from .internet import InternetDates
from .logging_setup import get_logger
from .runtime import hours_until, now_utc, parse_iso_utc


log = get_logger("qbit-guard")


class PreAirGate:
    def __init__(self, cfg: Config, sonarr: SonarrClient, internet: InternetDates):
        self.cfg = cfg
        self.sonarr = sonarr
        self.internet = internet

    def should_apply(self, category_norm: str) -> bool:
        return self.cfg.enable_preair and self.sonarr.enabled and (category_norm in self.cfg.sonarr_categories)

    def decision(self, qbit: QbitClient, torrent_hash: str, tracker_hosts: Set[str]) -> Tuple[bool, str, List[Dict]]:
        time.sleep(0.8)
        history = []
        for _ in range(5):
            history = self.sonarr.history_for_download(torrent_hash)
            if history:
                break
            time.sleep(0.8)

        episodes = {int(row["episodeId"]) for row in history if row.get("episodeId")}
        release_groups, indexers = set(), set()
        for row in history:
            data = row.get("data") or {}
            if data.get("releaseGroup"):
                release_groups.add(str(data["releaseGroup"]).lower())
            if data.get("indexer"):
                indexers.add(str(data["indexer"]).lower())

        if not episodes:
            message = "No Sonarr history."
            if self.cfg.resume_if_no_history:
                log.debug("Pre-air: %s Proceeding to file check.", message)
                return True, "no-history", history
            log.debug("Pre-air: %s Keeping stopped.", message)
            return False, "no-history", history

        future_hours: List[float] = []
        series_cache: Dict[int, Dict] = {}
        for episode_id in episodes:
            episode = self.sonarr.episode(episode_id) or {}
            air_date = parse_iso_utc(episode.get("airDateUtc"))
            if air_date and air_date > now_utc():
                future_hours.append(hours_until(air_date))
            elif air_date is None:
                future_hours.append(99999.0)

        all_aired = len(future_hours) == 0
        max_future = max(future_hours) if future_hours else 0.0

        if not all_aired and self.cfg.internet_check_provider in ("tvmaze", "both"):
            internet_future = []
            for episode_id in episodes:
                episode = self.sonarr.episode(episode_id) or {}
                series_id = episode.get("seriesId")
                if not series_id:
                    internet_future.append(99999.0)
                    continue
                if series_id not in series_cache:
                    series_cache[series_id] = self.sonarr.series(series_id) or {}
                series = series_cache[series_id]
                show_id = self.internet.tvmaze_show_id(series)
                season = episode.get("seasonNumber")
                number = episode.get("episodeNumber")
                if show_id and season is not None and number is not None:
                    date = self.internet.tvmaze_episode_airstamp(show_id, int(season), int(number))
                    if date and date > now_utc():
                        internet_future.append(hours_until(date))
                    elif date is None:
                        internet_future.append(99999.0)
            if internet_future:
                maximum = max(internet_future)
                max_future = min(max_future, maximum) if max_future else maximum
                all_aired = False

        if not all_aired and self.cfg.internet_check_provider in ("tvdb", "both"):
            internet_future = []
            for episode_id in episodes:
                episode = self.sonarr.episode(episode_id) or {}
                series_id = episode.get("seriesId")
                if not series_id:
                    internet_future.append(99999.0)
                    continue
                if series_id not in series_cache:
                    series_cache[series_id] = self.sonarr.series(series_id) or {}
                series = series_cache[series_id]
                tvdb_series_id = series.get("tvdbId")
                season = episode.get("seasonNumber")
                number = episode.get("episodeNumber")
                if tvdb_series_id and season is not None and number is not None:
                    date = self.internet.tvdb_episode_airstamp(int(tvdb_series_id), int(season), int(number))
                    if date and date > now_utc():
                        internet_future.append(hours_until(date))
                    elif date is None:
                        internet_future.append(99999.0)
            if internet_future:
                maximum = max(internet_future)
                max_future = min(max_future, maximum) if max_future else maximum
                all_aired = False

        allow_by_grace = (not all_aired) and (max_future <= self.cfg.early_grace_hours)
        allow_by_group = bool(self.cfg.whitelist_groups and (release_groups & self.cfg.whitelist_groups))
        allow_by_indexer = bool(self.cfg.whitelist_indexers and (indexers & self.cfg.whitelist_indexers))
        allow_by_tracker = bool(
            self.cfg.whitelist_trackers and any(any(whitelist in host for whitelist in self.cfg.whitelist_trackers) for host in tracker_hosts)
        )
        whitelist_allowed = allow_by_group or allow_by_indexer or allow_by_tracker

        if (not all_aired) and (max_future > self.cfg.early_hard_limit_hours) and (
            not (self.cfg.whitelist_overrides_hard_limit and whitelist_allowed)
        ):
            log.debug("Pre-air: BLOCK_CAP max_future=%.2f h", max_future)
            return False, "cap", history

        if all_aired or allow_by_grace or whitelist_allowed:
            reason = "+".join(
                [name for name, ok in [("aired", all_aired), ("grace", allow_by_grace), ("whitelist", whitelist_allowed)] if ok]
            ) or "allow"
            log.debug("Pre-air: ALLOW (%s)", reason)
            return True, reason, history

        log.debug("Pre-air: BLOCK (max_future=%.2f h)", max_future)
        return False, "block", history


class PreAirMovieGate:
    def __init__(self, cfg: Config, radarr: RadarrClient, internet: InternetDates):
        self.cfg = cfg
        self.radarr = radarr
        self.internet = internet

    def should_apply(self, category_norm: str) -> bool:
        return self.cfg.enable_preair and self.radarr.enabled and (category_norm in self.cfg.radarr_preair_categories)

    def decision(self, qbit: QbitClient, torrent_hash: str, tracker_hosts: Set[str]) -> Tuple[bool, str, List[Dict]]:
        time.sleep(0.8)
        history = []
        for _ in range(5):
            history = self.radarr.history_for_download(torrent_hash)
            if history:
                break
            time.sleep(0.8)

        movies = {int(row["movieId"]) for row in history if row.get("movieId")}
        release_groups, indexers = set(), set()
        for row in history:
            data = row.get("data") or {}
            if data.get("releaseGroup"):
                release_groups.add(str(data["releaseGroup"]).lower())
            if data.get("indexer"):
                indexers.add(str(data["indexer"]).lower())

        if not movies:
            message = "No Radarr history."
            if self.cfg.resume_if_no_history:
                log.debug("Pre-air Movie: %s Proceeding to file check.", message)
                return True, "no-history", history
            log.debug("Pre-air Movie: %s Keeping stopped.", message)
            return False, "no-history", history

        future_hours: List[float] = []
        movie_cache: Dict[int, Dict] = {}
        for movie_id in movies:
            movie = self.radarr.movie(movie_id) or {}
            movie_cache[movie_id] = movie
            radarr_date = None
            tmdb_release_dates = self.internet.tmdb_movie_release_dates(movie)
            self.radarr.ensure_minimum_availability_released(movie_id)

            for field in ["digitalRelease", "physicalRelease", "inCinemas", "releaseDate"]:
                date_str = movie.get(field)
                if date_str:
                    radarr_date = parse_iso_utc(date_str)
                    log.info("Movie %s: Found Radarr release date from field %s: %s", movie_id, field, radarr_date)
                    break

            release_date = tmdb_release_dates["digital"] or tmdb_release_dates["physical"]
            if release_date is not None:
                log.info("Movie %s: Using TMDB digital/physical release date: %s", movie_id, release_date)
            elif tmdb_release_dates["theatrical"] is not None:
                release_date = tmdb_release_dates["theatrical"]
                log.info("Movie %s: Using TMDB theatrical release date: %s", movie_id, release_date)
            elif radarr_date is not None:
                release_date = radarr_date
                log.info("Movie %s: Falling back to Radarr release date: %s", movie_id, release_date)

            if release_date and release_date > now_utc():
                future_hours.append(hours_until(release_date))
            elif release_date is None:
                future_hours.append(99999.0)

        all_released = len(future_hours) == 0
        max_future = max(future_hours) if future_hours else 0.0

        if not all_released and self.cfg.internet_check_provider in ("tvdb", "both"):
            internet_future = []
            for movie_id in movies:
                release_date = self.internet.tvdb_movie_release_date(movie_cache[movie_id])
                if release_date and release_date > now_utc():
                    internet_future.append(hours_until(release_date))
                elif release_date is None:
                    internet_future.append(99999.0)
            if internet_future:
                maximum = max(internet_future)
                max_future = min(max_future, maximum) if max_future else maximum
                all_released = False

        allow_by_grace = (not all_released) and (max_future <= self.cfg.early_grace_hours)
        allow_by_group = bool(self.cfg.whitelist_groups and (release_groups & self.cfg.whitelist_groups))
        allow_by_indexer = bool(self.cfg.whitelist_indexers and (indexers & self.cfg.whitelist_indexers))
        allow_by_tracker = bool(
            self.cfg.whitelist_trackers and any(any(whitelist in host for whitelist in self.cfg.whitelist_trackers) for host in tracker_hosts)
        )
        whitelist_allowed = allow_by_group or allow_by_indexer or allow_by_tracker

        if (not all_released) and (max_future > self.cfg.early_hard_limit_hours) and (
            not (self.cfg.whitelist_overrides_hard_limit and whitelist_allowed)
        ):
            log.debug("Pre-air Movie: BLOCK_CAP max_future=%.2f h", max_future)
            return False, "cap", history

        if all_released or allow_by_grace or whitelist_allowed:
            reason = "+".join(
                [name for name, ok in [("released", all_released), ("grace", allow_by_grace), ("whitelist", whitelist_allowed)] if ok]
            ) or "allow"
            log.debug("Pre-air Movie: ALLOW (%s)", reason)
            return True, reason, history

        log.debug("Pre-air Movie: BLOCK (max_future=%.2f h)", max_future)
        return False, "block", history
