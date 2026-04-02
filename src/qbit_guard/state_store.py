from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable


class WatcherStateStore:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS torrent_state (
                    hash TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retry_state (
                    hash TEXT PRIMARY KEY,
                    attempt INTEGER NOT NULL,
                    next_retry_at REAL NOT NULL,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )

    def load_torrent_state(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT hash, state_json FROM torrent_state").fetchall()
        return {row["hash"]: json.loads(row["state_json"]) for row in rows}

    def load_retry_state(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT hash, attempt, next_retry_at, last_error FROM retry_state"
            ).fetchall()
        return {
            row["hash"]: {
                "attempt": int(row["attempt"]),
                "next_retry_at": float(row["next_retry_at"]),
                "last_error": row["last_error"] or "",
            }
            for row in rows
        }

    def upsert_torrent_state(self, torrent_hash: str, state: Dict[str, Any]) -> None:
        payload = json.dumps(state, sort_keys=True)
        now_ts = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO torrent_state(hash, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (torrent_hash, payload, now_ts),
            )

    def delete_torrent_state(self, torrent_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM torrent_state WHERE hash = ?", (torrent_hash,))

    def upsert_retry_state(self, torrent_hash: str, retry: Dict[str, Any]) -> None:
        now_ts = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO retry_state(hash, attempt, next_retry_at, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET
                    attempt=excluded.attempt,
                    next_retry_at=excluded.next_retry_at,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    torrent_hash,
                    int(retry.get("attempt", 0)),
                    float(retry.get("next_retry_at", 0.0)),
                    str(retry.get("last_error", "")),
                    now_ts,
                ),
            )

    def delete_retry_state(self, torrent_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM retry_state WHERE hash = ?", (torrent_hash,))

    def prune_missing_hashes(self, active_hashes: Iterable[str]) -> None:
        active = set(active_hashes)
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT hash FROM torrent_state").fetchall()
            stale = [row["hash"] for row in rows if row["hash"] not in active]
            if stale:
                self._conn.executemany("DELETE FROM torrent_state WHERE hash = ?", [(h,) for h in stale])
                self._conn.executemany("DELETE FROM retry_state WHERE hash = ?", [(h,) for h in stale])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
