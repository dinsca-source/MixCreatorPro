# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import gui as gui_module
from clip_info import ClipInfo
from gui import MixCreatorApp
from project_manager import save_project


def _write_file(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _track(relative_path: str, clip_info: ClipInfo | None = None) -> dict[str, object]:
    clip = clip_info or ClipInfo()
    return {
        "file_name": Path(relative_path).name,
        "relative_path": Path(relative_path).as_posix(),
        "absolute_path_original": "",
        "position": 0,
        "size_bytes": None,
        "modified_timestamp": None,
        "duration_ms": None,
        "included": True,
        "in_ms": int(clip.clip_start_ms),
        "out_ms": int(clip.clip_end_ms),
        "clip_duration_ms": int(clip.clip_end_ms - clip.clip_start_ms) if clip.use_custom_clip else None,
        "has_custom_clip": bool(clip.use_custom_clip),
        "cut_mode": "inizio",
        "full_track": False,
        "clip_info": clip.to_dict(),
    }


class GuiProjectOpenVariationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()

    def tearDown(self) -> None:
        try:
            if self.app.winfo_exists():
                self.app.destroy()
        except Exception:
            pass
        self.temp.cleanup()

    def _project_settings(self, *, source_folder: Path, include_subfolders: bool) -> dict[str, object]:
        return {
            "output_folder": str(source_folder),
            "output_name": "MixFinale",
            "mix_include_subfolders": include_subfolders,
            "application_version": "test",
        }

    def _save_project(self, *, source_folder: Path, tracks: list[dict[str, object]], include_subfolders: bool) -> Path:
        return save_project(
            self.root / "project_under_test",
            source_folder,
            tracks,
            self._project_settings(source_folder=source_folder, include_subfolders=include_subfolders),
        )

    def _silence_popups(self):
        original_info = gui_module.messagebox.showinfo
        original_warning = gui_module.messagebox.showwarning
        original_error = gui_module.messagebox.showerror
        gui_module.messagebox.showinfo = lambda *args, **kwargs: None
        gui_module.messagebox.showwarning = lambda *args, **kwargs: None
        gui_module.messagebox.showerror = lambda *args, **kwargs: None
        return original_info, original_warning, original_error

    def _restore_popups(self, originals) -> None:
        info, warning, error = originals
        gui_module.messagebox.showinfo = info
        gui_module.messagebox.showwarning = warning
        gui_module.messagebox.showerror = error

    def test_scan_toggle_off_on_refresh_and_restore_from_project(self) -> None:
        source = self.root / "CartellaTest"
        _write_file(source / "brano_principale.mp3")
        _write_file(source / "documento.txt")
        _write_file(source / "SottocartellaA" / "brano_A.mp3")
        _write_file(source / "SottocartellaA" / "brano_A2.MP3")
        _write_file(source / "SottocartellaB" / "brano_B.Mp3")
        _write_file(source / "Diagnostica_MP3_Test" / "file_diagnostico.mp3")

        self.app.input_folder = str(source)
        self.app._replace_entry(self.app.input_entry, str(source))

        self.app.mix_include_subfolders_var.set(False)
        self.app.load_mp3_list()
        self.assertEqual(self.app.ordered_track_names, ["brano_principale.mp3"])

        self.app.mix_include_subfolders_var.set(True)
        self.app.load_mp3_list()
        self.assertEqual(
            self.app.ordered_track_names,
            [
                "brano_principale.mp3",
                "SottocartellaA/brano_A.mp3",
                "SottocartellaA/brano_A2.MP3",
                "SottocartellaB/brano_B.Mp3",
            ],
        )
        self.assertNotIn("Diagnostica_MP3_Test/file_diagnostico.mp3", self.app.ordered_track_names)

        self.app.refresh_input_folder()
        self.assertEqual(len(self.app.ordered_track_names), 4)

        tracks = [_track(name) for name in self.app.ordered_track_names]
        project_path = self._save_project(source_folder=source, tracks=tracks, include_subfolders=True)

        self.app.new_project()

        originals = self._silence_popups()
        ask_calls: list[int] = []
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: ask_calls.append(1) or "keep"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(ask_calls, [])
        self.assertTrue(bool(self.app.mix_include_subfolders_var.get()))
        self.assertEqual(len(self.app.ordered_track_names), 4)

        self.app.mix_include_subfolders_var.set(False)
        self.app.refresh_input_folder()
        self.assertEqual(self.app.ordered_track_names, ["brano_principale.mp3"])

    def test_open_project_invariant_no_warning(self) -> None:
        source = self.root / "A"
        _write_file(source / "a.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3")], include_subfolders=False)

        originals = self._silence_popups()
        ask_calls: list[int] = []
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: ask_calls.append(1) or "keep"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(ask_calls, [])
        self.assertEqual(self.app.ordered_track_names, ["a.mp3"])

    def test_open_project_detects_added_file(self) -> None:
        source = self.root / "B"
        _write_file(source / "a.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3")], include_subfolders=False)
        _write_file(source / "b.mp3")

        originals = self._silence_popups()
        payload: dict[str, list[str]] = {"added": [], "missing": []}
        try:
            self.app._ask_project_source_variation_action = (
                lambda **kwargs: payload.update({"added": kwargs["added_files"], "missing": kwargs["missing_files"]}) or "keep"
            )
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(len(payload["added"]), 1)
        self.assertEqual(len(payload["missing"]), 0)

    def test_open_project_detects_missing_file(self) -> None:
        source = self.root / "C"
        _write_file(source / "a.mp3")
        _write_file(source / "b.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3"), _track("b.mp3")], include_subfolders=False)
        (source / "b.mp3").unlink()

        originals = self._silence_popups()
        payload: dict[str, list[str]] = {"added": [], "missing": []}
        try:
            self.app._ask_project_source_variation_action = (
                lambda **kwargs: payload.update({"added": kwargs["added_files"], "missing": kwargs["missing_files"]}) or "keep"
            )
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(len(payload["added"]), 0)
        self.assertEqual(len(payload["missing"]), 1)

    def test_open_project_detects_added_and_missing(self) -> None:
        source = self.root / "D"
        _write_file(source / "a.mp3")
        _write_file(source / "b.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3"), _track("b.mp3")], include_subfolders=False)
        (source / "b.mp3").unlink()
        _write_file(source / "c.mp3")

        originals = self._silence_popups()
        payload: dict[str, list[str]] = {"added": [], "missing": []}
        try:
            self.app._ask_project_source_variation_action = (
                lambda **kwargs: payload.update({"added": kwargs["added_files"], "missing": kwargs["missing_files"]}) or "keep"
            )
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(len(payload["added"]), 1)
        self.assertEqual(len(payload["missing"]), 1)

    def test_open_project_subfolder_change_ignored_when_setting_off(self) -> None:
        source = self.root / "E"
        _write_file(source / "a.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3")], include_subfolders=False)
        _write_file(source / "Sub" / "new.mp3")

        originals = self._silence_popups()
        ask_calls: list[int] = []
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: ask_calls.append(1) or "keep"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(ask_calls, [])

    def test_open_project_subfolder_change_detected_when_setting_on(self) -> None:
        source = self.root / "F"
        _write_file(source / "a.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3")], include_subfolders=True)
        _write_file(source / "Sub" / "new.mp3")

        originals = self._silence_popups()
        payload: dict[str, list[str]] = {"added": [], "missing": []}
        try:
            self.app._ask_project_source_variation_action = (
                lambda **kwargs: payload.update({"added": kwargs["added_files"], "missing": kwargs["missing_files"]}) or "keep"
            )
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertIn("Sub/new.mp3", payload["added"])

    def test_open_project_same_basename_in_two_subfolders_are_distinct(self) -> None:
        source = self.root / "G"
        _write_file(source / "root.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("root.mp3")], include_subfolders=True)
        _write_file(source / "SottocartellaA" / "brano.mp3")
        _write_file(source / "SottocartellaB" / "brano.mp3")

        originals = self._silence_popups()
        payload: dict[str, list[str]] = {"added": [], "missing": []}
        try:
            self.app._ask_project_source_variation_action = (
                lambda **kwargs: payload.update({"added": kwargs["added_files"], "missing": kwargs["missing_files"]}) or "keep"
            )
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertIn("SottocartellaA/brano.mp3", payload["added"])
        self.assertIn("SottocartellaB/brano.mp3", payload["added"])
        self.assertEqual(len(payload["added"]), 2)

    def test_keep_choice_preserves_project_order_and_clip(self) -> None:
        source = self.root / "H"
        _write_file(source / "one.mp3")
        _write_file(source / "two.mp3")
        saved_clip = ClipInfo(use_custom_clip=True, clip_start_ms=1000, clip_end_ms=4000)
        project_path = self._save_project(
            source_folder=source,
            tracks=[_track("one.mp3", saved_clip), _track("two.mp3")],
            include_subfolders=False,
        )
        (source / "two.mp3").unlink()
        _write_file(source / "new.mp3")

        originals = self._silence_popups()
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: "keep"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(self.app.ordered_track_names, ["one.mp3"])
        self.assertNotIn("new.mp3", self.app.ordered_track_names)
        self.assertTrue(self.app.track_clip_info["one.mp3"].use_custom_clip)
        self.assertEqual(self.app.track_clip_info["one.mp3"].clip_start_ms, 1000)
        self.assertEqual(self.app.track_clip_info["one.mp3"].clip_end_ms, 4000)

    def test_refresh_choice_updates_list_and_preserves_existing_clip(self) -> None:
        source = self.root / "I"
        _write_file(source / "one.mp3")
        _write_file(source / "two.mp3")
        saved_clip = ClipInfo(use_custom_clip=True, clip_start_ms=1200, clip_end_ms=4200)
        project_path = self._save_project(
            source_folder=source,
            tracks=[_track("one.mp3", saved_clip), _track("two.mp3")],
            include_subfolders=False,
        )
        (source / "two.mp3").unlink()
        _write_file(source / "new.mp3")

        originals = self._silence_popups()
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: "refresh"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertIn("one.mp3", self.app.ordered_track_names)
        self.assertIn("new.mp3", self.app.ordered_track_names)
        self.assertNotIn("two.mp3", self.app.ordered_track_names)
        self.assertTrue(self.app.track_clip_info["one.mp3"].use_custom_clip)
        self.assertEqual(self.app.track_clip_info["one.mp3"].clip_start_ms, 1200)
        self.assertEqual(self.app.track_clip_info["one.mp3"].clip_end_ms, 4200)

    def test_cancel_choice_keeps_previous_gui_state(self) -> None:
        source = self.root / "J"
        _write_file(source / "a.mp3")
        project_path = self._save_project(source_folder=source, tracks=[_track("a.mp3")], include_subfolders=False)
        _write_file(source / "new.mp3")

        self.app.ordered_track_names = ["preexisting.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._refresh_track_list_box()

        originals = self._silence_popups()
        try:
            self.app._ask_project_source_variation_action = lambda **kwargs: "cancel"
            self.app._open_project_from_path(str(project_path))
        finally:
            self._restore_popups(originals)

        self.assertEqual(self.app.ordered_track_names, ["preexisting.mp3"])


if __name__ == "__main__":
    unittest.main()
