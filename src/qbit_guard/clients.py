from __future__ import annotations

import json
import math
import ssl
import time
import http.cookiejar as cookiejar
import urllib.parse as uparse
import urllib.request as ureq
from typing import Any, Dict, List, Optional, Sequence

from .config import Config
from .logging_setup import get_logger
from .runtime import compute_backoff_delay, is_connection_error, short_error, warn_after_attempt


log = get_logger("qbit-guard")


class HttpClient:
    def __init__(self, ignore_tls: bool, user_agent: str):
        self.cj = cookiejar.CookieJar()
        if ignore_tls:
            ctx = ssl._create_unverified_context()
            self.opener = ureq.build_opener(
                ureq.HTTPCookieProcessor(self.cj),
                ureq.HTTPSHandler(context=ctx),
                ureq.HTTPHandler(),
            )
        else:
            self.opener = ureq.build_opener(ureq.HTTPCookieProcessor(self.cj))
        self.user_agent = user_agent

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> bytes:
        request_headers = {"User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)
        req = ureq.Request(url, headers=request_headers)
        with self.opener.open(req, timeout=timeout) as response:
            return response.read()

    def post_bytes(self, url: str, payload: bytes, headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> bytes:
        request_headers = {"User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)
        req = ureq.Request(url, data=payload, headers=request_headers)
        with self.opener.open(req, timeout=timeout) as response:
            return response.read()

    def post_form(self, url: str, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> bytes:
        return self.post_bytes(url, uparse.urlencode(data or {}).encode(), headers, timeout)

    def post_json(self, url: str, obj: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> bytes:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        return self.post_bytes(url, json.dumps(obj or {}).encode(), request_headers, timeout)

    def delete(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> bytes:
        request_headers = {"User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)
        req = ureq.Request(url, headers=request_headers, method="DELETE")
        with self.opener.open(req, timeout=timeout) as response:
            return response.read()


class QbitClient:
    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg = cfg
        self.http = http

    def _url(self, path: str) -> str:
        return f"{self.cfg.qbit_host}{path}"

    def _retry(self, operation: str, fn):
        attempts = max(1, self.cfg.qbit_request_retries)
        last = None
        retries_used = 0
        warn_after = warn_after_attempt(self.cfg.qbit_request_warn_after_attempt, attempts)
        for attempt in range(attempts):
            try:
                result = fn()
                if retries_used >= warn_after:
                    log.info("qB %s recovered after %d retr%s", operation, retries_used, "y" if retries_used == 1 else "ies")
                return result
            except Exception as e:
                last = e
                retries_used = attempt + 1
                if not is_connection_error(e) or attempt == attempts - 1:
                    raise
                delay = compute_backoff_delay(
                    attempt,
                    self.cfg.qbit_request_initial_backoff_sec,
                    self.cfg.qbit_request_max_backoff_sec,
                )
                if retries_used >= warn_after:
                    log.warning(
                        "qB %s failed (attempt %d/%d): %s; retrying in %.1f seconds",
                        operation,
                        attempt + 1,
                        attempts,
                        short_error(e),
                        delay,
                    )
                else:
                    log.debug(
                        "qB %s transient failure (attempt %d/%d): %s; retrying in %.1f seconds",
                        operation,
                        attempt + 1,
                        attempts,
                        short_error(e),
                        delay,
                    )
                time.sleep(delay)
        raise last

    def login(self) -> None:
        log.info("Attempting qBittorrent login at %s", self.cfg.qbit_host)
        self._retry(
            "login",
            lambda: self.http.post_form(
                self._url("/api/v2/auth/login"),
                {"username": self.cfg.qbit_user, "password": self.cfg.qbit_pass},
            ),
        )
        log.info("Successfully authenticated with qBittorrent")

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self._url(path)
        if params:
            url += "?" + uparse.urlencode(params, doseq=True)
        raw = self._retry(f"GET {path}", lambda: self.http.get(url))
        return None if not raw else json.loads(raw.decode("utf-8"))

    def post(self, path: str, data: Optional[Dict[str, Any]] = None) -> None:
        self._retry(f"POST {path}", lambda: self.http.post_form(self._url(path), data or {}))

    def start(self, torrent_hash: str) -> bool:
        last_error = None
        for path in ("/api/v2/torrents/start", "/api/v2/torrents/resume"):
            try:
                self.post(path, {"hashes": torrent_hash})
                return True
            except Exception as e:
                last_error = e
        if last_error:
            log.warning("qB: could not start/resume %s: %s", torrent_hash, short_error(last_error))
        else:
            log.warning("qB: could not start/resume %s", torrent_hash)
        return False

    def stop(self, torrent_hash: str) -> bool:
        last_error = None
        for path in ("/api/v2/torrents/stop", "/api/v2/torrents/pause"):
            try:
                self.post(path, {"hashes": torrent_hash})
                return True
            except Exception as e:
                last_error = e
        if last_error:
            log.warning("qB: could not stop/pause %s: %s", torrent_hash, short_error(last_error))
        else:
            log.warning("qB: could not stop/pause %s", torrent_hash)
        return False

    def delete(self, torrent_hash: str, delete_files: bool) -> None:
        self.post("/api/v2/torrents/delete", {"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"})

    def reannounce(self, torrent_hash: str) -> None:
        try:
            self.post("/api/v2/torrents/reannounce", {"hashes": torrent_hash})
        except Exception as e:
            log.warning("Failed to reannounce torrent %s: %s", torrent_hash, e)

    def add_tags(self, torrent_hash: str, tags: str) -> None:
        try:
            self.post("/api/v2/torrents/addTags", {"hashes": torrent_hash, "tags": tags})
        except Exception as e:
            log.warning("Failed to add tags '%s' to torrent %s: %s", tags, torrent_hash, e)

    def remove_tags(self, torrent_hash: str, tags: str) -> None:
        try:
            self.post("/api/v2/torrents/removeTags", {"hashes": torrent_hash, "tags": tags})
        except Exception as e:
            log.warning("Failed to remove tags '%s' from torrent %s: %s", tags, torrent_hash, e)

    def info(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
        arr = self.get_json("/api/v2/torrents/info", {"hashes": torrent_hash}) or []
        return arr[0] if arr else None

    def files(self, torrent_hash: str) -> List[Dict[str, Any]]:
        return self.get_json("/api/v2/torrents/files", {"hash": torrent_hash}) or []

    def trackers(self, torrent_hash: str) -> List[Dict[str, Any]]:
        return self.get_json("/api/v2/torrents/trackers", {"hash": torrent_hash}) or []

    def set_file_priority(self, torrent_hash: str, file_ids: List[int], priority: int) -> None:
        try:
            id_str = "|".join(str(i) for i in file_ids)
            self.post("/api/v2/torrents/filePrio", {"hash": torrent_hash, "id": id_str, "priority": str(priority)})
        except Exception as e:
            log.warning("Failed to set file priority for torrent %s: %s", torrent_hash, e)


class BaseArr:
    def __init__(self, base_url: str, api_key: str, http: HttpClient, timeout: int, retries: int, name: str):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.http = http
        self.timeout = timeout
        self.retries = retries
        self.name = name

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.key)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base}/api/v3{path}"
        if params:
            url += "?" + uparse.urlencode(params, doseq=True)
        raw = self.http.get(url, headers={"X-Api-Key": self.key}, timeout=self.timeout)
        return None if not raw else json.loads(raw.decode("utf-8"))

    def _post_empty(self, path: str) -> None:
        url = f"{self.base}/api/v3{path}"
        last = None
        for attempt in range(self.retries):
            try:
                self.http.post_bytes(
                    url,
                    b"",
                    headers={"X-Api-Key": self.key, "Content-Type": "application/json", "Content-Length": "0"},
                    timeout=self.timeout,
                )
                return
            except Exception as e:
                last = e
                log.warning("API request failed (attempt %d/%d): %s", attempt + 1, self.retries, str(e).split("\n")[0][:100])
                time.sleep(min(2 ** attempt, 8))
        raise last

    def _put(self, path: str, obj: Dict[str, Any]) -> Any:
        url = f"{self.base}/api/v3{path}"
        payload = json.dumps(obj or {}).encode("utf-8")
        headers = {
            "X-Api-Key": self.key,
            "Content-Type": "application/json",
            "User-Agent": getattr(self.http, "user_agent", "qbit-guard"),
        }
        last = None
        for attempt in range(self.retries):
            try:
                req = ureq.Request(url, data=payload, headers=headers, method="PUT")
                with self.http.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read()
                    return None if not raw else json.loads(raw.decode("utf-8"))
            except Exception as e:
                last = e
                log.warning("API PUT request failed (attempt %d/%d): %s", attempt + 1, self.retries, str(e).split("\n")[0][:100])
                time.sleep(min(2 ** attempt, 8))
        raise last

    def _delete(self, path: str, query: Dict[str, Any]) -> None:
        url = f"{self.base}/api/v3{path}"
        if query:
            url += "?" + uparse.urlencode(query, doseq=True)
        self.http.delete(url, headers={"X-Api-Key": self.key}, timeout=self.timeout)

    def history_for_download(self, download_id: str) -> List[Dict[str, Any]]:
        try:
            obj = self._get("/history", {"downloadId": download_id})
            records = obj.get("records", obj) if isinstance(obj, dict) else obj
            if records:
                return records
        except Exception as e:
            log.warning("Failed to get history %s", e)
        try:
            obj = self._get("/history", {"page": 1, "pageSize": 200, "sortKey": "date", "sortDirection": "descending"})
            records = obj.get("records", []) if isinstance(obj, dict) else (obj or [])
            return [row for row in records if row.get("downloadId", "").lower() == download_id.lower()]
        except Exception as e:
            log.warning("Failed to get history %s", e)
        return []

    def queue_ids_for_download(self, download_id: str) -> List[int]:
        try:
            obj = self._get("/queue", {"page": 1, "pageSize": 500, "sortKey": "timeleft", "sortDirection": "ascending"})
            records = obj.get("records", obj) if isinstance(obj, dict) else obj
            return [int(row["id"]) for row in (records or []) if row.get("id") and row.get("downloadId", "").lower() == download_id.lower()]
        except Exception as e:
            log.warning("Failed to get queue %s", e)
        return []

    @staticmethod
    def dedup_grabbed_ids(history_rows: Sequence[Dict[str, Any]]) -> List[int]:
        grabbed = []
        for row in history_rows:
            event_type = (row.get("eventType") or "").lower()
            data = row.get("data") or {}
            if event_type == "grabbed" or data.get("sourceTitle") or data.get("releaseTitle"):
                grabbed.append(row)
        grabbed.sort(key=lambda x: int(x.get("id", 0)), reverse=True)
        seen = set()
        ids = []
        for row in grabbed:
            data = row.get("data") or {}
            title = (data.get("sourceTitle") or data.get("releaseTitle") or "").strip().lower()
            key = title or ("grab-" + (row.get("downloadId") or ""))
            if key and key not in seen and row.get("id"):
                seen.add(key)
                ids.append(int(row["id"]))
        return ids


class SonarrClient(BaseArr):
    def __init__(self, cfg: Config, http: HttpClient):
        super().__init__(cfg.sonarr_url, cfg.sonarr_apikey, http, cfg.sonarr_timeout_sec, cfg.sonarr_retries, "Sonarr")

    def blocklist_download(self, download_id: str) -> None:
        if not self.enabled:
            return
        rows = self.history_for_download(download_id)
        ids = self.dedup_grabbed_ids(rows)
        if ids:
            try:
                self._post_empty(f"/history/failed/{ids[0]}")
                log.info("Sonarr: blocklisted via history id=%s", ids[0])
                return
            except Exception as e:
                log.warning("Sonarr: history/failed error (%s); trying queue failover", e)
        qids = self.queue_ids_for_download(download_id)
        if qids:
            try:
                self._delete(f"/queue/{qids[0]}", {"blocklist": "true", "removeFromClient": "false"})
                log.info("Sonarr: blocklisted via queue id=%s", qids[0])
            except Exception as e:
                log.error("Sonarr: queue failover error: %s", e)
        else:
            log.info("Sonarr: nothing to fail or in queue for downloadId=%s", download_id)

    def episode(self, episode_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self._get(f"/episode/{episode_id}")
        except Exception as e:
            log.warning("Sonarr: episode %s fetch failed: %s", episode_id, e)
            return None

    def series(self, series_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self._get(f"/series/{series_id}")
        except Exception as e:
            log.warning("Sonarr: series %s fetch failed: %s", series_id, e)
            return None


class RadarrClient(BaseArr):
    def __init__(self, cfg: Config, http: HttpClient):
        super().__init__(cfg.radarr_url, cfg.radarr_apikey, http, cfg.radarr_timeout_sec, cfg.radarr_retries, "Radarr")

    def blocklist_download(self, download_id: str) -> None:
        if not self.enabled:
            return
        rows = self.history_for_download(download_id)
        ids = self.dedup_grabbed_ids(rows)
        if ids:
            try:
                self._post_empty(f"/history/failed/{ids[0]}")
                log.info("Radarr: blocklisted via history id=%s", ids[0])
                return
            except Exception as e:
                log.warning("Radarr: history/failed error (%s); trying queue failover", e)
        qids = self.queue_ids_for_download(download_id)
        if qids:
            try:
                self._delete(f"/queue/{qids[0]}", {"blocklist": "true", "removeFromClient": "false"})
                log.info("Radarr: blocklisted via queue id=%s", qids[0])
            except Exception as e:
                log.error("Radarr: queue failover error: %s", e)
        else:
            log.info("Radarr: nothing to fail or in queue for downloadId=%s", download_id)

    def movie(self, movie_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self._get(f"/movie/{movie_id}")
        except Exception as e:
            log.warning("Radarr: movie %s fetch failed: %s", movie_id, e)
            return None

    def ensure_minimum_availability_released(self, movie_id: int) -> bool:
        if not self.enabled:
            return False
        movie = self.movie(movie_id) or {}
        if not movie:
            log.warning("Radarr: movie %s not found (cannot update minimumAvailability)", movie_id)
            return False
        current = movie.get("minimumAvailability")
        if current == "released":
            log.info("Radarr: movie %s already has minimumAvailability='released'", movie_id)
            return False
        movie["minimumAvailability"] = "released"
        try:
            self._put(f"/movie/{movie_id}", movie)
            log.info("Radarr: movie %s minimumAvailability set to 'released' (was %s)", movie_id, current)
            return True
        except Exception as e:
            log.error("Radarr: failed to set minimumAvailability for movie %s: %s", movie_id, e)
            return False
