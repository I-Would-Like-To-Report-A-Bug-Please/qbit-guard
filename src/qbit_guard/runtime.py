from __future__ import annotations

import datetime
import math
import urllib.error as uerr

from .logging_setup import get_logger


log = get_logger("qbit-guard-runtime")


def short_error(e: Exception, max_len: int = 140) -> str:
    return str(e).split("\n")[0][:max_len]


def is_connection_error(e: Exception) -> bool:
    if isinstance(e, uerr.HTTPError):
        return e.code in (401, 403, 429, 500, 502, 503, 504)
    if isinstance(e, (uerr.URLError, ConnectionError, OSError, TimeoutError)):
        return True
    err = str(e).lower()
    return "timeout" in err or "connection" in err or "network is unreachable" in err


def compute_backoff_delay(attempt: int, initial_delay: float, max_delay: float) -> float:
    return min(initial_delay * (2 ** max(attempt, 0)), max_delay)


def warn_after_attempt(explicit_value: int, attempts: int, minimum: int = 2) -> int:
    if explicit_value > 0:
        return explicit_value
    return max(minimum, int(math.ceil(attempts / 2.0)))


def log_stage_result(stage: str, result: str, details: str = "") -> None:
    if details:
        log.info("%s: %s | %s", stage, result, details)
    else:
        log.info("%s: %s", stage, result)


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso_utc(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def hours_until(dt: datetime.datetime) -> float:
    return (dt - now_utc()).total_seconds() / 3600.0


def domain_from_url(u: str) -> str:
    try:
        s = u.split("://", 1)[-1]
        host = s.split("/", 1)[0].lower()
        return host.split(":", 1)[0]
    except Exception:
        return u.lower()
