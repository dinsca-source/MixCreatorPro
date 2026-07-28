# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui import MixCreatorApp
from mp3_recovery_batch import MP3BatchOutcome


class GuiMp3RecoveryMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()
        self.app.open_mp3_recovery_window()

    def tearDown(self) -> None:
        try:
            if self.app.winfo_exists():
                self.app.destroy()
        except Exception:
            pass

    def test_elapsed_time_updates(self) -> None:
        self.app._recovery_started_at = 10.0
        self.app._recovery_current_file_started_at = 15.0

        with mock.patch("gui.time.monotonic", return_value=70.0):
            self.app._render_recovery_monitor()

        self.assertEqual(
            str(self.app._recovery_elapsed_label.cget("text")),
            "Tempo trascorso complessivo: 00:01:00",
        )
        self.assertEqual(
            str(self.app._recovery_current_file_elapsed_label.cget("text")),
            "Tempo elaborazione file corrente: 00:00:55",
        )

    def test_current_file_updates(self) -> None:
        with mock.patch("gui.time.monotonic", return_value=42.0):
            self.app._set_recovery_current_file(2, 5, "Demo.mp3")

        self.assertEqual(str(self.app._recovery_current_file_label.cget("text")), "File corrente: Demo.mp3")
        self.assertEqual(str(self.app._recovery_examined_label.cget("text")), "File esaminati: 2 / 5")
        self.assertEqual(str(self.app._recovery_completed_label.cget("text")), "File completati: 0 / 5")

    def test_current_phase_updates(self) -> None:
        self.app._set_recovery_phase("Calcolo hash problematico")
        self.assertEqual(
            str(self.app._recovery_phase_label.cget("text")),
            "Fase corrente: Calcolo hash problematico",
        )

    def test_remaining_time_after_first_completed_file(self) -> None:
        self.app._recovery_started_at = 0.0
        self.app._recovery_total_files = 4
        self.app._recovery_completed_files = 1
        self.app._recovery_completed_file_durations = [5.0]
        self.app._recovery_current_file_started_at = 6.0

        with mock.patch("gui.time.monotonic", return_value=12.0):
            self.app._render_recovery_monitor()

        self.assertEqual(
            str(self.app._recovery_eta_label.cget("text")),
            "Tempo restante stimato: 00:00:15",
        )
        self.assertEqual(
            str(self.app._recovery_percent_label.cget("text")),
            "Percentuale batch: 25%",
        )
        self.assertEqual(
            str(self.app._recovery_batch_status_label.cget("text")),
            "Stato batch: In corso",
        )

    def test_recovery_log_callback_is_thread_safe(self) -> None:
        captured: list[tuple[int, object, tuple[object, ...]]] = []
        original_after = self.app.after

        def _after(delay_ms: int, callback, *args):
            captured.append((delay_ms, callback, args))
            return "after-id"

        self.app.after = _after
        try:
            self.app._recovery_worker_log("[TECH] Fase -> Scansione frame MPEG")
        finally:
            self.app.after = original_after

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0], 0)
        self.assertIs(getattr(captured[0][1], "__self__", None), self.app)
        self.assertIs(getattr(captured[0][1], "__func__", None), type(self.app)._append_recovery_log)
        self.assertEqual(captured[0][2], ("[TECH] Fase -> Scansione frame MPEG",))

    def test_open_results_button_enables_after_session_tech_message(self) -> None:
        self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "disabled")
        self.app._recovery_allow_session_log_updates = True
        self.app._recovery_expected_output_root = "C:/tmp"
        self.app._recovery_min_session_timestamp = "2000-01-01_00-00-00"

        self.app._append_recovery_log("[TECH] Sessione esiti creata | path=C:/tmp/Diagnosi Recupero 2099-01-01_00-00-00")

        self.assertEqual(self.app._recovery_session_folder, str(Path("C:/tmp/Diagnosi Recupero 2099-01-01_00-00-00").resolve()))
        self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "normal")

    def test_completion_message_variant_recovered(self) -> None:
        result = SimpleNamespace(
            interrupted=False,
            examined_problematic=1,
            completed_problematic=1,
            processed_problematic=1,
            total_problematic=1,
            elapsed_seconds=0.5,
            output_root="C:/tmp/output",
            session_folder="C:/tmp/sessione",
            report_paths={"csv": "C:/tmp/sessione/Report/report.csv"},
            counters={
                MP3BatchOutcome.RECOVERED_TAGS.value: 1,
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
                MP3BatchOutcome.RECOVERED_FORCED.value: 0,
                MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
                MP3BatchOutcome.READ_ERROR.value: 0,
                MP3BatchOutcome.WRITE_ERROR.value: 0,
                MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
                MP3BatchOutcome.ERROR.value: 0,
            },
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_recovery_worker_completed(result)

        self.assertTrue(info_mock.called)
        text = info_mock.call_args.args[1]
        self.assertIn("Operazione completata.", text)
        self.assertIn("File recuperati: 1", text)
        self.assertIn("Esiti salvati in:", text)

    def test_completion_message_variant_no_recovery(self) -> None:
        result = SimpleNamespace(
            interrupted=False,
            examined_problematic=1,
            completed_problematic=1,
            processed_problematic=1,
            total_problematic=1,
            elapsed_seconds=0.5,
            output_root="C:/tmp/output",
            session_folder="C:/tmp/sessione",
            report_paths={"csv": "C:/tmp/sessione/Report/report.csv"},
            counters={
                MP3BatchOutcome.RECOVERED_TAGS.value: 0,
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
                MP3BatchOutcome.RECOVERED_FORCED.value: 0,
                MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 1,
                MP3BatchOutcome.READ_ERROR.value: 0,
                MP3BatchOutcome.WRITE_ERROR.value: 0,
                MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
                MP3BatchOutcome.ERROR.value: 0,
            },
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_recovery_worker_completed(result)

        text = info_mock.call_args.args[1]
        self.assertIn("Operazione completata senza file recuperati.", text)
        self.assertIn("Originali incompatibili: 1", text)

    def test_completion_message_variant_interrupted(self) -> None:
        result = SimpleNamespace(
            interrupted=True,
            examined_problematic=2,
            completed_problematic=1,
            processed_problematic=1,
            total_problematic=3,
            elapsed_seconds=0.5,
            output_root="C:/tmp/output",
            session_folder="C:/tmp/sessione",
            report_paths={"csv": "C:/tmp/sessione/Report/report.csv"},
            counters={
                MP3BatchOutcome.RECOVERED_TAGS.value: 1,
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
                MP3BatchOutcome.RECOVERED_FORCED.value: 0,
                MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
                MP3BatchOutcome.READ_ERROR.value: 0,
                MP3BatchOutcome.WRITE_ERROR.value: 0,
                MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
                MP3BatchOutcome.ERROR.value: 0,
            },
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_recovery_worker_completed(result)

        text = info_mock.call_args.args[1]
        self.assertIn("Operazione interrotta.", text)
        self.assertIn("File esaminati: 2", text)
        self.assertIn("Stato parziale salvato in:", text)

    def test_open_results_uses_exact_session_folder(self) -> None:
        expected = "C:/tmp/Diagnosi Recupero 2026-07-27_10-11-12"
        self.app._recovery_session_folder = expected

        with mock.patch("gui.Path.exists", return_value=True):
            with mock.patch("gui.os.startfile") as startfile_mock:
                self.app._open_recovery_results_folder()

        self.assertTrue(startfile_mock.called)
        called_path = startfile_mock.call_args.args[0]
        self.assertEqual(str(Path(called_path)), str(Path(expected)))

    def test_open_results_button_stays_enabled_after_completion_and_updates_next_session(self) -> None:
        first_result = SimpleNamespace(
            interrupted=False,
            examined_problematic=1,
            completed_problematic=1,
            processed_problematic=1,
            total_problematic=1,
            elapsed_seconds=0.2,
            output_root="C:/tmp/output_a",
            session_folder="C:/tmp/output_a/Diagnosi Recupero A",
            report_paths={"csv": "C:/tmp/output_a/Diagnosi Recupero A/Report/r.csv"},
            counters={
                MP3BatchOutcome.RECOVERED_TAGS.value: 1,
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
                MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
                MP3BatchOutcome.READ_ERROR.value: 0,
                MP3BatchOutcome.WRITE_ERROR.value: 0,
                MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
                MP3BatchOutcome.ERROR.value: 0,
            },
        )
        second_result = SimpleNamespace(
            interrupted=False,
            examined_problematic=1,
            completed_problematic=1,
            processed_problematic=1,
            total_problematic=1,
            elapsed_seconds=0.2,
            output_root="C:/tmp/output_b",
            session_folder="C:/tmp/output_b/Diagnosi Recupero B",
            report_paths={"csv": "C:/tmp/output_b/Diagnosi Recupero B/Report/r.csv"},
            counters={
                MP3BatchOutcome.RECOVERED_TAGS.value: 0,
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 1,
                MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
                MP3BatchOutcome.READ_ERROR.value: 0,
                MP3BatchOutcome.WRITE_ERROR.value: 0,
                MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
                MP3BatchOutcome.ERROR.value: 0,
            },
        )

        with mock.patch("gui.messagebox.showinfo"):
            self.app._handle_recovery_worker_completed(first_result)
            self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "normal")
            self.assertEqual(self.app._recovery_session_folder, first_result.session_folder)

            self.app._handle_recovery_worker_completed(second_result)
            self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "normal")
            self.assertEqual(self.app._recovery_session_folder, second_result.session_folder)

        with mock.patch("gui.Path.exists", return_value=True):
            with mock.patch("gui.os.startfile") as startfile_mock:
                self.app._open_recovery_results_folder()
        self.assertEqual(str(Path(startfile_mock.call_args.args[0])), str(Path(second_result.session_folder)))

    def test_error_message_variant_includes_session_path_when_available(self) -> None:
        self.app._recovery_session_folder = "C:/tmp/sessione_errore"
        with mock.patch("gui.messagebox.showerror") as error_mock:
            self.app._handle_recovery_worker_error("Errore simulato")

        self.assertTrue(error_mock.called)
        message_text = error_mock.call_args.args[1]
        self.assertIn("Errore simulato", message_text)
        self.assertIn("Stato parziale salvato in:", message_text)
        self.assertIn("C:/tmp/sessione_errore", message_text)

    def test_new_start_failure_before_session_creation_does_not_keep_previous_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_session = Path(temp_dir) / "Diagnosi Recupero PREV"
            previous_session.mkdir(parents=True, exist_ok=True)
            marker = previous_session / "marker.txt"
            marker.write_text("keep", encoding="utf-8")

            self.app._recovery_session_folder = str(previous_session)
            self.app._recovery_open_results_button.configure(state="normal")
            self.app._recovery_problematic_entry.delete(0, "end")
            self.app._recovery_problematic_entry.insert(0, "C:/percorso/non_esistente")
            self.app._recovery_original_entry.delete(0, "end")
            self.app._recovery_original_entry.insert(0, "C:/percorso/non_esistente_2")

            with mock.patch("gui.messagebox.showerror"):
                self.app._start_mp3_recovery()

            self.assertIsNone(self.app._recovery_session_folder)
            self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "disabled")
            self.assertTrue(previous_session.is_dir())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

            with mock.patch("gui.os.startfile") as startfile_mock:
                self.app._open_recovery_results_folder()
            self.assertFalse(startfile_mock.called)

    def test_new_session_created_replaces_previous_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            problematic = Path(temp_dir) / "problematic"
            originals = Path(temp_dir) / "originals"
            destination = Path(temp_dir) / "dest"
            problematic.mkdir(parents=True, exist_ok=True)
            originals.mkdir(parents=True, exist_ok=True)
            destination.mkdir(parents=True, exist_ok=True)
            (problematic / "a.mp3").write_bytes(b"x")
            (originals / "a.mp3").write_bytes(b"x")

            previous_session = str(destination / "Diagnosi Recupero OLD")
            self.app._recovery_session_folder = previous_session
            self.app._recovery_open_results_button.configure(state="normal")
            self.app._recovery_problematic_entry.delete(0, "end")
            self.app._recovery_problematic_entry.insert(0, str(problematic))
            self.app._recovery_original_entry.delete(0, "end")
            self.app._recovery_original_entry.insert(0, str(originals))
            self.app._recovery_output_entry.delete(0, "end")
            self.app._recovery_output_entry.insert(0, str(destination))

            with mock.patch.object(self.app.recovery_worker, "start", return_value=None):
                self.app._start_mp3_recovery()

            new_session = str(destination / "Diagnosi Recupero 2099-01-01_00-00-00")
            self.app._append_recovery_log(f"[TECH] Sessione esiti creata | path={new_session}")
            self.assertEqual(self.app._recovery_session_folder, str(Path(new_session).resolve()))
            self.assertNotEqual(self.app._recovery_session_folder, previous_session)
            self.assertEqual(str(self.app._recovery_open_results_button.cget("state")), "normal")
            self.app._stop_recovery_timer()


if __name__ == "__main__":
    unittest.main()
