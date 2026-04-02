from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard.guard import Config


class ConfigFromEnvTests(unittest.TestCase):
    def test_from_env_reads_values_at_call_time(self) -> None:
        with patch.dict(os.environ, {"QBIT_ALLOWED_CATEGORIES": "radarr,sonarr"}, clear=False):
            first = Config.from_env()
        with patch.dict(os.environ, {"QBIT_ALLOWED_CATEGORIES": "anime"}, clear=False):
            second = Config.from_env()

        self.assertEqual(first.allowed_categories, {"radarr", "sonarr"})
        self.assertEqual(second.allowed_categories, {"anime"})

    def test_from_env_reads_metadata_timeout(self) -> None:
        with patch.dict(os.environ, {"METADATA_MAX_WAIT_SEC": "300"}, clear=False):
            config = Config.from_env()

        self.assertEqual(config.metadata_max_wait_sec, 300)
