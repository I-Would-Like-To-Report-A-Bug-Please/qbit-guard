#!/usr/bin/env python3
"""
watcher.py - stateless

Attaches to qBittorrent's /api/v2/sync/maindata and triggers guard.TorrentGuard
when new torrents appear. No disk state is kept beyond the current process
lifetime, but failed guard runs can be retried in-memory with backoff.

Behavior:
- On first snapshot:
  - If WATCH_PROCESS_EXISTING_AT_START=1, process all currently present torrents.
  - Otherwise, index them and only process hashes that already have scheduled
    retries from this process lifetime.
- During runtime:
  - Process a torrent the first time we see its infohash.
  - Retry guard failures with exponential backoff.
  - If qB reports torrents_removed, we forget those hashes so a future re-add
    will be processed again.
- Optional: force a rescan if category or tags contain WATCH_RESCAN_KEYWORD
  (default 'rescan'), even if we've already processed it in this session.
"""

import os, sys, json, time, signal, math, urllib.parse as uparse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set, Tuple, Any
import urllib.error

# Your class-based guard + clients
from guard import Config, HttpClient, QbitClient, TorrentGuard
from logging_setup import get_logger
from version import VERSION

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log = get_logger("qbit-guard-watcher")


POLL_SEC = float(os.getenv("WATCH_POLL_SECONDS", "3.0"))
PROCESS_EXISTING_AT_START = os.getenv("WATCH_PROCESS_EXISTING_AT_START", "0") == "1"
RESCAN_KEYWORD = os.getenv("WATCH_RESCAN_KEYWORD", "rescan").strip().lower()  # in category/tags -> force

# Connection retry configuration
MAX_RETRY_ATTEMPTS = int(os.getenv("QBIT_MAX_RETRY_ATTEMPTS", "5"))
INITIAL_BACKOFF_SEC = float(os.getenv("QBIT_INITIAL_BACKOFF_SEC", "1.0"))
MAX_BACKOFF_SEC = float(os.getenv("QBIT_MAX_BACKOFF_SEC", "60.0"))
GUARD_RUN_MAX_RETRIES = int(os.getenv("GUARD_RUN_MAX_RETRIES", "3"))
GUARD_RUN_INITIAL_BACKOFF_SEC = float(os.getenv("GUARD_RUN_INITIAL_BACKOFF_SEC", "30.0"))
GUARD_RUN_MAX_BACKOFF_SEC = float(os.getenv("GUARD_RUN_MAX_BACKOFF_SEC", "900.0"))
QBIT_CONNECTION_WARN_AFTER_ATTEMPT = int(os.getenv("QBIT_CONNECTION_WARN_AFTER_ATTEMPT", "0"))
WATCH_MAX_CONCURRENT_GUARDS = int(os.getenv("WATCH_MAX_CONCURRENT_GUARDS", "8"))

def is_connection_error(e: Exception) -> bool:
    """Check if an exception indicates a connection problem that warrants retry."""
    if isinstance(e, urllib.error.HTTPError):
        # Common HTTP error codes that indicate connection/auth issues
        return e.code in (401, 403, 500, 502, 503, 504)
    if isinstance(e, (urllib.error.URLError, ConnectionError, OSError)):
        return True
    # Check for timeout and other network-related errors
    if "timeout" in str(e).lower() or "connection" in str(e).lower():
        return True
    return False


def connection_warn_after() -> int:
    if QBIT_CONNECTION_WARN_AFTER_ATTEMPT > 0:
        return QBIT_CONNECTION_WARN_AFTER_ATTEMPT
    return max(2, int(math.ceil(MAX_RETRY_ATTEMPTS / 2.0)))


def exponential_backoff_sleep(attempt: int, initial_delay: float = INITIAL_BACKOFF_SEC, max_delay: float = MAX_BACKOFF_SEC, warn_after: int = None) -> None:
    """Sleep with exponential backoff, capped at max_delay."""
    delay = min(initial_delay * (2 ** attempt), max_delay)
    threshold = warn_after if warn_after is not None else connection_warn_after()
    if attempt + 1 >= threshold:
        log.warning("Connection failed, retrying in %.1f seconds (attempt %d/%d)", delay, attempt + 1, MAX_RETRY_ATTEMPTS)
    else:
        log.debug("Connection failed, retrying in %.1f seconds (attempt %d/%d)", delay, attempt + 1, MAX_RETRY_ATTEMPTS)
    time.sleep(delay)

def compute_backoff_delay(attempt: int, initial_delay: float, max_delay: float) -> float:
    """Return exponential backoff delay, capped at max_delay."""
    return min(initial_delay * (2 ** max(attempt, 0)), max_delay)


def log_connection_event(level_attempt: int, message: str, *args) -> None:
    if level_attempt >= connection_warn_after():
        log.warning(message, *args)
    else:
        log.debug(message, *args)

def qb_sync_maindata(http: HttpClient, cfg: Config, rid: int) -> Dict:
    url = f"{cfg.qbit_host}/api/v2/sync/maindata"
    if rid:
        url += "?" + uparse.urlencode({"rid": rid})
    raw = http.get(url)
    return {} if not raw else json.loads(raw.decode("utf-8"))


def run_guard_job(cfg: Config, torrent_hash: str, category: str) -> None:
    # Each worker gets its own guard/client state so retries and auth cookies do
    # not race across threads.
    TorrentGuard(cfg).run(torrent_hash, category)


def merge_torrent_state(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(previous or {})
    merged.update(current or {})
    return merged

def _should_process(h: str, t: Dict, seen: Set[str], retry_state: Dict[str, Dict[str, Any]], inflight: Set[str], now_ts: float) -> Tuple[bool, str]:
    # Manual rescan via keyword in category or tags
    cat = (t.get("category") or "").strip().lower()
    tags = (t.get("tags") or "").strip().lower()
    if h in inflight:
        return False, "in-flight"
    if RESCAN_KEYWORD and (RESCAN_KEYWORD in cat or RESCAN_KEYWORD in tags):
        return True, "manual-rescan"
    retry = retry_state.get(h)
    if retry and now_ts >= float(retry.get("next_retry_at", 0.0)):
        return True, "retry"
    if h not in seen:
        return True, "new"
    return False, "already-seen"

def main():
    log.info("qbit-guard watcher initializing - version %s", VERSION)
    cfg = Config()
    http = HttpClient(cfg.ignore_tls, cfg.user_agent)
    qb = QbitClient(cfg, http)
    log.info("Watcher configuration loaded - host=%s, categories=%s", cfg.qbit_host, sorted(cfg.allowed_categories))

    # graceful shutdown
    stop = {"flag": False}
    def _sig(*_): stop["flag"] = True
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _sig)

    # login with retry logic
    def ensure_authenticated() -> bool:
        """Ensure we're authenticated with qBittorrent, with retry logic."""
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                qb.login()
                if attempt + 1 >= connection_warn_after() and attempt > 0:
                    log.info("qBittorrent login recovered after %d retr%s", attempt, "y" if attempt == 1 else "ies")
                return True
            except Exception as e:
                if not is_connection_error(e) or attempt == MAX_RETRY_ATTEMPTS - 1:
                    log.error("qBittorrent login failed after %d attempts: %s", attempt + 1, e)
                    return False
                exponential_backoff_sleep(attempt)
        return False

    if not ensure_authenticated():
        log.critical("Fatal: Unable to authenticate with qBittorrent after maximum retries")
        log.critical("Terminating watcher process (exit code 2)")
        sys.exit(2)

    seen: Set[str] = set()
    rid = 0
    retry_state: Dict[str, Dict[str, Any]] = {}
    torrent_state: Dict[str, Dict[str, Any]] = {}
    executor = ThreadPoolExecutor(max_workers=max(1, WATCH_MAX_CONCURRENT_GUARDS), thread_name_prefix="guard")
    inflight: Dict[str, Dict[str, Any]] = {}
    first_snapshot = True
    consecutive_failures = 0
    log.info(
        "Watcher (stateless) started - version %s, host=%s, poll=%.1fs, process_existing_at_start=%s, rescan-keyword='%s', guard_run_max_retries=%d, max_concurrent_guards=%d",
        VERSION, cfg.qbit_host, POLL_SEC, PROCESS_EXISTING_AT_START, RESCAN_KEYWORD or "(disabled)", GUARD_RUN_MAX_RETRIES, max(1, WATCH_MAX_CONCURRENT_GUARDS)
    )

    try:
        while not stop["flag"]:
            try:
                completed_hashes = [h for h, item in inflight.items() if item["future"].done()]
                for h in completed_hashes:
                    item = inflight.pop(h)
                    future = item["future"]
                    name = item["name"]
                    try:
                        future.result()
                        if h in retry_state:
                            attempt_count = int(retry_state[h].get("attempt", 0))
                            log.info("Guard retry succeeded for torrent %s after %d failed attempt(s).", h[:8], attempt_count)
                            retry_state.pop(h, None)
                        seen.add(h)
                    except Exception as e:
                        error_str = str(e).split("\n")[0][:100]
                        if "404" in error_str or "Not Found" in error_str:
                            log.warning("Torrent %s (%s) was deleted before processing completed: %s", h[:8], name[:50], error_str)
                            retry_state.pop(h, None)
                            seen.add(h)
                        elif "401" in error_str or "403" in error_str or "Unauthorized" in error_str or "Forbidden" in error_str:
                            log.error("Authentication failed while processing torrent %s (%s): %s", h[:8], name[:50], error_str)
                            retry_state.pop(h, None)
                            seen.add(h)
                        else:
                            seen.add(h)
                            if GUARD_RUN_MAX_RETRIES <= 0:
                                log.error("Guard run failed for torrent %s (%s): %s", h[:8], name[:50], error_str)
                                continue

                            prev_attempt = int(retry_state.get(h, {}).get("attempt", 0))
                            next_attempt = prev_attempt + 1
                            if next_attempt > GUARD_RUN_MAX_RETRIES:
                                retry_state.pop(h, None)
                                log.error(
                                    "Guard run failed for torrent %s (%s): %s | retry budget exhausted after %d attempt(s)",
                                    h[:8], name[:50], error_str, prev_attempt
                                )
                                continue

                            delay = compute_backoff_delay(
                                next_attempt - 1,
                                GUARD_RUN_INITIAL_BACKOFF_SEC,
                                GUARD_RUN_MAX_BACKOFF_SEC,
                            )
                            retry_state[h] = {
                                "attempt": next_attempt,
                                "next_retry_at": time.time() + delay,
                                "last_error": error_str,
                            }
                            log.warning(
                                "Guard run failed for torrent %s (%s): %s | retrying in %.1f seconds (attempt %d/%d)",
                                h[:8], name[:50], error_str, delay, next_attempt, GUARD_RUN_MAX_RETRIES
                            )

                data = qb_sync_maindata(http, cfg, rid)
                if not data:
                    time.sleep(POLL_SEC)
                    continue

                consecutive_failures = 0
                rid = data.get("rid", rid)
                torrents = data.get("torrents") or {}
                removed = data.get("torrents_removed") or []

                for h, t in torrents.items():
                    torrent_state[h] = merge_torrent_state(torrent_state.get(h, {}), t)

                if first_snapshot:
                    first_snapshot = False
                    present = set(torrent_state.keys())
                    if PROCESS_EXISTING_AT_START:
                        log.info("Initial snapshot: processing %d existing torrents.", len(present))
                    else:
                        seen |= {h for h in present if h not in retry_state}
                        retry_candidates = sum(1 for h in present if h in retry_state)
                        log.info(
                            "Initial snapshot: indexed %d existing torrents (not processing), %d scheduled retry(s) remain eligible.",
                            len(present),
                            retry_candidates,
                        )

                for h in removed:
                    seen.discard(h)
                    retry_state.pop(h, None)
                    inflight.pop(h, None)
                    torrent_state.pop(h, None)

                now_ts = time.time()
                inflight_hashes = set(inflight.keys())
                for h, t in torrent_state.items():
                    name = t.get("name") or ""
                    category = (t.get("category") or "").strip()
                    ok, reason = _should_process(h, t, seen, retry_state, inflight_hashes, now_ts)
                    if not ok:
                        log.debug("Skip %s | %s", h, reason)
                        continue

                    log.info("Processing %s | reason=%s | category='%s' | name='%s'", h, reason, category, name)
                    inflight[h] = {
                        "future": executor.submit(run_guard_job, cfg, h, category),
                        "name": name,
                        "category": category,
                    }
                    inflight_hashes.add(h)

            except Exception as e:
                if is_connection_error(e):
                    consecutive_failures += 1
                    log_connection_event(
                        consecutive_failures,
                        "Connection error (consecutive failure %d): %s",
                        consecutive_failures,
                        str(e).split("\n")[0][:100],
                    )

                    if consecutive_failures >= 2:
                        log_connection_event(
                            consecutive_failures,
                            "Multiple connection failures detected (%d), attempting full reconnection...",
                            consecutive_failures,
                        )
                        rid = 0
                        first_snapshot = True

                        reconnected = False
                        for attempt in range(MAX_RETRY_ATTEMPTS):
                            try:
                                qb.login()
                                log.info("Successfully reconnected to qBittorrent after %d attempts", attempt + 1)
                                if consecutive_failures >= connection_warn_after():
                                    log.info("Watcher connection recovered after %d consecutive failure(s)", consecutive_failures)
                                consecutive_failures = 0
                                reconnected = True
                                break
                            except Exception as auth_e:
                                if not is_connection_error(auth_e) or attempt == MAX_RETRY_ATTEMPTS - 1:
                                    log.error("Reconnection attempt failed after %d attempts: %s", attempt + 1, auth_e)
                                    break
                                exponential_backoff_sleep(attempt)

                        if not reconnected:
                            log.critical("Fatal: Failed to reconnect to qBittorrent after multiple attempts")
                            log.critical("Terminating watcher process (exit code 3)")
                            sys.exit(3)
                    else:
                        exponential_backoff_sleep(0)
                else:
                    log.error("Watcher loop error (non-connection): %s", str(e).split("\n")[0][:100])
                    consecutive_failures = 0

            time.sleep(POLL_SEC)

        log.info("Received shutdown signal, cleaning up...")
    finally:
        executor.shutdown(wait=False, cancel_futures=False)
        log.info("qbit-guard watcher shutdown complete")

if __name__ == "__main__":
    main()
