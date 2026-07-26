# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import gui as gui_module
from gui import MixCreatorApp
from mp3_diagnostics import STATUS_PERFECT, STATUS_UNRECOVERABLE
from project_manager import save_project


def _make_track(file_name: str, start_ms: int, end_ms: int, order: int) -> dict[str, object]:
    return {
        "file_name": file_name,
        "source_path": f"C:/Music/{file_name}",
        "source_start_ms": 0,
        "source_end_ms": max(1, end_ms - start_ms),
        "clip_duration_ms": max(1, end_ms - start_ms),
        "mix_start_ms": start_ms,
        "mix_end_ms": end_ms,
        "crossfade_in_ms": 0,
        "crossfade_out_ms": 0,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "mix_order": order,
        "source_mode": "calculated",
        "manual_clip": False,
    }


class GuiNrAndExtractBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()
        self.app._replace_entry(self.app.input_entry, str(self.input_dir))
        self.app._replace_entry(self.app.output_entry, str(self.output_dir))
        self.app._replace_entry(self.app.output_name_entry, "MixTest")
        self.app.input_folder = str(self.input_dir)
        self.app.output_folder = str(self.output_dir)

        self._original_showinfo = gui_module.messagebox.showinfo
        self._original_showerror = gui_module.messagebox.showerror
        self._original_showwarning = gui_module.messagebox.showwarning

        gui_module.messagebox.showinfo = lambda *args, **kwargs: None
        gui_module.messagebox.showerror = lambda *args, **kwargs: None
        gui_module.messagebox.showwarning = lambda *args, **kwargs: None

    def _write_mp3(self, relative_path: str) -> Path:
        file_path = self.input_dir / Path(relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake-mp3")
        return file_path

    def _write_integrity_index(self, status_by_relative_path: dict[str, str]) -> Path:
        report_dir = self.output_dir / "REPORT"
        report_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, object]] = []

        for relative_path, status in status_by_relative_path.items():
            absolute = (self.input_dir / Path(relative_path)).resolve()
            stat = absolute.stat()
            normalized_path = self.app._normalize_path_key(str(absolute))
            stable_key = f"{normalized_path}|{int(stat.st_size)}|{int(round(stat.st_mtime))}"
            items.append(
                {
                    "file_name": absolute.name,
                    "file_path": str(absolute),
                    "normalized_path": normalized_path,
                    "stable_key": stable_key,
                    "status": status,
                    "integrity_index": 0,
                }
            )

        index_path = report_dir / "IntegrityIndex.json"
        index_path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        return index_path

    def tearDown(self) -> None:
        gui_module.messagebox.showinfo = self._original_showinfo
        gui_module.messagebox.showerror = self._original_showerror
        gui_module.messagebox.showwarning = self._original_showwarning
        try:
            if self.app.winfo_exists():
                self.app.destroy()
        except Exception:
            pass
        self.temp.cleanup()

    def _prepare_mix_start(self, tracks: list[str], statuses: dict[str, str]) -> dict[str, object]:
        self.app.ordered_track_names = list(tracks)
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._diagnostics_integrity_by_file = {
            name: {"status": status}
            for name, status in statuses.items()
        }

        captured: dict[str, object] = {}

        def _fake_start(**kwargs):
            captured.update(kwargs)

        self.app.worker.start = _fake_start
        self.app._start_timer = lambda: None
        self.app.save_settings = lambda: None
        return captured

    def test_nr_none_no_prompt_and_mix_starts(self) -> None:
        captured = self._prepare_mix_start(["a.mp3", "b.mp3"], {"a.mp3": STATUS_PERFECT, "b.mp3": STATUS_PERFECT})
        self.app._ask_unrecoverable_mix_action = lambda files: (_ for _ in ()).throw(AssertionError("prompt should not be shown"))

        self.app.start_mix()

        self.assertIn("ordered_file_names", captured)
        self.assertEqual(captured["ordered_file_names"], ["a.mp3", "b.mp3"])

    def test_nr_visual_prefix_appears_for_unrecoverable_file(self) -> None:
        self._write_mp3("good.mp3")
        self._write_mp3("bad.mp3")
        self._write_integrity_index({"bad.mp3": STATUS_UNRECOVERABLE, "good.mp3": STATUS_PERFECT})

        self.app.load_mp3_list()
        entries = [self.app.track_list.get(i) for i in range(self.app.track_list.size())]

        self.assertTrue(any("[!NR] bad.mp3" in entry for entry in entries))
        self.assertTrue(any("good.mp3" in entry and "[!NR]" not in entry for entry in entries))

    def test_nr_prefix_persists_after_refresh(self) -> None:
        self._write_mp3("bad.mp3")
        self._write_integrity_index({"bad.mp3": STATUS_UNRECOVERABLE})

        self.app.load_mp3_list()
        self.app.refresh_input_folder()
        entries = [self.app.track_list.get(i) for i in range(self.app.track_list.size())]

        self.assertTrue(any("[!NR] bad.mp3" in entry for entry in entries))

    def test_nr_prefix_restored_after_open_project(self) -> None:
        self._write_mp3("bad.mp3")
        self._write_integrity_index({"bad.mp3": STATUS_UNRECOVERABLE})

        tracks = [
            {
                "file_name": "bad.mp3",
                "relative_path": "bad.mp3",
                "absolute_path_original": str((self.input_dir / "bad.mp3").resolve()),
                "position": 0,
                "size_bytes": None,
                "modified_timestamp": None,
                "duration_ms": None,
                "included": True,
                "in_ms": 0,
                "out_ms": 0,
                "clip_duration_ms": None,
                "has_custom_clip": False,
                "cut_mode": "inizio",
                "full_track": False,
                "clip_info": {"use_custom_clip": False, "clip_start_ms": 0, "clip_end_ms": 0},
            }
        ]
        project_path = save_project(
            self.root / "nr_prefix_project",
            self.input_dir,
            tracks,
            {
                "output_folder": str(self.output_dir),
                "output_name": "MixFinale",
                "mix_include_subfolders": False,
                "application_version": "test",
            },
        )

        self.app._open_project_from_path(str(project_path))
        entries = [self.app.track_list.get(i) for i in range(self.app.track_list.size())]
        self.assertTrue(any("[!NR] bad.mp3" in entry for entry in entries))

    def test_nr_prefix_for_unrecoverable_file_in_subfolder(self) -> None:
        self._write_mp3("Sub/bad.mp3")
        self._write_integrity_index({"Sub/bad.mp3": STATUS_UNRECOVERABLE})

        self.app.mix_include_subfolders_var.set(True)
        self.app.load_mp3_list()
        entries = [self.app.track_list.get(i) for i in range(self.app.track_list.size())]

        self.assertTrue(any("[!NR] Sub/bad.mp3" in entry for entry in entries))

    def test_nr_exclude_skips_only_for_single_generation(self) -> None:
        captured = self._prepare_mix_start(["ok.mp3", "bad.mp3"], {"ok.mp3": STATUS_PERFECT, "bad.mp3": STATUS_UNRECOVERABLE})
        self.app._ask_unrecoverable_mix_action = lambda files: "exclude"

        self.app.start_mix()

        self.assertEqual(captured["ordered_file_names"], ["ok.mp3"])
        self.assertIn("bad.mp3", self.app.ordered_track_names)

    def test_nr_include_keeps_file_and_logs_authorization(self) -> None:
        captured = self._prepare_mix_start(["ok.mp3", "bad.mp3"], {"ok.mp3": STATUS_PERFECT, "bad.mp3": STATUS_UNRECOVERABLE})
        self.app._ask_unrecoverable_mix_action = lambda files: "include"

        self.app.start_mix()

        self.assertEqual(captured["ordered_file_names"], ["ok.mp3", "bad.mp3"])
        self.app.log_box.configure(state="normal")
        text = self.app.log_box.get("1.0", "end")
        self.app.log_box.configure(state="disabled")
        self.assertIn("Inclusione autorizzata", text)

    def test_nr_cancel_stops_mix_start(self) -> None:
        called = {"start": 0}
        self._prepare_mix_start(["ok.mp3", "bad.mp3"], {"ok.mp3": STATUS_PERFECT, "bad.mp3": STATUS_UNRECOVERABLE})
        self.app.worker.start = lambda **kwargs: called.__setitem__("start", called["start"] + 1)
        self.app._start_timer = lambda: None
        self.app.save_settings = lambda: None
        self.app._ask_unrecoverable_mix_action = lambda files: "cancel"

        self.app.start_mix()

        self.assertEqual(called["start"], 0)

    def test_nr_only_files_exclude_shows_error_and_does_not_start(self) -> None:
        errors: list[str] = []
        gui_module.messagebox.showerror = lambda _title, message, **kwargs: errors.append(str(message))

        called = {"start": 0}
        self._prepare_mix_start(["bad.mp3"], {"bad.mp3": STATUS_UNRECOVERABLE})
        self.app.worker.start = lambda **kwargs: called.__setitem__("start", called["start"] + 1)
        self.app._start_timer = lambda: None
        self.app.save_settings = lambda: None
        self.app._ask_unrecoverable_mix_action = lambda files: "exclude"

        self.app.start_mix()

        self.assertEqual(called["start"], 0)
        self.assertTrue(errors)
        self.assertIn("Nessun brano disponibile", errors[0])

    def test_extract_songs_without_temporal_data_does_not_create_file(self) -> None:
        infos: list[str] = []
        gui_module.messagebox.showinfo = lambda _title, message, **kwargs: infos.append(str(message))

        self.app.last_generated_mix_data = None
        self.app.extract_songs()

        self.assertTrue(infos)
        self.assertIn("Non sono disponibili i dati temporali", infos[-1])
        self.assertFalse((self.output_dir / "Elenco_Mix.csv").exists())
        self.assertFalse((self.output_dir / "Elenco_Mix.txt").exists())

    def test_extract_songs_exports_utf8_bom_csv_and_txt_with_real_times_and_duration(self) -> None:
        self.app.last_generated_mix_data = {
            "tracks": [
                _make_track("È stata lei.mp3", 0, 62_999, 0),
                _make_track("Titolo; versione live.mp3", 62_999, 125_999, 1),
            ]
        }

        self.app.extract_songs()

        csv_path = self.output_dir / "Elenco_Mix.csv"
        txt_path = self.output_dir / "Elenco_Mix.txt"
        self.assertTrue(csv_path.is_file())
        self.assertTrue(txt_path.is_file())

        raw_csv = csv_path.read_bytes()
        raw_txt = txt_path.read_bytes()
        self.assertTrue(raw_csv.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw_txt.startswith(b"\xef\xbb\xbf"))

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))

        txt_content = txt_path.read_text(encoding="utf-8-sig")
        csv_content = csv_path.read_text(encoding="utf-8-sig")

        self.assertEqual(rows[0], ["N", "Nome Song", "Da", "A", "Durata"])
        self.assertEqual(rows[1], ["001", "È stata lei", "00:00:00", "00:01:02", "00:01:02"])
        self.assertEqual(rows[2], ["002", "Titolo; versione live", "00:01:02", "00:02:05", "00:01:03"])

        self.assertEqual(csv_content, txt_content)
        self.assertNotIn("00:01:03", rows[2][2])

        extra_mp3 = list(self.output_dir.glob("*.mp3"))
        self.assertEqual(extra_mp3, [])


if __name__ == "__main__":
    unittest.main()
