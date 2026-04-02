from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Set

from .extensions import _ext_of, _split_exts
from .logging_setup import get_logger


log = get_logger("qbit-guard")

DISC_IMAGE_EXTS = _split_exts("iso, img, mdf, nrg, cue, bin")
RISKY_EXEC_EXTS = _split_exts("exe, bat, cmd, sh, ps1, msi, dmg, apk, jar, com, scr, vbs, vb, lnk, reg")
ARCHIVE_EXTS = _split_exts("zip, rar, 7z, tar, gz, bz2, xz, zst")

DEFAULT_ALLOWED_EXTS = _split_exts(
    """
    mkv, mp4, m4v, mov, webm, avi, m2ts, ts,
    srt, ass, ssa, sub, idx, sup,
    flac, mka, mp3, aac, ac3, eac3, dts, opus,
    nfo, txt, jpg, jpeg, png, webp
    """
)
DEFAULT_BLOCKED_EXTS = set().union(DISC_IMAGE_EXTS, RISKY_EXEC_EXTS, ARCHIVE_EXTS)


@dataclass
class Config:
    qbit_host: str = os.getenv("QBIT_HOST", "http://127.0.0.1:8080").rstrip("/")
    qbit_user: str = os.getenv("QBIT_USER", "admin")
    qbit_pass: str = os.getenv("QBIT_PASS", "adminadmin")
    allowed_categories: Set[str] = frozenset(
        c.strip().lower() for c in os.getenv("QBIT_ALLOWED_CATEGORIES", "tv-sonarr,radarr").split(",") if c.strip()
    )
    ignore_tls: bool = os.getenv("QBIT_IGNORE_TLS", "0") == "1"
    dry_run: bool = os.getenv("QBIT_DRY_RUN", "0") == "1"
    delete_files: bool = os.getenv("QBIT_DELETE_FILES", "true").lower() in ("1", "true", "yes")
    user_agent: str = os.getenv("USER_AGENT", "qbit-guard/2.0")
    qbit_request_retries: int = int(os.getenv("QBIT_REQUEST_RETRIES", "3"))
    qbit_request_initial_backoff_sec: float = float(os.getenv("QBIT_REQUEST_INITIAL_BACKOFF_SEC", "1.0"))
    qbit_request_max_backoff_sec: float = float(os.getenv("QBIT_REQUEST_MAX_BACKOFF_SEC", "15.0"))
    qbit_request_warn_after_attempt: int = int(os.getenv("QBIT_REQUEST_WARN_AFTER_ATTEMPT", "0"))

    enable_preair: bool = os.getenv("ENABLE_PREAIR_CHECK", "1") == "1"
    sonarr_url: str = (os.getenv("SONARR_URL", "http://127.0.0.1:8989") or "").rstrip("/")
    sonarr_apikey: str = os.getenv("SONARR_APIKEY", "")
    sonarr_categories: Set[str] = frozenset(
        c.strip().lower() for c in os.getenv("SONARR_CATEGORIES", "tv-sonarr").split(",") if c.strip()
    )
    early_grace_hours: float = float(os.getenv("EARLY_GRACE_HOURS", "6"))
    early_hard_limit_hours: float = float(os.getenv("EARLY_HARD_LIMIT_HOURS", "72"))
    whitelist_overrides_hard_limit: bool = os.getenv("WHITELIST_OVERRIDES_HARD_LIMIT", "0") == "1"
    whitelist_groups: Set[str] = frozenset(
        g.strip().lower() for g in os.getenv("EARLY_WHITELIST_GROUPS", "").split(",") if g.strip()
    )
    whitelist_indexers: Set[str] = frozenset(
        i.strip().lower() for i in os.getenv("EARLY_WHITELIST_INDEXERS", "").split(",") if i.strip()
    )
    whitelist_trackers: Set[str] = frozenset(
        t.strip().lower() for t in os.getenv("EARLY_WHITELIST_TRACKERS", "").split(",") if t.strip()
    )
    resume_if_no_history: bool = os.getenv("RESUME_IF_NO_HISTORY", "1") == "1"
    sonarr_timeout_sec: int = int(os.getenv("SONARR_TIMEOUT_SEC", "45"))
    sonarr_retries: int = int(os.getenv("SONARR_RETRIES", "3"))

    internet_check_provider: str = os.getenv("INTERNET_CHECK_PROVIDER", "tvmaze").strip().lower()
    tvmaze_base: str = os.getenv("TVMAZE_BASE", "https://api.tvmaze.com").rstrip("/")
    tvmaze_timeout: int = int(os.getenv("TVMAZE_TIMEOUT_SEC", "8"))
    tvdb_base: str = os.getenv("TVDB_BASE", "https://api4.thetvdb.com/v4").rstrip("/")
    tvdb_apikey: str = os.getenv("TVDB_APIKEY", "")
    tvdb_pin: str = os.getenv("TVDB_PIN", "")
    tvdb_language: str = os.getenv("TVDB_LANGUAGE", "eng")
    tvdb_order: str = os.getenv("TVDB_ORDER", "default").strip().lower()
    tvdb_timeout: int = int(os.getenv("TVDB_TIMEOUT_SEC", "8"))
    tvdb_bearer: str = os.getenv("TVDB_BEARER", "")
    tmdb_base: str = os.getenv("TMDB_BASE", "https://api.themoviedb.org/3").rstrip("/")
    tmdb_apikey: str = os.getenv("TMDB_APIKEY", "")
    tmdb_timeout: int = int(os.getenv("TMDB_TIMEOUT_SEC", "8"))

    enable_iso_check: bool = os.getenv("ENABLE_ISO_CHECK", "1") == "1"
    min_keepable_video_mb: float = float(os.getenv("MIN_KEEPABLE_VIDEO_MB", "50"))
    metadata_poll_interval: float = float(os.getenv("METADATA_POLL_INTERVAL", "1.5"))
    metadata_max_wait_sec: int = int(os.getenv("METADATA_MAX_WAIT_SEC", "0"))
    metadata_download_budget_bytes: int = int(os.getenv("METADATA_DOWNLOAD_BUDGET_BYTES", "0"))
    metadata_max_transient_errors: int = int(os.getenv("METADATA_MAX_TRANSIENT_ERRORS", "8"))

    min_torrent_age_minutes: int = int(os.getenv("MIN_TORRENT_AGE_MINUTES", "0"))

    radarr_url: str = (os.getenv("RADARR_URL", "http://127.0.0.1:7878") or "").rstrip("/")
    radarr_apikey: str = os.getenv("RADARR_APIKEY", "")
    radarr_preair_categories: Set[str] = frozenset(
        c.strip().lower()
        for c in os.getenv("RADARR_PREAIR_CATEGORIES", os.getenv("RADARR_CATEGORIES", "radarr")).split(",")
        if c.strip()
    )
    radarr_categories: Set[str] = frozenset(
        c.strip().lower() for c in os.getenv("RADARR_CATEGORIES", "radarr").split(",") if c.strip()
    )
    radarr_timeout_sec: int = int(os.getenv("RADARR_TIMEOUT_SEC", "45"))
    radarr_retries: int = int(os.getenv("RADARR_RETRIES", "3"))

    ext_strategy: str = os.getenv("GUARD_EXT_STRATEGY", "block").strip().lower()
    allowed_exts: Set[str] = None
    blocked_exts: Set[str] = None
    exts_file: str = os.getenv("GUARD_EXTS_FILE", "/config/extensions.json")
    ext_delete_if_all_blocked: bool = os.getenv("GUARD_EXT_DELETE_IF_ALL_BLOCKED", "1") in ("1", "true", "yes")
    ext_delete_if_any_blocked: bool = os.getenv("GUARD_EXT_DELETE_IF_ANY_BLOCKED", "0") in ("1", "true", "yes")
    ext_violation_tag: str = os.getenv("GUARD_EXT_VIOLATION_TAG", "trash:ext")
    uncheck_blocked_files: bool = os.getenv("GUARD_UNCHECK_BLOCKED_FILES", "1") in ("1", "true", "yes")
    disc_exts_env: str = os.getenv("GUARD_DISC_EXTS", "")
    disc_exts: Set[str] = None

    detailed_logging: bool = os.getenv("LOG_LEVEL", "INFO").upper() == "DETAILED"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            qbit_host=os.getenv("QBIT_HOST", "http://127.0.0.1:8080").rstrip("/"),
            qbit_user=os.getenv("QBIT_USER", "admin"),
            qbit_pass=os.getenv("QBIT_PASS", "adminadmin"),
            allowed_categories=frozenset(
                c.strip().lower()
                for c in os.getenv("QBIT_ALLOWED_CATEGORIES", "tv-sonarr,radarr").split(",")
                if c.strip()
            ),
            ignore_tls=os.getenv("QBIT_IGNORE_TLS", "0") == "1",
            dry_run=os.getenv("QBIT_DRY_RUN", "0") == "1",
            delete_files=os.getenv("QBIT_DELETE_FILES", "true").lower() in ("1", "true", "yes"),
            user_agent=os.getenv("USER_AGENT", "qbit-guard/2.0"),
            qbit_request_retries=int(os.getenv("QBIT_REQUEST_RETRIES", "3")),
            qbit_request_initial_backoff_sec=float(os.getenv("QBIT_REQUEST_INITIAL_BACKOFF_SEC", "1.0")),
            qbit_request_max_backoff_sec=float(os.getenv("QBIT_REQUEST_MAX_BACKOFF_SEC", "15.0")),
            qbit_request_warn_after_attempt=int(os.getenv("QBIT_REQUEST_WARN_AFTER_ATTEMPT", "0")),
            enable_preair=os.getenv("ENABLE_PREAIR_CHECK", "1") == "1",
            sonarr_url=(os.getenv("SONARR_URL", "http://127.0.0.1:8989") or "").rstrip("/"),
            sonarr_apikey=os.getenv("SONARR_APIKEY", ""),
            sonarr_categories=frozenset(
                c.strip().lower()
                for c in os.getenv("SONARR_CATEGORIES", "tv-sonarr").split(",")
                if c.strip()
            ),
            early_grace_hours=float(os.getenv("EARLY_GRACE_HOURS", "6")),
            early_hard_limit_hours=float(os.getenv("EARLY_HARD_LIMIT_HOURS", "72")),
            whitelist_overrides_hard_limit=os.getenv("WHITELIST_OVERRIDES_HARD_LIMIT", "0") == "1",
            whitelist_groups=frozenset(
                g.strip().lower() for g in os.getenv("EARLY_WHITELIST_GROUPS", "").split(",") if g.strip()
            ),
            whitelist_indexers=frozenset(
                i.strip().lower() for i in os.getenv("EARLY_WHITELIST_INDEXERS", "").split(",") if i.strip()
            ),
            whitelist_trackers=frozenset(
                t.strip().lower() for t in os.getenv("EARLY_WHITELIST_TRACKERS", "").split(",") if t.strip()
            ),
            resume_if_no_history=os.getenv("RESUME_IF_NO_HISTORY", "1") == "1",
            sonarr_timeout_sec=int(os.getenv("SONARR_TIMEOUT_SEC", "45")),
            sonarr_retries=int(os.getenv("SONARR_RETRIES", "3")),
            internet_check_provider=os.getenv("INTERNET_CHECK_PROVIDER", "tvmaze").strip().lower(),
            tvmaze_base=os.getenv("TVMAZE_BASE", "https://api.tvmaze.com").rstrip("/"),
            tvmaze_timeout=int(os.getenv("TVMAZE_TIMEOUT_SEC", "8")),
            tvdb_base=os.getenv("TVDB_BASE", "https://api4.thetvdb.com/v4").rstrip("/"),
            tvdb_apikey=os.getenv("TVDB_APIKEY", ""),
            tvdb_pin=os.getenv("TVDB_PIN", ""),
            tvdb_language=os.getenv("TVDB_LANGUAGE", "eng"),
            tvdb_order=os.getenv("TVDB_ORDER", "default").strip().lower(),
            tvdb_timeout=int(os.getenv("TVDB_TIMEOUT_SEC", "8")),
            tvdb_bearer=os.getenv("TVDB_BEARER", ""),
            tmdb_base=os.getenv("TMDB_BASE", "https://api.themoviedb.org/3").rstrip("/"),
            tmdb_apikey=os.getenv("TMDB_APIKEY", ""),
            tmdb_timeout=int(os.getenv("TMDB_TIMEOUT_SEC", "8")),
            enable_iso_check=os.getenv("ENABLE_ISO_CHECK", "1") == "1",
            min_keepable_video_mb=float(os.getenv("MIN_KEEPABLE_VIDEO_MB", "50")),
            metadata_poll_interval=float(os.getenv("METADATA_POLL_INTERVAL", "1.5")),
            metadata_max_wait_sec=int(os.getenv("METADATA_MAX_WAIT_SEC", "0")),
            metadata_download_budget_bytes=int(os.getenv("METADATA_DOWNLOAD_BUDGET_BYTES", "0")),
            metadata_max_transient_errors=int(os.getenv("METADATA_MAX_TRANSIENT_ERRORS", "8")),
            min_torrent_age_minutes=int(os.getenv("MIN_TORRENT_AGE_MINUTES", "0")),
            radarr_url=(os.getenv("RADARR_URL", "http://127.0.0.1:7878") or "").rstrip("/"),
            radarr_apikey=os.getenv("RADARR_APIKEY", ""),
            radarr_preair_categories=frozenset(
                c.strip().lower()
                for c in os.getenv("RADARR_PREAIR_CATEGORIES", os.getenv("RADARR_CATEGORIES", "radarr")).split(",")
                if c.strip()
            ),
            radarr_categories=frozenset(
                c.strip().lower() for c in os.getenv("RADARR_CATEGORIES", "radarr").split(",") if c.strip()
            ),
            radarr_timeout_sec=int(os.getenv("RADARR_TIMEOUT_SEC", "45")),
            radarr_retries=int(os.getenv("RADARR_RETRIES", "3")),
            ext_strategy=os.getenv("GUARD_EXT_STRATEGY", "block").strip().lower(),
            exts_file=os.getenv("GUARD_EXTS_FILE", "/config/extensions.json"),
            ext_delete_if_all_blocked=os.getenv("GUARD_EXT_DELETE_IF_ALL_BLOCKED", "1") in ("1", "true", "yes"),
            ext_delete_if_any_blocked=os.getenv("GUARD_EXT_DELETE_IF_ANY_BLOCKED", "0") in ("1", "true", "yes"),
            ext_violation_tag=os.getenv("GUARD_EXT_VIOLATION_TAG", "trash:ext"),
            uncheck_blocked_files=os.getenv("GUARD_UNCHECK_BLOCKED_FILES", "1") in ("1", "true", "yes"),
            disc_exts_env=os.getenv("GUARD_DISC_EXTS", ""),
            detailed_logging=os.getenv("LOG_LEVEL", "INFO").upper() == "DETAILED",
        )

    def __post_init__(self) -> None:
        self.allowed_exts = set(DEFAULT_ALLOWED_EXTS)
        self.blocked_exts = set(DEFAULT_BLOCKED_EXTS)

        if os.path.isfile(self.exts_file):
            try:
                with open(self.exts_file, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                strategy = str(data.get("strategy", self.ext_strategy)).strip().lower()
                if strategy in ("block", "allow"):
                    self.ext_strategy = strategy
                allowed_val = data.get("allowed", [])
                blocked_val = data.get("blocked", [])
                allowed = _split_exts(",".join(allowed_val)) if isinstance(allowed_val, list) else _split_exts(str(allowed_val or ""))
                blocked = _split_exts(",".join(blocked_val)) if isinstance(blocked_val, list) else _split_exts(str(blocked_val or ""))
                if allowed:
                    self.allowed_exts = allowed
                if blocked:
                    self.blocked_exts = blocked
                log.info(
                    "Loaded extension policy from %s | strategy=%s | allowed=%d | blocked=%d",
                    self.exts_file,
                    self.ext_strategy,
                    len(self.allowed_exts),
                    len(self.blocked_exts),
                )
            except Exception as e:
                log.warning("Failed to read %s: %s (falling back to env/defaults)", self.exts_file, e)

        env_allowed = _split_exts(os.getenv("GUARD_ALLOWED_EXTS", ""))
        env_blocked = _split_exts(os.getenv("GUARD_BLOCKED_EXTS", ""))
        env_disc = _split_exts(self.disc_exts_env)
        self.disc_exts = env_disc if env_disc else set(DISC_IMAGE_EXTS)

        env_strategy = os.getenv("GUARD_EXT_STRATEGY", "").strip().lower()
        if env_allowed:
            self.allowed_exts = env_allowed
        if env_blocked:
            self.blocked_exts = env_blocked
        if env_strategy in ("block", "allow"):
            self.ext_strategy = env_strategy

        log.info(
            "Extension policy | strategy=%s | allowed=%d | blocked=%d | enforce(any=%s, all=%s, uncheck=%s)",
            self.ext_strategy,
            len(self.allowed_exts),
            len(self.blocked_exts),
            self.ext_delete_if_any_blocked,
            self.ext_delete_if_all_blocked,
            self.uncheck_blocked_files,
        )

    def is_ext_allowed(self, ext: str) -> bool:
        if not ext:
            return self.ext_strategy == "block"
        if ext in self.blocked_exts:
            return False
        if self.ext_strategy == "allow":
            return ext in self.allowed_exts
        return True

    def is_path_allowed(self, path: str) -> bool:
        return self.is_ext_allowed(_ext_of(path))
