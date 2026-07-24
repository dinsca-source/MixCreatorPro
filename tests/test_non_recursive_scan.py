# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gui import MixCreatorApp
from utils import scan_mp3_files


def _write_file(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class NonRecursiveScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "Eventi"
        self.folder.mkdir(parents=True, exist_ok=True)

        # 3 MP3 in root
        _write_file(self.folder / "Brano1.mp3")
        _write_file(self.folder / "Brano2.mp3")
        _write_file(self.folder / "Brano3.MP3")

        # Non-MP3 in root
        _write_file(self.folder / "note.txt")

        # 2 subfolders with 4 MP3 total (must be ignored)
        _write_file(self.folder / "Archivio" / "A1.mp3")
        _write_file(self.folder / "Archivio" / "A2.mp3")
        _write_file(self.folder / "Vecchi" / "V1.mp3")
        _write_file(self.folder / "Vecchi" / "V2.MP3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_1_initial_scan_finds_only_root_3(self) -> None:
        files = scan_mp3_files(self.folder)
        self.assertEqual([item.name for item in files], ["Brano1.mp3", "Brano2.mp3", "Brano3.MP3"])

    def test_2_refresh_like_scan_stays_3(self) -> None:
        files_a = scan_mp3_files(self.folder)
        files_b = scan_mp3_files(self.folder)
        self.assertEqual(len(files_a), 3)
        self.assertEqual(len(files_b), 3)

    def test_3_add_root_mp3_becomes_4(self) -> None:
        _write_file(self.folder / "Brano4.mp3")
        files = scan_mp3_files(self.folder)
        self.assertEqual(len(files), 4)

    def test_4_add_subfolder_mp3_stays_3(self) -> None:
        _write_file(self.folder / "Nuovi" / "N1.mp3")
        files = scan_mp3_files(self.folder)
        self.assertEqual(len(files), 3)

    def test_5_uppercase_extension_is_included(self) -> None:
        files = scan_mp3_files(self.folder)
        names = [item.name for item in files]
        self.assertIn("Brano3.MP3", names)

    def test_6_empty_folder_count_zero(self) -> None:
        empty = self.root / "Vuota"
        empty.mkdir(parents=True, exist_ok=True)
        files = scan_mp3_files(empty)
        self.assertEqual(len(files), 0)

    def test_7_missing_folder_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            scan_mp3_files(self.root / "inesistente")

    def test_gui_scanner_uses_same_non_recursive_rule(self) -> None:
        files = MixCreatorApp.scan_mp3_files(self.folder)
        self.assertEqual(files, ["Brano1.mp3", "Brano2.mp3", "Brano3.MP3"])


if __name__ == "__main__":
    unittest.main()
