from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbit_guard.extensions import _ext_of, _generate_detailed_extension_summary, _split_exts


class ExtensionHelpersTests(unittest.TestCase):
    def test_split_exts_normalizes_case_and_dots(self) -> None:
        self.assertEqual(_split_exts(" .MKV,mp4 ; SRT "), {"mkv", "mp4", "srt"})

    def test_ext_of_returns_last_suffix(self) -> None:
        self.assertEqual(_ext_of("/downloads/movie.sample.mkv"), "mkv")
        self.assertEqual(_ext_of("README"), "")

    def test_detailed_summary_groups_extensions(self) -> None:
        summary = _generate_detailed_extension_summary(
            [
                {"name": "bad.exe"},
                {"name": "nested/archive.zip"},
                {"name": "second.exe"},
            ]
        )

        self.assertIn(".exe: 2 files", summary)
        self.assertIn(".zip: 1 file", summary)
