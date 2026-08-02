# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mp3_repertory_new_tracks import (
    NewTrackItem,
    NewTracksAssignmentModel,
    RepertoryFolderItem,
    STATUS_DA_GESTIRE,
    STATUS_GESTITO,
    ensure_folder_available,
    list_new_tracks_non_recursive,
    scan_repertory_folders_non_recursive_stats,
)


class Mp3RepertoryNewTracksTests(unittest.TestCase):
    def test_list_new_tracks_non_recursive_filters_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "zeta.mp3").write_bytes(b"z")
            (root / "Alpha.MP3").write_bytes(b"a")
            (root / "notes.txt").write_text("x", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "inside.mp3").write_bytes(b"n")

            rows = list_new_tracks_non_recursive(root)

        self.assertEqual([item.file_name for item in rows], ["Alpha.MP3", "zeta.mp3"])
        self.assertTrue(all(item.status == STATUS_DA_GESTIRE for item in rows))

    def test_scan_repertory_folders_non_recursive_stats_direct_counts_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "root.mp3").write_bytes(b"12")
            child = root / "A"
            child.mkdir()
            (child / "a1.mp3").write_bytes(b"1234")
            (child / "a2.txt").write_text("x", encoding="utf-8")
            grandchild = child / "B"
            grandchild.mkdir()
            (grandchild / "b1.mp3").write_bytes(b"123")

            rows = scan_repertory_folders_non_recursive_stats(root)

        by_relative = {row.relative_path: row for row in rows}
        self.assertIn("", by_relative)
        self.assertIn("A", by_relative)
        self.assertIn("A/B", by_relative)
        self.assertEqual(by_relative[""].direct_mp3_count, 1)
        self.assertEqual(by_relative["A"].direct_mp3_count, 1)
        self.assertEqual(by_relative["A/B"].direct_mp3_count, 1)
        self.assertGreater(by_relative["A"].direct_mp3_size_bytes, 0)

    def test_model_assignment_remove_and_visibility(self) -> None:
        model = NewTracksAssignmentModel()
        tracks = [
            NewTrackItem(source_path="C:/music/a.mp3", file_name="a.mp3"),
            NewTrackItem(source_path="C:/music/b.mp3", file_name="b.mp3"),
        ]
        folders = [
            RepertoryFolderItem(relative_path="", full_path="C:/rep", folder_name="ROOT", direct_mp3_count=0, direct_mp3_size_bytes=0),
            RepertoryFolderItem(relative_path="Balli", full_path="C:/rep/Balli", folder_name="Balli", direct_mp3_count=2, direct_mp3_size_bytes=2048),
        ]
        model.load_tracks(tracks)
        model.load_folders(folders)

        model.assign_tracks(["C:/music/a.mp3"], ["Balli"])
        first = model.get_track("C:/music/a.mp3")
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.status, STATUS_GESTITO)
        self.assertEqual(first.destinations, ("Balli",))
        self.assertIn("Balli", model.destination_labels_for_track("C:/music/a.mp3"))

        visible_hidden = model.get_visible_tracks(show_managed=False)
        self.assertEqual([item.file_name for item in visible_hidden], ["b.mp3"])

        model.remove_assignments(["C:/music/a.mp3"])
        first_after = model.get_track("C:/music/a.mp3")
        self.assertIsNotNone(first_after)
        assert first_after is not None
        self.assertEqual(first_after.status, STATUS_DA_GESTIRE)
        self.assertEqual(first_after.destinations, ())

    def test_model_blocks_multiselect_if_one_already_managed(self) -> None:
        model = NewTracksAssignmentModel()
        model.load_tracks(
            [
                NewTrackItem(source_path="C:/music/a.mp3", file_name="a.mp3"),
                NewTrackItem(source_path="C:/music/b.mp3", file_name="b.mp3"),
            ]
        )
        model.assign_tracks(["C:/music/a.mp3"], ["Balli"])

        with self.assertRaises(ValueError):
            model.assign_tracks(["C:/music/a.mp3", "C:/music/b.mp3"], ["Lenti"])

    def test_destination_labels_show_root_and_disambiguate_same_leaf(self) -> None:
        model = NewTracksAssignmentModel()
        model.load_tracks([
            NewTrackItem(source_path="C:/music/a.mp3", file_name="a.mp3"),
        ])
        model.load_folders(
            [
                RepertoryFolderItem(relative_path="", full_path="C:/rep", folder_name="ROOT", direct_mp3_count=0, direct_mp3_size_bytes=0),
                RepertoryFolderItem(relative_path="Italiano/Classici", full_path="C:/rep/Italiano/Classici", folder_name="Classici", direct_mp3_count=1, direct_mp3_size_bytes=1),
                RepertoryFolderItem(relative_path="Estero/Classici", full_path="C:/rep/Estero/Classici", folder_name="Classici", direct_mp3_count=1, direct_mp3_size_bytes=1),
                RepertoryFolderItem(relative_path="Balli", full_path="C:/rep/Balli", folder_name="Balli", direct_mp3_count=1, direct_mp3_size_bytes=1),
            ]
        )

        model.assign_tracks(["C:/music/a.mp3"], [".", "Italiano/Classici", "Estero/Classici", "Balli"])
        rendered = model.destination_labels_for_track("C:/music/a.mp3")
        self.assertIn("ROOT", rendered)
        self.assertIn("Balli", rendered)
        self.assertIn("Italiano\\Classici", rendered)
        self.assertIn("Estero\\Classici", rendered)

    def test_ensure_folder_available_creates_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "new_folder"
            self.assertFalse(target.exists())
            resolved = ensure_folder_available(target)
            self.assertTrue(target.exists())
            self.assertTrue(target.is_dir())
            self.assertEqual(resolved.resolve(), target.resolve())


if __name__ == "__main__":
    unittest.main()
