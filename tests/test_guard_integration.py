from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard.config import Config
from qbit_guard.guard import TorrentGuard


class TorrentGuardIntegrationTests(unittest.TestCase):
    def _make_guard(self) -> TorrentGuard:
        cfg = Config.from_env()
        guard = TorrentGuard(cfg)
        guard.qbit = Mock()
        guard.sonarr = Mock()
        guard.radarr = Mock()
        guard.preair = Mock()
        guard.preair_movie = Mock()
        guard.metadata = Mock()
        guard.iso = Mock()
        return guard

    def test_skips_disallowed_category_without_stopping(self) -> None:
        guard = self._make_guard()
        guard.cfg.allowed_categories = {"sonarr"}
        guard.qbit.info.return_value = {"category": "general", "name": "Ignored.mkv"}

        guard.run("abc123", "")

        guard.qbit.login.assert_called_once()
        guard.qbit.stop.assert_not_called()
        guard.qbit.start.assert_not_called()

    def test_allows_torrent_after_successful_checks(self) -> None:
        guard = self._make_guard()
        guard.cfg.allowed_categories = {"sonarr"}
        guard.qbit.info.return_value = {"category": "sonarr", "name": "Episode.mkv"}
        guard.qbit.stop.return_value = True
        guard.qbit.trackers.return_value = [{"url": "https://tracker.example/announce"}]
        guard.qbit.start.return_value = True
        guard.preair.should_apply.return_value = False
        guard.preair_movie.should_apply.return_value = False
        guard.metadata.fetch.return_value = [{"name": "Episode.mkv", "size": 123}]
        guard.iso.evaluate_and_act.return_value = False

        guard.run("abc123", "")

        guard.qbit.stop.assert_called_once_with("abc123")
        guard.metadata.fetch.assert_called_once_with("abc123")
        guard.iso.evaluate_and_act.assert_called_once_with("abc123", "sonarr")
        guard.qbit.start.assert_called_once_with("abc123")
        guard.qbit.add_tags.assert_any_call("abc123", "guard:stopped")
        guard.qbit.add_tags.assert_any_call("abc123", "guard:allowed")
        guard.qbit.remove_tags.assert_any_call("abc123", "guard:metadata-pending")

    def test_metadata_unavailable_keeps_torrent_stopped(self) -> None:
        guard = self._make_guard()
        guard.cfg.allowed_categories = {"sonarr"}
        guard.qbit.info.return_value = {"category": "sonarr", "name": "Episode.mkv"}
        guard.qbit.stop.return_value = True
        guard.qbit.trackers.return_value = []
        guard.preair.should_apply.return_value = False
        guard.preair_movie.should_apply.return_value = False
        guard.metadata.fetch.return_value = []

        with self.assertRaisesRegex(RuntimeError, "metadata unavailable"):
            guard.run("abc123", "")

        guard.qbit.start.assert_not_called()
        guard.qbit.add_tags.assert_any_call("abc123", "guard:metadata-pending")

    def test_tv_preair_block_deletes_torrent(self) -> None:
        guard = self._make_guard()
        guard.cfg.allowed_categories = {"sonarr"}
        guard.qbit.info.return_value = {"category": "sonarr", "name": "Episode.mkv"}
        guard.qbit.stop.return_value = True
        guard.qbit.trackers.return_value = []
        guard.preair.should_apply.return_value = True
        guard.preair_movie.should_apply.return_value = False
        guard.preair.decision.return_value = (False, "block", [])

        guard.run("abc123", "")

        guard.sonarr.blocklist_download.assert_called_once_with("abc123")
        guard.qbit.delete.assert_called_once_with("abc123", guard.cfg.delete_files)
        guard.metadata.fetch.assert_not_called()
