# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

import gui as gui_module
from gui import MixCreatorApp
from clip_info import ClipInfo
from mp3_diagnostics import STATUS_PERFECT, STATUS_REPAIRED, STATUS_UNRECOVERABLE
from selective_reverify import PreviousReportRow, MissingOriginalRow, SelectiveReverifySelection


class GuiProjectResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()

    def tearDown(self) -> None:
        try:
            if self.app.winfo_exists():
                self.app.destroy()
        except Exception:
            pass

    def test_tracks_count_updates_from_operational_list(self) -> None:
        self.app.ordered_track_names = ["a.mp3", "b.mp3", "c.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._refresh_track_list_box()

        self.assertEqual(self.app.track_count, 3)
        self.assertEqual(self.app.tracks_found_label.cget("text"), "Brani trovati: 3")

    def test_new_project_resets_to_initial_state(self) -> None:
        self.app.ordered_track_names = ["a.mp3", "b.mp3"]
        self.app.track_clip_info = {"a.mp3": ClipInfo(use_custom_clip=True, clip_start_ms=1000, clip_end_ms=5000)}
        self.app._replace_entry(self.app.input_entry, "C:/Temp")
        self.app._replace_entry(self.app.output_entry, "C:/Out")
        self.app._replace_entry(self.app.output_name_entry, "Custom")
        self.app.current_project_path = "C:/Temp/test.mixproject"
        self.app.project_name = "Test"
        self.app.project_dirty = True
        self.app._refresh_track_list_box()
        self.app._confirm_save_if_dirty = lambda: True

        self.app.new_project()

        self.assertEqual(self.app.ordered_track_names, [])
        self.assertEqual(self.app.track_clip_info, {})
        self.assertEqual(self.app.track_count, 0)
        self.assertEqual(self.app.tracks_found_label.cget("text"), "Brani trovati: 0")
        self.assertEqual(self.app.input_entry.get().strip(), "")
        self.assertEqual(self.app.current_project_path, None)
        self.assertFalse(self.app.project_dirty)
        self.assertEqual(self.app.project_name, "")

    def test_new_project_cancel_does_not_change_state(self) -> None:
        self.app.ordered_track_names = ["a.mp3"]
        self.app._refresh_track_list_box()
        self.app.project_dirty = True

        original = list(self.app.ordered_track_names)
        self.app._confirm_save_if_dirty = lambda: False

        self.app.new_project()

        self.assertEqual(self.app.ordered_track_names, original)
        self.assertTrue(self.app.project_dirty)

    def test_extract_song_requires_new_temporal_data(self) -> None:
        self.app.last_generated_mix_data = {
            "tracks": [
                {
                    "file_name": "a.mp3",
                    "start_ms": 1000,
                    "duration_ms": 5000,
                }
            ]
        }
        self.assertFalse(self.app._has_valid_last_mix_temporal_data())

        self.app.last_generated_mix_data = {
            "tracks": [
                {
                    "file_name": "a.mp3",
                    "source_path": "C:/Music/a.mp3",
                    "source_start_ms": 1000,
                    "source_end_ms": 6000,
                    "clip_duration_ms": 5000,
                    "mix_start_ms": 57000,
                    "mix_end_ms": 62000,
                    "crossfade_in_ms": 3000,
                    "crossfade_out_ms": 3000,
                }
            ]
        }
        self.assertTrue(self.app._has_valid_last_mix_temporal_data())

    def test_track_list_shows_mix_start_hhmmss(self) -> None:
        self.app.ordered_track_names = ["a.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app.last_generated_mix_data = {
            "tracks": [
                {
                    "file_name": "a.mp3",
                    "source_path": "C:/Music/a.mp3",
                    "source_start_ms": 0,
                    "source_end_ms": 5000,
                    "clip_duration_ms": 5000,
                    "mix_start_ms": 57000,
                    "mix_end_ms": 62000,
                    "crossfade_in_ms": 0,
                    "crossfade_out_ms": 2000,
                }
            ]
        }

        self.app._refresh_track_mix_times_index()
        self.app._refresh_track_list_box()

        first_item = self.app.track_list.get(0)
        self.assertIn("00:00:57", first_item)

    def test_extract_button_is_always_enabled_without_temporal_data(self) -> None:
        self.app.last_generated_mix_data = None
        self.app._update_controls_state()
        self.assertEqual(str(self.app.extract_song_button.cget("state")), "normal")

    def test_diagnostics_subfolders_default_is_disabled(self) -> None:
        self.assertFalse(bool(self.app.diagnostics_include_subfolders_var.get()))

    def test_diagnostics_placement_mode_defaults_to_copy(self) -> None:
        self.assertEqual(str(self.app.diagnostics_placement_mode_var.get()), "copy")

    def test_diagnostics_window_reuses_single_instance(self) -> None:
        self.app.open_diagnostics_window()
        first = self.app.diagnostics_window
        self.assertIsNotNone(first)

        self.app.open_diagnostics_window()
        second = self.app.diagnostics_window

        self.assertIs(first, second)

    def test_diagnostics_reverify_button_exists(self) -> None:
        self.app.open_diagnostics_window()
        self.assertTrue(hasattr(self.app, "diagnostics_reverify_button"))
        self.assertEqual(str(self.app.diagnostics_reverify_button.cget("text")), "Riverifica file problematici")

    def test_diagnostics_reverify_button_disabled_while_running(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_worker._running = True
        try:
            self.app._update_controls_state()
            self.assertEqual(str(self.app.diagnostics_analyze_button.cget("state")), "disabled")
            self.assertEqual(str(self.app.diagnostics_repair_button.cget("state")), "disabled")
            self.assertEqual(str(self.app.diagnostics_reverify_button.cget("state")), "disabled")
            self.assertEqual(str(self.app.diagnostics_stop_button.cget("state")), "normal")
        finally:
            self.app.diagnostics_worker._running = False
            self.app._update_controls_state()

    def test_diagnostics_eta_default_before_start(self) -> None:
        self.app.open_diagnostics_window()
        self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato: --")

    def test_diagnostics_eta_preparing_state(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_worker_start_time = time.monotonic()
        self.app.diagnostics_worker_total = 4
        self.app.diagnostics_last_progress = 0
        self.app._update_diagnostics_eta()
        self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato: calcolo in corso...")

    def test_diagnostics_eta_formats_seconds_and_mmss(self) -> None:
        self.app.open_diagnostics_window()

        self.app.diagnostics_worker_total = 4
        self.app.diagnostics_last_progress = 3
        self.app.diagnostics_worker_start_time = time.monotonic() - 2.0
        self.app._update_diagnostics_eta()
        self.assertIn("Tempo stimato:", self.app.diagnostics_eta_label.cget("text"))
        self.assertIn("secondi", self.app.diagnostics_eta_label.cget("text"))

        self.app.diagnostics_worker_total = 2
        self.app.diagnostics_last_progress = 1
        self.app.diagnostics_worker_start_time = time.monotonic() - 120.0
        self.app._update_diagnostics_eta()
        self.assertRegex(self.app.diagnostics_eta_label.cget("text"), r"^Tempo stimato: \d{2}:\d{2}$")

    def test_diagnostics_eta_final_states_on_complete_cancel_error(self) -> None:
        self.app.open_diagnostics_window()

        original_showinfo = gui_module.messagebox.showinfo
        original_showerror = gui_module.messagebox.showerror
        try:
            gui_module.messagebox.showinfo = lambda *args, **kwargs: None
            gui_module.messagebox.showerror = lambda *args, **kwargs: None

            self.app._handle_diagnostics_worker_completed(
                {
                    "summary": {
                        "category_ok_files": 1,
                        "category_repaired_files": 1,
                        "category_unrecoverable_files": 0,
                        "ignored_silent_anomalies": 0,
                        "analyzed_files": 2,
                    },
                    "report_paths": {},
                }
            )
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato: completato")

            self.app._handle_diagnostics_worker_cancelled("stop")
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato: annullato")

            self.app._handle_diagnostics_worker_error("boom")
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato: non disponibile")
        finally:
            gui_module.messagebox.showinfo = original_showinfo
            gui_module.messagebox.showerror = original_showerror

    def test_selective_reverify_missing_columns_shows_error(self) -> None:
        self.app.open_diagnostics_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "Riepilogo_File.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Stato finale file", "File"])
                writer.writeheader()
                writer.writerow({"Stato finale file": "Riparato", "File": "a.mp3"})

            original_open = gui_module.filedialog.askopenfilename
            original_showerror = gui_module.messagebox.showerror
            errors: list[str] = []
            try:
                gui_module.filedialog.askopenfilename = lambda **kwargs: str(csv_path)
                gui_module.messagebox.showerror = lambda _title, message, **kwargs: errors.append(str(message))
                self.app.start_selective_reverify()
            finally:
                gui_module.filedialog.askopenfilename = original_open
                gui_module.messagebox.showerror = original_showerror

            self.assertTrue(errors)
            self.assertIn("Colonne obbligatorie mancanti", errors[0])

    def test_selective_reverify_without_problematic_rows_shows_info(self) -> None:
        self.app.open_diagnostics_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            original_mp3 = Path(temp_dir) / "a.mp3"
            original_mp3.write_bytes(b"x")
            csv_path = Path(temp_dir) / "Riepilogo_File.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["Stato finale file", "Percorso originale", "File"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "Stato finale file": "Integro",
                        "Percorso originale": str(original_mp3),
                        "File": "a.mp3",
                    }
                )

            original_open = gui_module.filedialog.askopenfilename
            original_showinfo = gui_module.messagebox.showinfo
            infos: list[str] = []
            try:
                gui_module.filedialog.askopenfilename = lambda **kwargs: str(csv_path)
                gui_module.messagebox.showinfo = lambda _title, message, **kwargs: infos.append(str(message))
                self.app.start_selective_reverify()
            finally:
                gui_module.filedialog.askopenfilename = original_open
                gui_module.messagebox.showinfo = original_showinfo

            self.assertTrue(infos)
            self.assertEqual(infos[0], "Nessun file problematico da riverificare nel report selezionato.")

    def test_selective_mode_cleanup_on_error_and_cancel(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_run_mode = "selective_reverify"
        self.app.diagnostics_reverify_selection = object()  # type: ignore[assignment]

        original_showerror = gui_module.messagebox.showerror
        try:
            gui_module.messagebox.showerror = lambda *args, **kwargs: None
            self.app._handle_diagnostics_worker_error("boom")
        finally:
            gui_module.messagebox.showerror = original_showerror

        self.assertEqual(self.app.diagnostics_run_mode, "normal")
        self.assertIsNone(self.app.diagnostics_reverify_selection)

        self.app.diagnostics_run_mode = "selective_reverify"
        self.app.diagnostics_reverify_selection = object()  # type: ignore[assignment]
        self.app._handle_diagnostics_worker_cancelled("stop")
        self.assertEqual(self.app.diagnostics_run_mode, "normal")
        self.assertIsNone(self.app.diagnostics_reverify_selection)
        self.assertEqual(str(self.app.diagnostics_reverify_button.cget("state")), "normal")

    def test_comparative_report_includes_missing_original_row(self) -> None:
        self.app.open_diagnostics_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "REPORT"
            report_dir.mkdir(parents=True, exist_ok=True)
            summary_path = report_dir / "Riepilogo_File.csv"
            existing_original = Path(temp_dir) / "source" / "ok.mp3"
            existing_original.parent.mkdir(parents=True, exist_ok=True)
            existing_original.write_bytes(b"x")

            with summary_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "File",
                        "Percorso originale",
                        "Stato finale file",
                        "Categoria finale",
                        "Fine audio significativo",
                        "Silenzio finale (ms)",
                        "Percorso finale",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "File": "ok.mp3",
                        "Percorso originale": str(existing_original),
                        "Stato finale file": "Integro",
                        "Categoria finale": "File già rilevati OK",
                        "Fine audio significativo": "00:00:10.000",
                        "Silenzio finale (ms)": "0",
                        "Percorso finale": str(report_dir / ".." / "File già rilevati OK" / "ok.mp3"),
                    }
                )

            missing_original = str(Path(temp_dir) / "source" / "missing.mp3")
            selection = SelectiveReverifySelection(
                report_csv_path=summary_path,
                total_rows=2,
                repaired_rows=1,
                unrecoverable_rows=1,
                duplicates_excluded=0,
                valid_original_files=[existing_original],
                missing_originals=[
                    MissingOriginalRow(
                        row=PreviousReportRow(
                            file_name="missing.mp3",
                            original_path=missing_original,
                            previous_status="Riparato",
                            previous_category="File riparati",
                            previous_significant_end="",
                            previous_trailing_silence_ms="",
                        ),
                        reason="Originale non trovato",
                    )
                ],
                selected_rows=[
                    PreviousReportRow(
                        file_name="ok.mp3",
                        original_path=str(existing_original),
                        previous_status="Riparato",
                        previous_category="File riparati",
                        previous_significant_end="00:00:09.500",
                        previous_trailing_silence_ms="500",
                    ),
                    PreviousReportRow(
                        file_name="missing.mp3",
                        original_path=missing_original,
                        previous_status="Non recuperabile",
                        previous_category="Non recuperabili",
                        previous_significant_end="",
                        previous_trailing_silence_ms="",
                    ),
                ],
            )

            self.app.diagnostics_reverify_selection = selection
            comparative_path = self.app._write_selective_comparative_report({"csv_summary": str(summary_path)})

            self.assertTrue(Path(comparative_path).is_file())
            with Path(comparative_path).open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            missing_row = next(row for row in rows if row["File"] == "missing.mp3")
            self.assertEqual(missing_row["Stato nuovo"], "Originale non trovato")

            self.app.diagnostics_run_mode = "selective_reverify"
            original_showinfo = gui_module.messagebox.showinfo
            try:
                gui_module.messagebox.showinfo = lambda *args, **kwargs: None
                self.app._handle_diagnostics_worker_completed(
                    {
                        "summary": {
                            "category_ok_files": 1,
                            "category_repaired_files": 0,
                            "category_unrecoverable_files": 0,
                            "ignored_silent_anomalies": 0,
                            "analyzed_files": 1,
                        },
                        "report_paths": {"csv_summary": str(summary_path)},
                    }
                )
            finally:
                gui_module.messagebox.showinfo = original_showinfo

            self.assertEqual(self.app.diagnostics_run_mode, "normal")
            self.assertIsNone(self.app.diagnostics_reverify_selection)
            self.assertEqual(str(self.app.diagnostics_analyze_button.cget("state")), "normal")

    def test_load_mp3_list_keeps_selection_model_in_sync(self) -> None:
        self.app.input_folder = "C:/Music"
        self.app.scan_mp3_files = lambda _folder: ["a.mp3", "b.mp3"]

        self.app.load_mp3_list()

        self.app.track_list.selection_clear(0, "end")
        self.app.track_list.selection_set(0)

        self.assertEqual(self.app._selected_visible_file_name(), "a.mp3")

    def test_set_clip_with_single_filtered_selection_does_not_raise_selection_error(self) -> None:
        self.app.ordered_track_names = ["a.mp3", "b.mp3", "c.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._diagnostics_integrity_by_file = {
            "a.mp3": {"status": STATUS_PERFECT},
            "b.mp3": {"status": STATUS_REPAIRED},
            "c.mp3": {"status": STATUS_UNRECOVERABLE},
        }
        self.app.track_filter_var.set("Solo riparati")
        self.app._refresh_track_list_box()

        self.app.track_list.selection_clear(0, "end")
        self.app.track_list.selection_set(0)

        errors: list[str] = []
        original_showerror = gui_module.messagebox.showerror
        original_dialog = gui_module.ClipEditorDialog

        class _DummyDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

        try:
            gui_module.messagebox.showerror = lambda _title, message: errors.append(message)
            gui_module.ClipEditorDialog = _DummyDialog
            self.app.set_test_clip()
        finally:
            gui_module.messagebox.showerror = original_showerror
            gui_module.ClipEditorDialog = original_dialog

        self.assertEqual(errors, [])

    def test_clear_custom_clip_and_delete_work_with_filtered_selection(self) -> None:
        self.app.ordered_track_names = ["a.mp3", "b.mp3", "c.mp3"]
        self.app.track_clip_info = {
            "a.mp3": ClipInfo(),
            "b.mp3": ClipInfo(use_custom_clip=True, clip_start_ms=1000, clip_end_ms=5000),
            "c.mp3": ClipInfo(),
        }
        self.app._diagnostics_integrity_by_file = {
            "a.mp3": {"status": STATUS_PERFECT},
            "b.mp3": {"status": STATUS_REPAIRED},
            "c.mp3": {"status": STATUS_UNRECOVERABLE},
        }
        self.app.track_filter_var.set("Solo riparati")
        self.app._refresh_track_list_box()

        self.app.track_list.selection_clear(0, "end")
        self.app.track_list.selection_set(0)
        self.app.clear_custom_clip()
        self.assertFalse(self.app.track_clip_info["b.mp3"].use_custom_clip)

        self.app.track_list.selection_clear(0, "end")
        self.app.track_list.selection_set(0)
        self.app.delete_selected_track()
        self.assertNotIn("b.mp3", self.app.ordered_track_names)

    def test_move_up_down_work_with_filtered_selection(self) -> None:
        self.app.ordered_track_names = ["a.mp3", "b.mp3", "c.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._diagnostics_integrity_by_file = {
            "a.mp3": {"status": STATUS_PERFECT},
            "b.mp3": {"status": STATUS_REPAIRED},
            "c.mp3": {"status": STATUS_UNRECOVERABLE},
        }
        self.app.track_filter_var.set("Solo riparati")
        self.app._refresh_track_list_box()

        self.app.track_list.selection_clear(0, "end")
        self.app.track_list.selection_set(0)

        self.app.move_track_up()
        self.assertEqual(self.app.ordered_track_names, ["b.mp3", "a.mp3", "c.mp3"])

        self.app.move_track_down()
        self.assertEqual(self.app.ordered_track_names, ["a.mp3", "b.mp3", "c.mp3"])

    def test_sort_and_shuffle_keep_operational_model_under_filter(self) -> None:
        self.app.ordered_track_names = ["c.mp3", "a.mp3", "b.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._diagnostics_integrity_by_file = {
            "a.mp3": {"status": STATUS_PERFECT},
            "b.mp3": {"status": STATUS_REPAIRED},
            "c.mp3": {"status": STATUS_UNRECOVERABLE},
        }
        self.app.track_filter_var.set("Solo non recuperabili")
        self.app._refresh_track_list_box()

        self.app.sort_tracks_alphabetically()
        self.assertEqual(self.app.ordered_track_names, ["a.mp3", "b.mp3", "c.mp3"])

        import random
        original_shuffle = random.shuffle
        try:
            random.shuffle = lambda seq: seq.reverse()
            self.app.shuffle_track_list()
        finally:
            random.shuffle = original_shuffle

        self.assertEqual(self.app.ordered_track_names, ["c.mp3", "b.mp3", "a.mp3"])

    def test_extract_song_rows_use_real_order_not_filtered_view(self) -> None:
        self.app._replace_entry(self.app.input_entry, "C:/Music")
        self.app.ordered_track_names = ["a.mp3", "b.mp3", "c.mp3"]
        self.app._sync_clip_info(self.app.ordered_track_names)
        self.app._diagnostics_integrity_by_file = {
            "a.mp3": {"status": STATUS_PERFECT},
            "b.mp3": {"status": STATUS_REPAIRED},
            "c.mp3": {"status": STATUS_UNRECOVERABLE},
        }

        for filter_label in ("Tutti", "Solo integri", "Solo riparati", "Solo non recuperabili"):
            self.app.track_filter_var.set(filter_label)
            self.app._refresh_track_list_box()
            rows = self.app._build_song_rows_from_current_order()
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["title"] for row in rows], ["a.mp3", "b.mp3", "c.mp3"])


if __name__ == "__main__":
    unittest.main()
