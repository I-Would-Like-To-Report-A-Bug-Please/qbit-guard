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
