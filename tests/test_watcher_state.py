from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard.watcher_state import merge_torrent_state, should_process


class WatcherStateTests(unittest.TestCase):
    def test_merge_torrent_state_preserves_sparse_fields(self) -> None:
        merged = merge_torrent_state(
            {"name": "Episode.mkv", "category": "sonarr", "tags": "guard:metadata-pending"},
            {"tags": "guard:metadata-pending,rescan"},
        )

        self.assertEqual(merged["name"], "Episode.mkv")
        self.assertEqual(merged["category"], "sonarr")
        self.assertEqual(merged["tags"], "guard:metadata-pending,rescan")

    def test_should_process_respects_retry_window(self) -> None:
        ok, reason = should_process(
            "abc",
            {"category": "sonarr", "tags": ""},
            seen={"abc"},
            retry_state={"abc": {"next_retry_at": 10}},
            inflight=set(),
            now_ts=15,
            rescan_keyword="rescan",
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "retry")

    def test_should_process_ignores_inflight_hash(self) -> None:
        ok, reason = should_process(
            "abc",
            {"category": "sonarr", "tags": ""},
            seen=set(),
            retry_state={},
            inflight={"abc"},
            now_ts=15,
            rescan_keyword="rescan",
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "in-flight")

    def test_should_process_manual_rescan_even_when_seen(self) -> None:
        ok, reason = should_process(
            "abc",
            {"category": "sonarr", "tags": "guard:metadata-pending,rescan"},
            seen={"abc"},
            retry_state={},
            inflight=set(),
            now_ts=15,
            rescan_keyword="rescan",
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "manual-rescan")
