from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard import version as version_mod


class VersionResolutionTests(unittest.TestCase):
    def test_get_version_prefers_app_version_env(self) -> None:
        with patch.dict(os.environ, {"APP_VERSION": "9.9.9-test"}, clear=False):
            with patch.object(version_mod, "package_version", side_effect=RuntimeError("should not be called")):
                self.assertEqual(version_mod.get_version(), "9.9.9-test")

    def test_get_version_uses_package_metadata_when_available(self) -> None:
        with patch.dict(os.environ, {"APP_VERSION": ""}, clear=False):
            with patch.object(version_mod, "package_version", return_value="1.2.3"):
                self.assertEqual(version_mod.get_version(), "1.2.3")

    def test_get_version_falls_back_to_dev_when_sources_unavailable(self) -> None:
        with patch.dict(os.environ, {"APP_VERSION": ""}, clear=False):
            with patch.object(version_mod, "package_version", side_effect=version_mod.PackageNotFoundError):
                with patch.object(version_mod.subprocess, "check_output", side_effect=FileNotFoundError):
                    self.assertEqual(version_mod.get_version(), "0.0.0-dev")
