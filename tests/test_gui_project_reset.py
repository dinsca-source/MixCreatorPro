# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import gui as gui_module
from gui import MixCreatorApp
from clip_info import ClipInfo
from mp3_diagnostics import STATUS_PERFECT, STATUS_REPAIRED, STATUS_UNRECOVERABLE


class _FakeAppearanceCombo:
    def __init__(self, initial: str = "System") -> None:
        self.value = str(initial)
        self.set_calls: list[str] = []

    def set(self, value: str) -> None:
        self.value = str(value)
        self.set_calls.append(self.value)

    def get(self) -> str:
        return self.value


class GuiProjectResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()
        if not hasattr(self.app, "appearance_combo"):
            self.app.appearance_combo = _FakeAppearanceCombo(
                str(self.app.settings.get("appearance_mode", "System"))
            )

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

    def test_extract_button_is_disabled_without_temporal_data(self) -> None:
        self.app.last_generated_mix_data = None
        self.app._update_controls_state()
        self.assertEqual(str(self.app.extract_song_button.cget("state")), "disabled")

    def test_diagnostics_subfolders_default_is_disabled(self) -> None:
        self.assertFalse(bool(self.app.diagnostics_include_subfolders_var.get()))

    def test_diagnostics_placement_mode_defaults_to_copy(self) -> None:
        self.assertEqual(str(self.app.diagnostics_placement_mode_var.get()), "copy")

    def test_diagnostics_winlive_defaults_are_disabled(self) -> None:
        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_winlive_var.get()))

    def test_diagnostics_defaults_restored_after_manual_change_and_reset(self) -> None:
        self.app.diagnostics_verify_mp3_integrity_var.set(False)
        self.app.diagnostics_verify_winlive_var.set(True)

        self.app._reset_to_initial_state()

        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_winlive_var.get()))

    def test_diagnostics_window_reuses_single_instance(self) -> None:
        self.app.open_diagnostics_window()
        first = self.app.diagnostics_window
        self.assertIsNotNone(first)

        self.app.open_diagnostics_window()
        second = self.app.diagnostics_window

        self.assertIs(first, second)

    def test_diagnostics_reverify_button_removed(self) -> None:
        self.app.open_diagnostics_window()
        self.assertFalse(hasattr(self.app, "diagnostics_reverify_button"))

    def test_diagnostics_winlive_checkboxes_exist_and_positioned_under_subfolders(self) -> None:
        self.app.open_diagnostics_window()
        self.assertTrue(hasattr(self.app, "diagnostics_integrity_checkbox"))
        self.assertTrue(hasattr(self.app, "diagnostics_winlive_group_label"))
        self.assertTrue(hasattr(self.app, "diagnostics_winlive_checkbox"))

        subfolders_row = int(self.app.diagnostics_subfolders_checkbox.grid_info().get("row", 0))
        integrity_row = int(self.app.diagnostics_integrity_checkbox.grid_info().get("row", 0))
        group_row = int(self.app.diagnostics_winlive_group_label.grid_info().get("row", 0))
        winlive_row = int(self.app.diagnostics_winlive_checkbox.grid_info().get("row", 0))
        output_row = int(self.app.diagnostics_output_entry.grid_info().get("row", 0))

        self.assertEqual(integrity_row, subfolders_row + 1)
        self.assertEqual(group_row, integrity_row + 1)
        self.assertEqual(winlive_row, group_row + 1)
        self.assertGreater(output_row, winlive_row)

    def test_diagnostics_winlive_toggle_keeps_only_one_option(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_verify_winlive_var.set(True)
        self.app._on_diagnostics_winlive_toggle()
        self.assertTrue(bool(self.app.diagnostics_verify_winlive_var.get()))
        self.assertTrue(self.app._diagnostics_actions_enabled())

    def test_diagnostics_both_on_is_allowed(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_verify_mp3_integrity_var.set(True)
        self.app._on_diagnostics_integrity_toggle()
        self.app.diagnostics_verify_winlive_var.set(True)
        self.app._on_diagnostics_winlive_toggle()
        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertTrue(bool(self.app.diagnostics_verify_winlive_var.get()))

    def test_turning_off_last_active_integrity_is_reverted(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_verify_winlive_var.set(False)
        self.app._on_diagnostics_winlive_toggle()
        self.app.diagnostics_verify_mp3_integrity_var.set(False)

        infos: list[str] = []
        original_showinfo = gui_module.messagebox.showinfo
        try:
            gui_module.messagebox.showinfo = lambda _title, message, **kwargs: infos.append(str(message))
            self.app._on_diagnostics_integrity_toggle()
        finally:
            gui_module.messagebox.showinfo = original_showinfo

        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_winlive_var.get()))
        self.assertIn("Almeno un controllo diagnostico deve rimanere attivo.", infos[-1])

    def test_turning_off_last_active_winlive_is_reverted(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_verify_winlive_var.set(True)
        self.app._on_diagnostics_winlive_toggle()
        self.app.diagnostics_verify_mp3_integrity_var.set(False)
        self.app._on_diagnostics_integrity_toggle()
        self.app.diagnostics_verify_winlive_var.set(False)

        infos: list[str] = []
        original_showinfo = gui_module.messagebox.showinfo
        try:
            gui_module.messagebox.showinfo = lambda _title, message, **kwargs: infos.append(str(message))
            self.app._on_diagnostics_winlive_toggle()
        finally:
            gui_module.messagebox.showinfo = original_showinfo

        self.assertTrue(bool(self.app.diagnostics_verify_winlive_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertIn("Almeno un controllo diagnostico deve rimanere attivo.", infos[-1])

    def test_diagnostics_winlive_tooltips_exist(self) -> None:
        self.app.open_diagnostics_window()
        tooltip_texts = [tip.text for tip in self.app.tooltips]
        self.assertIn(
            "Controlla la presenza e la correttezza\n"
            "dei TAG WinLive (testo e accordi)\n"
            "e include i risultati nei report.",
            tooltip_texts,
        )
        self.assertIn(
            "Controlla la presenza e la correttezza\n"
            "dei TAG WinLive (testo e accordi)\n"
            "e include i risultati nei report.",
            tooltip_texts,
        )
        self.assertIn(
            "Analizza i file selezionati, applica le correzioni disponibili quando necessarie e genera un'unica cartella di esito in base ai controlli attivati.",
            tooltip_texts,
        )

    def test_diagnostics_winlive_settings_are_saved_and_restored(self) -> None:
        saved_payload: dict[str, object] = {}
        original_save = self.app.settings_manager.save
        self.app.settings_manager.save = lambda payload: saved_payload.update(dict(payload))
        try:
            self.app.diagnostics_verify_winlive_var.set(True)
            self.app.diagnostics_verify_mp3_integrity_var.set(False)
            self.app.save_settings()
        finally:
            self.app.settings_manager.save = original_save

        self.assertFalse(bool(saved_payload.get("diagnostics_verify_mp3_integrity")))
        self.assertTrue(bool(saved_payload.get("diagnostics_verify_winlive")))
        self.assertNotIn("diagnostics_winlive_autocorrect", saved_payload)

        self.app.settings["diagnostics_verify_mp3_integrity"] = False
        self.app.settings["diagnostics_verify_winlive"] = True
        self.app.settings["appearance_mode"] = "Dark"
        self.app._load_settings_into_ui()
        self.assertEqual(self.app.appearance_combo.set_calls[-1], "Dark")
        self.app.open_diagnostics_window()
        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_winlive_var.get()))

    def test_diagnostics_winlive_autocorrect_ui_and_state_are_absent(self) -> None:
        self.app.open_diagnostics_window()
        self.assertFalse(hasattr(self.app, "diagnostics_winlive_autocorrect_var"))
        self.assertFalse(hasattr(self.app, "diagnostics_winlive_autocorrect_checkbox"))

    def test_diagnostics_invalid_saved_both_off_recovers_with_integrity_on(self) -> None:
        self.app.settings["diagnostics_verify_mp3_integrity"] = False
        self.app.settings["diagnostics_verify_winlive"] = False
        self.app.settings["appearance_mode"] = "Light"
        self.app._load_settings_into_ui()
        self.assertEqual(self.app.appearance_combo.set_calls[-1], "Light")
        self.assertTrue(bool(self.app.diagnostics_verify_mp3_integrity_var.get()))
        self.assertFalse(bool(self.app.diagnostics_verify_winlive_var.get()))

    def test_diagnostics_winlive_flags_not_propagated_when_verify_disabled(self) -> None:
        self.app.open_diagnostics_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "in"
            output_dir = Path(temp_dir) / "out"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            self.app._set_diagnostics_entry_values(input_folder=str(input_dir), output_folder=str(output_dir))
            self.app.diagnostics_verify_winlive_var.set(False)

            captured: dict[str, object] = {}
            original_start = self.app.diagnostics_worker.start
            try:
                self.app.diagnostics_worker.start = lambda **kwargs: captured.update(kwargs)
                self.app.start_diagnostics_repair()
            finally:
                self.app.diagnostics_worker.start = original_start

            self.assertIn("verify_winlive", captured)
            self.assertFalse(bool(captured["verify_winlive"]))
            self.assertIn("verify_mp3_integrity", captured)
            self.assertTrue(bool(captured["verify_mp3_integrity"]))
            self.assertNotIn("winlive_autocorrect", captured)

    def test_diagnostics_winlive_flags_are_propagated_when_enabled(self) -> None:
        self.app.open_diagnostics_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "in"
            output_dir = Path(temp_dir) / "out"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            self.app._set_diagnostics_entry_values(input_folder=str(input_dir), output_folder=str(output_dir))
            self.app.diagnostics_verify_winlive_var.set(True)
            self.app.diagnostics_verify_mp3_integrity_var.set(False)

            captured: dict[str, object] = {}
            original_start = self.app.diagnostics_worker.start
            try:
                self.app.diagnostics_worker.start = lambda **kwargs: captured.update(kwargs)
                self.app.start_diagnostics_repair()
            finally:
                self.app.diagnostics_worker.start = original_start

            self.assertFalse(bool(captured["verify_mp3_integrity"]))
            self.assertTrue(bool(captured["verify_winlive"]))
            self.assertNotIn("winlive_autocorrect", captured)

    def test_diagnostics_actions_disabled_when_both_verifications_disabled(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_verify_mp3_integrity_var.set(False)
        self.app.diagnostics_verify_winlive_var.set(False)
        self.app._update_controls_state()
        self.assertEqual(str(self.app.diagnostics_repair_button.cget("state")), "disabled")

    def test_diagnostics_winlive_completion_updates_log_and_final_stats(self) -> None:
        self.app.open_diagnostics_window()

        result_ok = SimpleNamespace(
            file_name="ok.mp3",
            winlive=SimpleNamespace(
                verifica_winlive_eseguita=True,
                stato_winlive_finale=SimpleNamespace(value="FILE GIA' OK"),
                normalizzazione_validata=False,
                errore_winlive="",
                errore_winlive_code="",
            ),
        )
        result_norm = SimpleNamespace(
            file_name="norm.mp3",
            winlive=SimpleNamespace(
                verifica_winlive_eseguita=True,
                stato_winlive_finale=SimpleNamespace(value="NORMALIZZATO"),
                normalizzazione_validata=True,
                errore_winlive="",
                errore_winlive_code="",
            ),
        )
        result_err = SimpleNamespace(
            file_name="err.mp3",
            winlive=SimpleNamespace(
                verifica_winlive_eseguita=True,
                stato_winlive_finale=SimpleNamespace(value="NON INTEGRO DOPO MODIFICA"),
                normalizzazione_validata=False,
                errore_winlive="forced failure",
                errore_winlive_code="FORCED",
            ),
        )

        info_messages: list[str] = []
        original_showinfo = gui_module.messagebox.showinfo
        try:
            gui_module.messagebox.showinfo = lambda _title, message, **kwargs: info_messages.append(str(message))
            self.app._handle_diagnostics_worker_completed(
                {
                    "summary": {
                        "category_ok_files": 1,
                        "category_repaired_files": 1,
                        "category_unrecoverable_files": 1,
                        "ignored_silent_anomalies": 0,
                        "analyzed_files": 3,
                    },
                    "report_paths": {},
                    "diagnostic_results": [result_ok, result_norm, result_err],
                }
            )
        finally:
            gui_module.messagebox.showinfo = original_showinfo

        self.assertTrue(info_messages)
        final_message = info_messages[-1]
        self.assertIn("WinLive verificati: 3", final_message)
        self.assertIn("WinLive normalizzati: 1", final_message)
        self.assertIn("Errori WinLive: 1", final_message)

        self.app.diagnostics_log_box.configure(state="normal")
        log_text = self.app.diagnostics_log_box.get("1.0", "end")
        self.app.diagnostics_log_box.configure(state="disabled")
        self.assertIn("Verifica WinLive completata.", log_text)
        self.assertIn("FILE GIA' OK", log_text)
        self.assertIn("NORMALIZZATO", log_text)
        self.assertIn("NON INTEGRO DOPO MODIFICA", log_text)

    def test_diagnostics_reverify_button_disabled_while_running(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_worker._running = True
        try:
            self.app._update_controls_state()
            self.assertEqual(str(self.app.diagnostics_repair_button.cget("state")), "disabled")
            self.assertEqual(str(self.app.diagnostics_stop_button.cget("state")), "normal")
        finally:
            self.app.diagnostics_worker._running = False
            self.app._update_controls_state()

    def test_diagnostics_close_and_reopen_restores_full_form(self) -> None:
        self.app.open_diagnostics_window()
        first_window = self.app.diagnostics_window
        self.assertIsNotNone(first_window)
        self.app._close_diagnostics_window()
        self.assertIsNone(self.app.diagnostics_window)

        self.app.open_diagnostics_window()
        second_window = self.app.diagnostics_window
        self.assertIsNotNone(second_window)
        self.assertIsNot(first_window, second_window)
        self.assertIsNotNone(self.app.diagnostics_output_entry)
        self.assertIsNotNone(self.app.diagnostics_progress)
        self.assertIsNotNone(self.app.diagnostics_log_box)
        self.assertIsNotNone(self.app.diagnostics_stop_button)

    def test_diagnostics_reopen_after_recovery_keeps_full_form(self) -> None:
        self.app.open_diagnostics_window()
        self.app._close_diagnostics_window()
        self.app.open_mp3_recovery_window()
        self.app._close_recovery_window()
        self.app.open_diagnostics_window()

        self.assertIsNotNone(self.app.diagnostics_window)
        self.assertIsNotNone(self.app.diagnostics_output_entry)
        self.assertIsNotNone(self.app.diagnostics_status_label)
        self.assertIsNotNone(self.app.diagnostics_log_box)
        self.assertEqual(str(self.app.diagnostics_repair_button.winfo_manager()), "grid")
        self.assertEqual(str(self.app.diagnostics_stop_button.winfo_manager()), "grid")

    def test_diagnostics_close_clears_window_reference_and_timer(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_timer_job = "diag-timer"
        cancelled: list[str] = []
        original_after_cancel = self.app.after_cancel

        def _after_cancel(job_id):
            cancelled.append(str(job_id))

        self.app.after_cancel = _after_cancel
        try:
            self.app._close_diagnostics_window()
        finally:
            self.app.after_cancel = original_after_cancel

        self.assertIn("diag-timer", cancelled)
        self.assertIsNone(self.app.diagnostics_timer_job)
        self.assertIsNone(self.app.diagnostics_window)

    def test_alternating_diagnostics_recovery_open_close_keeps_forms_stable(self) -> None:
        for _ in range(5):
            self.app.open_diagnostics_window()
            self.assertIsNotNone(self.app.diagnostics_repair_button)
            self.assertIsNotNone(self.app.diagnostics_stop_button)
            self.app._close_diagnostics_window()

            self.app.open_mp3_recovery_window()
            self.assertIsNotNone(self.app._recovery_start_button)
            self.assertIsNotNone(self.app._recovery_close_button)
            self.app._close_recovery_window()

    def test_diagnostics_eta_default_before_start(self) -> None:
        self.app.open_diagnostics_window()
        self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato restante: --")

    def test_diagnostics_eta_preparing_state(self) -> None:
        self.app.open_diagnostics_window()
        self.app.diagnostics_worker_start_time = time.monotonic()
        self.app.diagnostics_worker_total = 4
        self.app.diagnostics_last_progress = 0
        self.app._update_diagnostics_eta()
        self.assertRegex(self.app.diagnostics_eta_label.cget("text"), r"^Tempo stimato restante: ")
        self.assertIn("secondi", self.app.diagnostics_eta_label.cget("text"))

    def test_diagnostics_eta_formats_seconds_and_mmss(self) -> None:
        self.app.open_diagnostics_window()

        self.app.diagnostics_worker_total = 4
        self.app.diagnostics_last_progress = 3
        self.app.diagnostics_worker_start_time = time.monotonic() - 2.0
        self.app._update_diagnostics_eta()
        self.assertIn("Tempo stimato restante:", self.app.diagnostics_eta_label.cget("text"))
        self.assertIn("secondi", self.app.diagnostics_eta_label.cget("text"))

        self.app.diagnostics_worker_total = 2
        self.app.diagnostics_last_progress = 1
        self.app.diagnostics_worker_start_time = time.monotonic() - 120.0
        self.app._update_diagnostics_eta()
        self.assertRegex(self.app.diagnostics_eta_label.cget("text"), r"^Tempo stimato restante: \d{2}:\d{2}$")

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
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato restante: completato")

            self.app._handle_diagnostics_worker_cancelled("stop")
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato restante: annullato")

            self.app._handle_diagnostics_worker_error("boom")
            self.assertEqual(self.app.diagnostics_eta_label.cget("text"), "Tempo stimato restante: non disponibile")
        finally:
            gui_module.messagebox.showinfo = original_showinfo
            gui_module.messagebox.showerror = original_showerror

    def test_mix_eta_tracks_preparation_and_export_phases(self) -> None:
        self.app.start_time = time.monotonic()
        self.app.last_progress_percent = 0

        self.app._handle_worker_progress(1, 4, "Preparazione clip 1/4: song.mp3")
        preparation_eta = str(self.app.remaining_label.cget("text"))
        self.assertNotEqual(preparation_eta, "--:--:--")

        self.app._handle_worker_progress(0, 100, "Normalizzazione e composizione mix...")
        self.app._handle_worker_progress(10, 100, "Creazione mix: 10%")
        export_eta = str(self.app.remaining_label.cget("text"))

        self.assertNotEqual(export_eta, "--:--:--")
        self.assertTrue(export_eta == "calcolo in corso..." or export_eta.endswith("secondi") or ":" in export_eta)


    def test_load_mp3_list_keeps_selection_model_in_sync(self) -> None:
        self.app.input_folder = "C:/Music"
        self.app.scan_mp3_files = lambda _folder, **_kwargs: ["a.mp3", "b.mp3"]

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
