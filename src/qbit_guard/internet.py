from __future__ import annotations

import datetime
import json
import urllib.parse as uparse
from typing import Any, Dict, Optional

from .clients import HttpClient, RadarrClient, SonarrClient
from .config import Config
from .logging_setup import get_logger
from .runtime import parse_iso_utc


log = get_logger("qbit-guard")


class InternetDates:
    def __init__(self, cfg: Config, http: HttpClient, sonarr: SonarrClient, radarr: RadarrClient):
        self.cfg = cfg
        self.http = http
        self.sonarr = sonarr
        self.radarr = radarr
        self._tvdb_token = cfg.tvdb_bearer.strip()

    def _get(self, url: str, timeout: int) -> Any:
        raw = self.http.get(url, timeout=timeout)
        return None if not raw else json.loads(raw.decode("utf-8"))

    def tvmaze_show_id(self, series: Dict[str, Any]) -> Optional[int]:
        tvdb = series.get("tvdbId") or None
        imdb = series.get("imdbId") or None
        title = series.get("title") or None
        try:
            if tvdb:
                data = self._get(f"{self.cfg.tvmaze_base}/lookup/shows?thetvdb={int(tvdb)}", self.cfg.tvmaze_timeout)
                if isinstance(data, dict) and data.get("id"):
                    return int(data["id"])
            if imdb and not str(imdb).startswith("tt"):
                imdb = "tt" + str(imdb)
            if imdb:
                data = self._get(f"{self.cfg.tvmaze_base}/lookup/shows?imdb={uparse.quote(str(imdb))}", self.cfg.tvmaze_timeout)
                if isinstance(data, dict) and data.get("id"):
                    return int(data["id"])
            if title:
                data = self._get(f"{self.cfg.tvmaze_base}/singlesearch/shows?q={uparse.quote(title)}", self.cfg.tvmaze_timeout)
                if isinstance(data, dict) and data.get("id"):
                    return int(data["id"])
        except Exception:
            return None
        return None

    def tvmaze_episode_airstamp(self, tm_id: int, season: int, number: int) -> Optional[datetime.datetime]:
        try:
            data = self._get(
                f"{self.cfg.tvmaze_base}/shows/{tm_id}/episodebynumber?season={season}&number={number}",
                self.cfg.tvmaze_timeout,
            )
            stamp = data.get("airstamp") if isinstance(data, dict) else None
            return parse_iso_utc(stamp) if stamp else None
        except Exception:
            return None

    def _tvdb_login(self) -> Optional[str]:
        if self._tvdb_token:
            return self._tvdb_token
        if not self.cfg.tvdb_apikey:
            return None
        body = {"apikey": self.cfg.tvdb_apikey}
        if self.cfg.tvdb_pin:
            body["pin"] = self.cfg.tvdb_pin
        try:
            raw = self.http.post_json(f"{self.cfg.tvdb_base}/login", obj=body, timeout=self.cfg.tvdb_timeout)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            token = data.get("data", {}).get("token") or data.get("token")
            if token:
                self._tvdb_token = token
                return token
        except Exception:
            return None
        return None

    def tvdb_episode_airstamp(self, tvdb_series_id: int, season: int, number: int) -> Optional[datetime.datetime]:
        token = self._tvdb_login()
        if not token:
            return None
        order = self.cfg.tvdb_order if self.cfg.tvdb_order in ("default", "official") else "default"
        lang = self.cfg.tvdb_language or "eng"
        try:
            for page in range(0, 10):
                url = f"{self.cfg.tvdb_base}/series/{tvdb_series_id}/episodes/{order}/{lang}?page={page}"
                raw = self.http.get(url, headers={"Authorization": "Bearer " + token}, timeout=self.cfg.tvdb_timeout)
                data = json.loads(raw.decode("utf-8")) if raw else {}
                for episode in (data.get("data") or []):
                    if episode.get("seasonNumber") == season and episode.get("number") == number:
                        stamp = episode.get("airstamp") or episode.get("firstAired") or episode.get("airDate") or episode.get("date")
                        if not stamp:
                            return None
                        if isinstance(stamp, str) and stamp.endswith("Z"):
                            stamp = stamp[:-1] + "+00:00"
                        if isinstance(stamp, str) and len(stamp) == 10 and stamp[4] == "-" and stamp[7] == "-":
                            stamp += "T00:00:00+00:00"
                        try:
                            return datetime.datetime.fromisoformat(stamp)
                        except Exception:
                            return None
                if not data.get("data"):
                    break
        except Exception:
            return None
        return None

    def tvdb_movie_release_date(self, movie: Dict[str, Any]) -> Optional[datetime.datetime]:
        tvdb_id = movie.get("tvdbId") or None
        imdb = movie.get("imdbId") or None
        if not tvdb_id and not imdb:
            return None
        token = self._tvdb_login()
        if not token:
            return None
        try:
            if tvdb_id:
                url = f"{self.cfg.tvdb_base}/movies/{tvdb_id}"
                raw = self.http.get(url, headers={"Authorization": "Bearer " + token}, timeout=self.cfg.tvdb_timeout)
                data = json.loads(raw.decode("utf-8")) if raw else {}
                value = data.get("data", {}).get("releaseDate") or data.get("data", {}).get("year")
                if value:
                    if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
                        value += "T00:00:00+00:00"
                    try:
                        return datetime.datetime.fromisoformat(value)
                    except Exception:
                        return None

            if imdb:
                imdb_id = imdb if str(imdb).startswith("tt") else "tt" + str(imdb)
                url = f"{self.cfg.tvdb_base}/search?imdbId={imdb_id}"
                raw = self.http.get(url, headers={"Authorization": "Bearer " + token}, timeout=self.cfg.tvdb_timeout)
                data = json.loads(raw.decode("utf-8")) if raw else {}
                for result in data.get("data", []):
                    if result.get("type") == "movie":
                        value = result.get("releaseDate") or result.get("year")
                        if value:
                            if isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
                                value += "T00:00:00+00:00"
                            try:
                                return datetime.datetime.fromisoformat(value)
                            except Exception:
                                return None
        except Exception as e:
            log.warning("TVDB: Failed to retrieve release dates for movie %s: %s", tvdb_id, e)
        return None

    def tmdb_movie_release_dates(self, movie: Dict[str, Any]) -> Dict[str, datetime.datetime]:
        result = {"digital": None, "physical": None, "theatrical": None}
        if not self.cfg.tmdb_apikey:
            return result
        tmdb_id = movie.get("tmdbId") or None
        if not tmdb_id:
            return result
        try:
            url = f"{self.cfg.tmdb_base}/movie/{int(tmdb_id)}?api_key={self.cfg.tmdb_apikey}&append_to_response=release_dates"
            raw = self.http.get(url, timeout=self.cfg.tmdb_timeout)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            release_dates = data.get("release_dates", {}).get("results", [])
            for country_data in release_dates:
                for release in country_data.get("release_dates", []):
                    date_str = release.get("release_date")
                    if not date_str:
                        continue
                    parsed_date = parse_iso_utc(date_str)
                    if not parsed_date:
                        continue
                    release_type = release.get("type")
                    if release_type == 4 and (result["digital"] is None or parsed_date < result["digital"]):
                        result["digital"] = parsed_date
                    elif release_type == 5 and (result["physical"] is None or parsed_date < result["physical"]):
                        result["physical"] = parsed_date
                    elif release_type in (1, 2, 3) and (result["theatrical"] is None or parsed_date < result["theatrical"]):
                        result["theatrical"] = parsed_date
            for key, value in result.items():
                if value:
                    log.debug("TMDB: Found %s release date for movie %s: %s", key, tmdb_id, value)
        except Exception as e:
            log.warning("TMDB: Failed to retrieve release dates for movie %s: %s", tmdb_id, e)
        return result
