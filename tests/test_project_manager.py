# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from project_manager import (
    PROJECT_FORMAT,
    ProjectValidationError,
    ProjectVersionError,
    load_project,
    resolve_project_files,
    save_project,
    validate_project
)


def _write_dummy_mp3(path: Path, size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


def _track_dict(relative_path: str, clip_start: int = 0, clip_end: int = 0, custom: bool = False) -> dict:
    return {
        "file_name": Path(relative_path).name,
        "relative_path": relative_path,
        "absolute_path_original": "",
        "position": 0,
        "size_bytes": 64,
        "modified_timestamp": 0.0,
        "duration_ms": None,
        "included": True,
        "in_ms": clip_start,
        "out_ms": clip_end,
        "clip_duration_ms": (clip_end - clip_start) if custom else None,
        "has_custom_clip": custom,
        "cut_mode": "inizio",
        "full_track": False,
        "clip_info": {
            "use_custom_clip": custom,
            "clip_start_ms": clip_start,
            "clip_end_ms": clip_end,
        },
    }


class ProjectManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Cartella Spazio à"
        self.source.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _base_settings(self) -> dict:
        return {
            "clip_seconds": 60,
            "crossfade_seconds": 3,
            "fade_in_seconds": 1,
            "fade_out_seconds": 1,
            "bitrate": "320k",
            "cut_mode": "inizio",
            "random_order": False,
            "normalize_audio": True,
            "continue_short_tracks": False,
            "output_name": "MixFinale",
            "output_folder": str(self.root / "output"),
            "application_version": "test"
        }

    def test_save_and_load_roundtrip(self) -> None:
        _write_dummy_mp3(self.source / "a.mp3")
        tracks = [_track_dict("a.mp3")]

        project_path = save_project(self.root / "test", self.source, tracks, self._base_settings())
        data = load_project(project_path)

        self.assertEqual(data["format"], PROJECT_FORMAT)
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["tracks"]), 1)

    def test_order_is_restored(self) -> None:
        _write_dummy_mp3(self.source / "uno.mp3")
        _write_dummy_mp3(self.source / "due.mp3")

        tracks = [_track_dict("due.mp3"), _track_dict("uno.mp3")]
        project_path = save_project(self.root / "order", self.source, tracks, self._base_settings())

        result = resolve_project_files(load_project(project_path))
        self.assertEqual([item["file_name"] for item in result.tracks[:2]], ["due.mp3", "uno.mp3"])

    def test_clip_in_out_are_restored(self) -> None:
        _write_dummy_mp3(self.source / "clip.mp3")
        tracks = [_track_dict("clip.mp3", clip_start=5000, clip_end=12000, custom=True)]
        project_path = save_project(self.root / "clip", self.source, tracks, self._base_settings())

        result = resolve_project_files(load_project(project_path))
        clip_info = result.tracks[0]["clip_info"]
        self.assertTrue(clip_info.use_custom_clip)
        self.assertEqual(clip_info.clip_start_ms, 5000)
        self.assertEqual(clip_info.clip_end_ms, 12000)

    def test_global_settings_are_restored(self) -> None:
        _write_dummy_mp3(self.source / "s.mp3")
        settings = self._base_settings()
        settings["clip_seconds"] = 90
        settings["output_name"] = "Matrimonio"

        project_path = save_project(self.root / "settings", self.source, [_track_dict("s.mp3")], settings)
        result = resolve_project_files(load_project(project_path))

        self.assertEqual(result.settings["clip_seconds"], 90)
        self.assertEqual(result.settings["output_name"], "Matrimonio")

    def test_missing_file_does_not_block_load(self) -> None:
        _write_dummy_mp3(self.source / "present.mp3")
        tracks = [_track_dict("present.mp3"), _track_dict("missing.mp3")]
        project_path = save_project(self.root / "missing", self.source, tracks, self._base_settings())

        result = resolve_project_files(load_project(project_path))
        self.assertEqual(len(result.missing_files), 1)
        self.assertEqual(result.tracks[0]["file_name"], "present.mp3")

    def test_new_mp3_added_is_appended(self) -> None:
        _write_dummy_mp3(self.source / "a.mp3")
        project_path = save_project(self.root / "newfiles", self.source, [_track_dict("a.mp3")], self._base_settings())

        _write_dummy_mp3(self.source / "zeta.mp3")
        result = resolve_project_files(load_project(project_path))

        self.assertIn("zeta.mp3", result.new_files)
        self.assertEqual(result.tracks[-1]["file_name"], "zeta.mp3")

    def test_modified_file_is_reported(self) -> None:
        mp3 = self.source / "mod.mp3"
        _write_dummy_mp3(mp3, size=64)
        stat = mp3.stat()

        track = _track_dict("mod.mp3")
        track["size_bytes"] = int(stat.st_size)
        track["modified_timestamp"] = float(stat.st_mtime)

        project_path = save_project(self.root / "modified", self.source, [track], self._base_settings())

        time.sleep(0.02)
        _write_dummy_mp3(mp3, size=96)

        result = resolve_project_files(load_project(project_path))
        self.assertIn("mod.mp3", result.modified_files)

    def test_moved_folder_can_be_resolved(self) -> None:
        _write_dummy_mp3(self.source / "song.mp3")
        project_path = save_project(self.root / "moved", self.source, [_track_dict("song.mp3")], self._base_settings())

        new_source = self.root / "Nuova Cartella"
        new_source.mkdir(parents=True, exist_ok=True)
        _write_dummy_mp3(new_source / "song.mp3")

        result = resolve_project_files(load_project(project_path), selected_folder=new_source)
        self.assertEqual(result.source_folder, str(new_source.resolve()))
        self.assertEqual(result.tracks[0]["file_name"], "song.mp3")

    def test_invalid_json_is_rejected(self) -> None:
        bad_path = self.root / "bad.mixproject"
        bad_path.write_text("{ invalid", encoding="utf-8")

        with self.assertRaises(ProjectValidationError):
            load_project(bad_path)

    def test_future_version_is_rejected(self) -> None:
        data = {
            "format": PROJECT_FORMAT,
            "version": 99,
            "source_folder": str(self.source),
            "settings": {},
            "tracks": []
        }
        with self.assertRaises(ProjectVersionError):
            validate_project(data)

    def test_atomic_save_preserves_created_at(self) -> None:
        _write_dummy_mp3(self.source / "a.mp3")
        project_path = save_project(self.root / "atomic", self.source, [_track_dict("a.mp3")], self._base_settings())
        first = load_project(project_path)

        time.sleep(0.02)
        save_project(project_path, self.source, [_track_dict("a.mp3")], self._base_settings())
        second = load_project(project_path)

        self.assertEqual(first["created_at"], second["created_at"])
        self.assertNotEqual(first["modified_at"], second["modified_at"])

    def test_duplicate_names_in_different_folders_are_safe(self) -> None:
        _write_dummy_mp3(self.source / "A" / "song.mp3")
        _write_dummy_mp3(self.source / "B" / "song.mp3")

        settings = self._base_settings()
        tracks = [{
            "file_name": "song.mp3",
            "relative_path": "",
            "absolute_path_original": "",
            "position": 0,
            "size_bytes": 64,
            "modified_timestamp": 0.0,
            "duration_ms": None,
            "included": True,
            "in_ms": 0,
            "out_ms": 0,
            "clip_duration_ms": None,
            "has_custom_clip": False,
            "cut_mode": "inizio",
            "full_track": False,
            "clip_info": {"use_custom_clip": False, "clip_start_ms": 0, "clip_end_ms": 0}
        }]

        project_path = save_project(self.root / "dup", self.source, tracks, settings)
        result = resolve_project_files(load_project(project_path))

        self.assertEqual(len(result.tracks), 0)
        self.assertEqual(len(result.missing_files), 1)

    def test_subfolder_relative_path_is_supported_when_recursive_enabled(self) -> None:
        _write_dummy_mp3(self.source / "top.mp3")
        _write_dummy_mp3(self.source / "sub" / "nested.mp3")

        project_path = save_project(
            self.root / "nested_rel",
            self.source,
            [_track_dict("sub/nested.mp3")],
            self._base_settings()
        )

        result = resolve_project_files(
            load_project(project_path),
            include_subfolders=True,
            auto_append_new=False,
        )

        self.assertEqual(result.missing_files, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.tracks), 1)
        self.assertEqual(result.tracks[0]["relative_path"], "sub/nested.mp3")


if __name__ == "__main__":
    unittest.main()
