from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard.state_store import WatcherStateStore


class WatcherStateStoreTests(unittest.TestCase):
    def test_roundtrip_and_prune(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "watcher.db"
            store = WatcherStateStore(str(db_path))
            try:
                store.upsert_torrent_state("a", {"name": "Episode.mkv", "category": "sonarr"})
                store.upsert_torrent_state("b", {"name": "Movie.mkv", "category": "radarr"})
                store.upsert_retry_state("a", {"attempt": 2, "next_retry_at": 123.0, "last_error": "boom"})

                self.assertEqual(store.load_torrent_state()["a"]["name"], "Episode.mkv")
                self.assertEqual(store.load_retry_state()["a"]["attempt"], 2)

                store.prune_missing_hashes({"a"})
                self.assertIn("a", store.load_torrent_state())
                self.assertNotIn("b", store.load_torrent_state())
                self.assertIn("a", store.load_retry_state())
            finally:
                store.close()

            reopened = WatcherStateStore(str(db_path))
            try:
                self.assertIn("a", reopened.load_torrent_state())
                self.assertEqual(reopened.load_retry_state()["a"]["last_error"], "boom")
            finally:
                reopened.close()

    def test_prune_removes_orphan_retry_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "watcher.db"
            store = WatcherStateStore(str(db_path))
            try:
                store.upsert_retry_state("orphan", {"attempt": 1, "next_retry_at": 10.0, "last_error": "x"})
                self.assertIn("orphan", store.load_retry_state())
                store.prune_missing_hashes(set())
                self.assertNotIn("orphan", store.load_retry_state())
            finally:
                store.close()

    def test_load_torrent_state_drops_corrupt_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "watcher.db"
            store = WatcherStateStore(str(db_path))
            try:
                with store._conn:
                    store._conn.execute(
                        "INSERT INTO torrent_state(hash, state_json, updated_at) VALUES (?, ?, ?)",
                        ("bad-json", "{", 1.0),
                    )
                    store._conn.execute(
                        "INSERT INTO torrent_state(hash, state_json, updated_at) VALUES (?, ?, ?)",
                        ("bad-type", "[]", 1.0),
                    )
                    store._conn.execute(
                        "INSERT INTO torrent_state(hash, state_json, updated_at) VALUES (?, ?, ?)",
                        ("good", '{"name":"Episode.mkv"}', 1.0),
                    )

                loaded = store.load_torrent_state()
                self.assertIn("good", loaded)
                self.assertNotIn("bad-json", loaded)
                self.assertNotIn("bad-type", loaded)

                rows = store._conn.execute(
                    "SELECT hash FROM torrent_state WHERE hash IN (?, ?)",
                    ("bad-json", "bad-type"),
                ).fetchall()
                self.assertEqual(rows, [])
            finally:
                store.close()

    def test_load_retry_state_drops_corrupt_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "watcher.db"
            store = WatcherStateStore(str(db_path))
            try:
                with store._conn:
                    store._conn.execute(
                        "INSERT INTO retry_state(hash, attempt, next_retry_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?)",
                        ("bad", "abc", "xyz", "boom", 1.0),
                    )
                    store._conn.execute(
                        "INSERT INTO retry_state(hash, attempt, next_retry_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?)",
                        ("good", 2, 42.0, "ok", 1.0),
                    )

                loaded = store.load_retry_state()
                self.assertIn("good", loaded)
                self.assertNotIn("bad", loaded)

                rows = store._conn.execute(
                    "SELECT hash FROM retry_state WHERE hash = ?",
                    ("bad",),
                ).fetchall()
                self.assertEqual(rows, [])
            finally:
                store.close()
