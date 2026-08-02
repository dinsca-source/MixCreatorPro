# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
import tempfile
import time
import os
import gc
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gui as gui_module
from gui import MixCreatorApp
from mp3_repertory_new_tracks import NewTrackItem, RepertoryFolderItem
from mp3_repertory_new_tracks_update import Rep003UpdateResult


class _FakeRepertoryWorker:
    def __init__(self, running: bool = False) -> None:
        self._running = running
        self.start_calls = 0
        self.cancel_calls = 0
        self.submitted: list[tuple[str, str]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, **kwargs) -> None:
        _ = kwargs
        self.start_calls += 1
        self._running = True

    def cancel(self) -> None:
        self.cancel_calls += 1

    def submit_decision(self, request_id: str, decision: str) -> bool:
        self.submitted.append((request_id, decision))
        return True


class _FakeRep003Worker:
    def __init__(self) -> None:
        self._running = False
        self.start_calls: list[dict[str, object]] = []
        self.cancel_calls = 0
        self.submitted: list[tuple[str, str]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, **kwargs) -> None:
        self.start_calls.append(kwargs)
        self._running = True

    def cancel(self) -> None:
        self.cancel_calls += 1

    def submit_decision(self, request_id: str, decision: str) -> bool:
        self.submitted.append((request_id, decision))
        return True


class _FakeClosableWorker:
    def __init__(self, running: bool) -> None:
        self._running = running
        self.cancel_calls = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancel_calls += 1
        self._running = False


class GuiRepertoryOrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._settings_temp_dir = tempfile.TemporaryDirectory()
        self._orig_localappdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._settings_temp_dir.name

        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()

        self._orig_askyesno = gui_module.messagebox.askyesno
        self._orig_showinfo = gui_module.messagebox.showinfo
        self._orig_showwarning = gui_module.messagebox.showwarning
        self._orig_showerror = gui_module.messagebox.showerror
        gui_module.messagebox.askyesno = lambda *args, **kwargs: True
        gui_module.messagebox.showinfo = lambda *args, **kwargs: None
        gui_module.messagebox.showwarning = lambda *args, **kwargs: None
        gui_module.messagebox.showerror = lambda *args, **kwargs: None

    def tearDown(self) -> None:
        cleanup_errors: list[Exception] = []
        app = getattr(self, "app", None)
        try:
            if app is not None:
                try:
                    grab_widget = app.grab_current()
                except (tk.TclError, RuntimeError, AttributeError):
                    grab_widget = None
                if grab_widget is not None:
                    try:
                        grab_widget.grab_release()
                    except (tk.TclError, RuntimeError):
                        pass

                if getattr(app, "_repertory_dialog", None) is not None:
                    try:
                        app._finalize_repertory_window_close()
                    except (tk.TclError, RuntimeError) as exc:
                        cleanup_errors.append(exc)

                if getattr(app, "_rep003_decision_dialog", None) is not None:
                    try:
                        app._close_rep003_decision_dialog()
                    except (tk.TclError, RuntimeError) as exc:
                        cleanup_errors.append(exc)

                if getattr(app, "_rep003_window", None) is not None:
                    try:
                        app._close_rep003_window()
                    except (tk.TclError, RuntimeError) as exc:
                        cleanup_errors.append(exc)

                try:
                    if app.winfo_exists():
                        app.update_idletasks()
                        app.destroy()
                except (tk.TclError, RuntimeError) as exc:
                    cleanup_errors.append(exc)
                except Exception as exc:
                    cleanup_errors.append(exc)
        finally:
            self.app = None
            gc.collect()
            gui_module.messagebox.askyesno = self._orig_askyesno
            gui_module.messagebox.showinfo = self._orig_showinfo
            gui_module.messagebox.showwarning = self._orig_showwarning
            gui_module.messagebox.showerror = self._orig_showerror
            if getattr(self, "_orig_localappdata", None) is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = str(self._orig_localappdata)
            settings_temp_dir = getattr(self, "_settings_temp_dir", None)
            if settings_temp_dir is not None:
                settings_temp_dir.cleanup()

        if cleanup_errors:
            raise AssertionError(f"Unexpected teardown cleanup errors: {cleanup_errors!r}")

    def _collect_widget_texts(self, root) -> list[str]:
        texts: list[str] = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                texts.append(str(current.cget("text")))
            except Exception:
                pass
            try:
                stack.extend(list(current.winfo_children()))
            except Exception:
                pass
        return texts

    @staticmethod
    def _parse_geometry_size(geometry: str) -> tuple[int, int]:
        size_part = geometry.split("+", 1)[0]
        width_text, _, height_text = size_part.partition("x")
        return int(width_text), int(height_text)

    def _rep003_iid_for_relative(self, relative_path: str) -> str:
        normalized = str(relative_path or "").strip().replace("\\", "/").strip("/")
        target = "" if normalized in {"", "."} else normalized
        for iid, relative in self.app._rep003_folder_iid_by_relative.items():
            if str(relative or "") == target:
                return str(iid)
        raise AssertionError(f"Relative path not found in folders tree: {relative_path}")

    def _find_button_by_text(self, root, text: str):
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                if str(current.cget("text")) == text:
                    return current
            except Exception:
                pass
            try:
                stack.extend(list(current.winfo_children()))
            except Exception:
                pass
        return None

    def test_t_gui_prevents_double_start(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        self.app._start_repertory_organization()
        self.assertEqual(fake_worker.start_calls, 0)

    def test_u_gui_closable_after_interruption(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        self.app._close_repertory_window()
        self.assertEqual(fake_worker.cancel_calls, 1)
        self.assertIsNotNone(self.app._repertory_dialog)

        fake_worker._running = False
        result = SimpleNamespace(
            interrupted=True,
            processed_source_files=1,
            total_source_files=3,
            counters={},
            session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            log_path="x.log",
        )
        with mock.patch("gui.messagebox.showinfo"):
            self.app._handle_repertory_worker_completed(result)

        self.app._close_repertory_window()
        self.assertIsNone(self.app._repertory_dialog)

    def test_v_open_results_button_enabled_after_session_created(self) -> None:
        self.app.open_repertory_organizer_window()
        self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = root / "Organizzazione_Repertorio_20990101_010101"
            session.mkdir()
            self.app._repertory_allow_session_log_updates = True
            self.app._repertory_expected_output_root = str(root)
            self.app._repertory_min_session_timestamp = "20000101_000000"

            self.app._append_repertory_log(f"[TECH] Sessione esiti creata | path={session}")

            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

    def test_x_close_equivalent_to_skip_current(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        payload = {
            "request_id": "req-1",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        self.assertIsNotNone(self.app._repertory_decision_dialog)

        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)
        close_handler = dialog.protocol("WM_DELETE_WINDOW")
        if close_handler:
            dialog.tk.call(close_handler)
        else:
            self.app._submit_repertory_decision("SKIP_CURRENT")
        self.assertEqual(fake_worker.submitted, [("req-1", "SKIP_CURRENT")])

    def test_bypass_session_decision_updates_flag(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker
        payload = {
            "request_id": "req-2",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        self.app._submit_repertory_decision("UPDATE_AND_BYPASS_SESSION")
        self.assertTrue(self.app._repertory_mtime_bypass_active)
        self.assertEqual(self.app._repertory_mtime_session_choice, "UPDATE_ALL")
        self.assertEqual(fake_worker.submitted, [("req-2", "UPDATE_AND_BYPASS_SESSION")])

    def test_keep_all_session_decision_updates_flag(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker
        payload = {
            "request_id": "req-keep-all",
            "source_name": "B.mp3",
            "source_path": "C:/src/B.mp3",
            "source_size": 120,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "B.mp3",
            "destination_path": "C:/dst/B.mp3",
            "destination_size": 140,
            "destination_mtime_human": "2026-01-01 11:00:00",
            "mtime_delta_human": "1 ora",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        self.app._submit_repertory_decision("SKIP_AND_BYPASS_SESSION")
        self.assertTrue(self.app._repertory_mtime_bypass_active)
        self.assertEqual(self.app._repertory_mtime_session_choice, "SKIP_ALL")
        self.assertEqual(fake_worker.submitted, [("req-keep-all", "SKIP_AND_BYPASS_SESSION")])

    def test_global_session_choice_reused_without_reopening_dialog(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        first_payload = {
            "request_id": "req-global-first",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
        }
        self.app._handle_repertory_worker_decision_required(first_payload)
        self.assertIsNotNone(self.app._repertory_decision_dialog)
        self.app._submit_repertory_decision("UPDATE_AND_BYPASS_SESSION")
        self.assertIsNone(self.app._repertory_decision_dialog)

        second_payload = {
            "request_id": "req-global-second",
            "source_name": "B.mp3",
            "source_path": "C:/src/B.mp3",
            "source_size": 120,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "B.mp3",
            "destination_path": "C:/dst/B.mp3",
            "destination_size": 130,
            "destination_mtime_human": "2026-01-01 12:00:00",
        }
        self.app._handle_repertory_worker_decision_required(second_payload)
        self.assertIsNone(self.app._repertory_decision_dialog)
        self.assertEqual(
            fake_worker.submitted,
            [
                ("req-global-first", "UPDATE_AND_BYPASS_SESSION"),
                ("req-global-second", "UPDATE_AND_BYPASS_SESSION"),
            ],
        )

    def test_dialog_shows_summary_text(self) -> None:
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-delta",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime": 1000.0,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime": 4600.0,
            "destination_mtime_human": "2026-01-01 11:00:00",
            "mtime_delta_compact": "1 ora",
            "comparison_reason": "File del repertorio piu recente",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)

        all_text = "\n".join(self._collect_widget_texts(dialog))
        self.assertIn("BLOCCO FILE AGGIORNAMENTI", all_text)
        self.assertIn("BLOCCO FILE REPERTORIO", all_text)
        self.assertIn("piu vecchio", all_text)
        self.assertIn("1 ora", all_text)
        self.assertIn("Differenza temporale:", all_text)
        self.assertIn("Motivo confronto:", all_text)

    def test_dialog_shows_same_datetime_summary(self) -> None:
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-same",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime": 2000.0,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime": 2000.0,
            "destination_mtime_human": "2026-01-01 10:00:00",
            "comparison_reason": "Stessa data e ora di modifica",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)

        joined = "\n".join(self._collect_widget_texts(dialog))
        self.assertIn("BLOCCO FILE AGGIORNAMENTI", joined)
        self.assertIn("BLOCCO FILE REPERTORIO", joined)
        self.assertIn("stessa data e ora di modifica", joined)

    def test_dialog_geometry_is_clamped_and_centered(self) -> None:
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-geometry",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
            "comparison_reason": "File del repertorio piu recente",
            "mtime_delta_human": "1 ora",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)
        dialog.update()

        width, height = self._parse_geometry_size(dialog.geometry())
        screen_w = int(dialog.winfo_screenwidth())
        screen_h = int(dialog.winfo_screenheight())

        self.assertLessEqual(width, screen_w - 96)
        self.assertLessEqual(height, screen_h - 80)

    def test_dialog_long_paths_wrap_without_expanding_width(self) -> None:
        self.app.open_repertory_organizer_window()
        very_long_path = "C:/" + "/".join(["cartella_molto_lunga"] * 24) + "/nome_file_molto_lungo.mp3"
        payload = {
            "request_id": "req-wrap",
            "source_name": "nome_file_molto_lungo_nome_file_molto_lungo.mp3",
            "source_path": very_long_path,
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "nome_file_molto_lungo_nome_file_molto_lungo.mp3",
            "destination_path": very_long_path,
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
            "comparison_reason": "File del repertorio piu recente",
            "mtime_delta_human": "1 ora",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)
        dialog.update()

        width, _height = self._parse_geometry_size(dialog.geometry())
        screen_w = int(dialog.winfo_screenwidth())
        self.assertLessEqual(width, screen_w - 96)

        wrapped_labels = []

        def _visit(node) -> None:
            try:
                if str(node.cget("text")) == very_long_path:
                    wrapped_labels.append(node)
            except Exception:
                pass
            try:
                for child in node.winfo_children():
                    _visit(child)
            except Exception:
                pass

        _visit(dialog)

        self.assertGreaterEqual(len(wrapped_labels), 1)
        for label in wrapped_labels:
            wrap = int(label.cget("wraplength"))
            self.assertGreater(wrap, 0)
            self.assertLessEqual(wrap, max(220, width - 100))

    def test_dialog_all_four_buttons_are_visible(self) -> None:
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-buttons",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
        }
        self.app._handle_repertory_worker_decision_required(payload)
        dialog = self.app._repertory_decision_dialog
        self.assertIsNotNone(dialog)
        dialog.update_idletasks()

        labels = set(self._collect_widget_texts(dialog))
        self.assertIn("Aggiorna comunque", labels)
        self.assertIn("Non aggiornare", labels)
        self.assertIn("Aggiorna questo e tutti i successivi", labels)
        self.assertIn("Mantieni questo e tutti i successivi", labels)

    def test_repertory_decision_tooltips_registered_and_cleanup(self) -> None:
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-tip",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
            "mtime_delta_human": "1 ora",
        }
        self.app._handle_repertory_worker_decision_required(payload)

        self.assertGreaterEqual(len(self.app._repertory_decision_tooltips), 5)
        tooltip_texts = [tip.text for tip in self.app._repertory_decision_tooltips]
        self.assertIn(
            "Sostituisce solo il file visualizzato.\n\nIl controllo data e ora restera attivo per i file successivi.",
            tooltip_texts,
        )
        self.assertIn(
            "Mantiene il file attualmente presente nel Repertorio.\n\nIl controllo data e ora restera attivo per i file successivi.",
            tooltip_texts,
        )
        self.assertIn(
            "Mantiene il file visualizzato e conserva automaticamente tutti i successivi casi soggetti al controllo data e ora.\n\nLa scelta vale solo per questa sessione.",
            tooltip_texts,
        )
        self.assertIn(
            "Riassume quale dei due file e piu recente e la relativa differenza temporale.",
            tooltip_texts,
        )

        for tip in self.app._repertory_decision_tooltips:
            tip._schedule()
            self.assertIsNotNone(tip._after_id)

        tracked = list(self.app._repertory_decision_tooltips)
        self.assertTrue(all(tip in self.app.tooltips for tip in tracked))
        self.app._close_repertory_decision_dialog()
        self.assertIsNone(self.app._repertory_decision_dialog)
        self.assertEqual(self.app._repertory_decision_tooltips, [])
        for tip in tracked:
            self.assertIsNone(tip._after_id)
            self.assertNotIn(tip, self.app.tooltips)

    def test_repertory_timer_is_cancelled_on_window_close(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_started_at = time.monotonic()

        self.app._start_repertory_timer()
        self.assertIsNotNone(self.app._repertory_timer_job)

        self.app._close_repertory_window()

        self.assertIsNone(self.app._repertory_timer_job)
        self.assertIsNone(self.app._repertory_dialog)

    def test_open_smartphone_folder_shows_warning_when_missing(self) -> None:
        self.app.open_repertory_organizer_window()
        missing_folder = str(Path(self._settings_temp_dir.name) / "missing_sub")
        self.app._repertory_selected_smartphone_folder = missing_folder
        self.app._repertory_last_completed_smartphone_folder = missing_folder
        if self.app._repertory_smartphone_entry is not None:
            self.app._replace_entry(self.app._repertory_smartphone_entry, missing_folder)
        with mock.patch("gui.messagebox.showwarning") as warning_mock:
            self.app._open_repertory_smartphone_folder()
        self.assertTrue(warning_mock.called)

    def test_reset_smartphone_folder_cancel_does_nothing(self) -> None:
        self.app.open_repertory_organizer_window()
        with mock.patch.object(self.app, "_confirm_reset_repertory_smartphone_folder", return_value=False):
            with mock.patch("gui.reset_smartphone_tablet_dir") as reset_mock:
                self.app._reset_repertory_smartphone_folder()
        reset_mock.assert_not_called()

    def test_reset_smartphone_folder_success_message(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected = str(Path(temp_dir).resolve())
            self.app._repertory_smartphone_root = selected
            self.app._repertory_selected_smartphone_folder = selected
            self.app._repertory_last_completed_smartphone_folder = selected
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)

            class _ImmediateThread:
                def __init__(self, target=None, daemon=None):
                    self._target = target
                    self._daemon = daemon

                def start(self):
                    if self._target is not None:
                        self._target()

            with mock.patch.object(self.app, "_confirm_reset_repertory_smartphone_folder", return_value=True):
                with mock.patch("gui.reset_smartphone_tablet_dir", return_value=(4, 2)):
                    with mock.patch("gui.threading.Thread", _ImmediateThread):
                        with mock.patch.object(
                            self.app,
                            "after",
                            side_effect=lambda _delay, callback, *args: callback(*args),
                        ):
                            with mock.patch("gui.messagebox.showinfo") as info_mock:
                                self.app._reset_repertory_smartphone_folder()
                                self.assertTrue(info_mock.called)
                                rendered = "\n".join(str(arg) for arg in info_mock.call_args.args)
                                self.assertIn("File eliminati: 4", rendered)
                                self.assertIn("Sottocartelle eliminate: 2", rendered)

    def test_android_buttons_are_disabled_on_form_open(self) -> None:
        self.app.open_repertory_organizer_window()
        self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "disabled")
        self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "disabled")

    def test_android_buttons_remain_disabled_after_selection_before_processing(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected = str(Path(temp_dir).resolve())
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)
            self.app._on_repertory_diagnostics_paths_changed()
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "disabled")

    def test_android_buttons_enable_only_after_successful_update_completion(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected = str(Path(temp_dir).resolve())
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)
            self.app._repertory_selected_smartphone_folder = selected

            result = SimpleNamespace(
                success=True,
                interrupted=False,
                processed_source_files=1,
                total_source_files=1,
                counters={},
                session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
            )
            with mock.patch("gui.messagebox.showinfo"):
                self.app._handle_repertory_worker_completed(result)

            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "normal")

    def test_android_buttons_disable_when_selected_folder_disappears(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected_path = Path(temp_dir) / "android"
            selected_path.mkdir()
            selected = str(selected_path.resolve())
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)
            self.app._repertory_selected_smartphone_folder = selected
            self.app._repertory_last_completed_smartphone_folder = selected
            self.app._update_repertory_android_buttons_state()
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "normal")

            selected_path.rmdir()
            self.app._update_repertory_android_buttons_state()
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "disabled")

    def test_android_open_uses_current_session_path(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected = str(Path(temp_dir).resolve())
            self.app._repertory_smartphone_root = r"C:\\MixCreatorPro-File per aggiornamento Smartphone-Tablet"
            self.app._repertory_selected_smartphone_folder = selected
            self.app._repertory_last_completed_smartphone_folder = selected
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)

            with mock.patch("gui.os.startfile") as startfile_mock:
                self.app._open_repertory_smartphone_folder()

            self.assertTrue(startfile_mock.called)
            self.assertEqual(str(startfile_mock.call_args.args[0]), selected)

    def test_android_reset_uses_current_session_path(self) -> None:
        self.app.open_repertory_organizer_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            selected = str(Path(temp_dir).resolve())
            self.app._repertory_smartphone_root = r"C:\\MixCreatorPro-File per aggiornamento Smartphone-Tablet"
            self.app._repertory_selected_smartphone_folder = selected
            self.app._repertory_last_completed_smartphone_folder = selected
            if self.app._repertory_smartphone_entry is not None:
                self.app._replace_entry(self.app._repertory_smartphone_entry, selected)

            class _ImmediateThread:
                def __init__(self, target=None, daemon=None):
                    self._target = target
                    self._daemon = daemon

                def start(self):
                    if self._target is not None:
                        self._target()

            with mock.patch.object(self.app, "_confirm_reset_repertory_smartphone_folder", return_value=True):
                with mock.patch("gui.threading.Thread", _ImmediateThread):
                    with mock.patch.object(
                        self.app,
                        "after",
                        side_effect=lambda _delay, callback, *args: callback(*args),
                    ):
                        with mock.patch("gui.reset_smartphone_tablet_dir", return_value=(0, 0)) as reset_mock:
                            self.app._reset_repertory_smartphone_folder()

            self.assertTrue(reset_mock.called)
            target_arg = Path(reset_mock.call_args.args[0]).resolve()
            expected_arg = Path(selected).resolve()
            self.assertEqual(target_arg, expected_arg)
            self.assertEqual(Path(reset_mock.call_args.kwargs.get("expected_root", "")).resolve(), expected_arg)

    def test_android_reset_rejects_disk_root(self) -> None:
        self.app.open_repertory_organizer_window()
        drive_root = Path.cwd().anchor or "C:\\"
        normalized = str(Path(drive_root).resolve())
        self.app._repertory_selected_smartphone_folder = normalized
        self.app._repertory_last_completed_smartphone_folder = normalized
        if self.app._repertory_smartphone_entry is not None:
            self.app._replace_entry(self.app._repertory_smartphone_entry, normalized)

        with mock.patch("gui.messagebox.showerror") as error_mock:
            self.app._reset_repertory_smartphone_folder()
        self.assertTrue(error_mock.called)

    def test_repertory_browse_titles_for_update_mode(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("update")
        with mock.patch("gui.filedialog.askdirectory", return_value="") as ask_mock:
            self.app._select_repertory_updates_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella contenente i file da aggiornare")
            self.app._select_repertory_library_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella del Repertorio suddiviso")
            self.app._select_repertory_general_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella del Repertorio generale")
            self.app._select_repertory_smartphone_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella per dispositivo Android")
            self.app._select_repertory_results_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella dei risultati")

    def test_repertory_browse_titles_for_diagnostics_mode(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("diagnostics")
        with mock.patch("gui.filedialog.askdirectory", return_value="") as ask_mock:
            self.app._select_repertory_updates_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella del Repertorio suddiviso da controllare")
            self.app._select_repertory_library_folder()
            self.assertEqual(ask_mock.call_args.kwargs.get("title"), "Seleziona la cartella del Repertorio generale da confrontare")

    def test_tooltips_rep001_main_controls_present_and_android_terminology(self) -> None:
        self.app.open_repertory_organizer_window()

        tips_by_widget = {
            getattr(tip, "widget", None): str(getattr(tip, "text", ""))
            for tip in self.app.tooltips
        }

        self.assertIn("file MP3 da confrontare e aggiornare", tips_by_widget.get(self.app._repertory_updates_entry, ""))
        self.assertIn("root del repertorio", tips_by_widget.get(self.app._repertory_library_entry, ""))
        self.assertIn("cartella piatta", tips_by_widget.get(self.app._repertory_general_entry, ""))
        self.assertIn("dispositivo Android", tips_by_widget.get(self.app._repertory_smartphone_entry, ""))
        self.assertIn("Avvia il confronto", tips_by_widget.get(self.app._repertory_start_button, ""))
        self.assertIn("interruzione sicura", tips_by_widget.get(self.app._repertory_stop_button, ""))
        self.assertIn("sessione contenente report", tips_by_widget.get(self.app._repertory_open_results_button, ""))
        self.assertIn("cartella di appoggio Android", tips_by_widget.get(self.app._repertory_open_smartphone_button, ""))
        self.assertIn("cartella Android selezionata", tips_by_widget.get(self.app._repertory_reset_smartphone_button, ""))
        self.assertIn("versioni precedenti", tips_by_widget.get(self.app._repertory_backup_check, ""))

    def test_tooltips_rep002_main_controls_present(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("diagnostics")
        self.app._apply_repertory_mode_layout()

        tips_by_widget = {
            getattr(tip, "widget", None): str(getattr(tip, "text", ""))
            for tip in self.app.tooltips
        }

        self.assertIn("repertorio organizzato da confrontare", tips_by_widget.get(self.app._repertory_updates_entry, "").casefold())
        self.assertIn("repertorio generale piatto", tips_by_widget.get(self.app._repertory_library_entry, "").casefold())
        self.assertIn("Seleziona tutte le cartelle", tips_by_widget.get(self.app._repertory_diagnostics_select_all_button, ""))
        self.assertIn("Rimuove tutte le selezioni", tips_by_widget.get(self.app._repertory_diagnostics_deselect_all_button, ""))
        self.assertIn("Rilegge l'alberatura", tips_by_widget.get(self.app._repertory_diagnostics_refresh_button, ""))
        self.assertIn("Confronta i due repertori", tips_by_widget.get(self.app._repertory_start_button, ""))
        self.assertIn("diagnosi in corso", tips_by_widget.get(self.app._repertory_stop_button, ""))
        self.assertIn("sessione diagnostica completata", tips_by_widget.get(self.app._repertory_open_results_button, ""))

    def test_tooltips_rep003_main_controls_present_and_results_folder_message(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("insert_tracks")
        self.app._apply_repertory_mode_layout()

        tips_by_widget = {
            getattr(tip, "widget", None): str(getattr(tip, "text", ""))
            for tip in self.app.tooltips
        }

        self.assertIn("nuovi MP3 da inserire", tips_by_widget.get(self.app._rep003_new_tracks_entry, ""))
        self.assertIn("root del repertorio", tips_by_widget.get(self.app._rep003_split_entry, ""))
        self.assertIn("copia di ogni nuovo brano", tips_by_widget.get(self.app._rep003_general_entry, ""))
        self.assertIn("futura sincronizzazione Android", tips_by_widget.get(self.app._rep003_smartphone_entry, ""))
        self.assertIn("Carica i nuovi MP3", tips_by_widget.get(self.app._rep003_load_button, ""))
        self.assertIn("mostra anche i brani gia abbinati", tips_by_widget.get(self.app._rep003_show_managed_switch, "").casefold())
        self.assertIn("Crea una nuova cartella", tips_by_widget.get(self.app._rep003_create_folder_button, ""))
        self.assertIn("Rilegge le cartelle", tips_by_widget.get(self.app._rep003_refresh_folders_button, ""))
        self.assertIn("Memorizza le cartelle", tips_by_widget.get(self.app._rep003_assign_button, ""))
        self.assertIn("Cancella gli abbinamenti", tips_by_widget.get(self.app._rep003_remove_button, ""))
        self.assertIn("sessione di inserimento nuovi brani completata", tips_by_widget.get(self.app._repertory_open_results_button, ""))

    def test_tooltips_do_not_contain_legacy_hardcoded_android_path(self) -> None:
        self.app.open_repertory_organizer_window()
        legacy = "mixcreatorpro-file per aggiornamento smartphone-tablet"
        for tip in self.app.tooltips:
            text = str(getattr(tip, "text", "")).casefold()
            self.assertNotIn(legacy, text)

    def test_worker_side_does_not_create_gui(self) -> None:
        # Contract test: worker emits callback payload and GUI handles dialog creation in main thread.
        self.app.open_repertory_organizer_window()
        payload = {
            "request_id": "req-3",
            "source_name": "A.mp3",
            "source_path": "C:/src/A.mp3",
            "source_size": 100,
            "source_mtime_human": "2026-01-01 10:00:00",
            "destination_name": "A.mp3",
            "destination_path": "C:/dst/A.mp3",
            "destination_size": 110,
            "destination_mtime_human": "2026-01-01 11:00:00",
        }
        with mock.patch.object(self.app, "_open_repertory_mtime_decision_dialog") as open_mock:
            self.app._handle_repertory_worker_decision_required(payload)
        open_mock.assert_called_once_with(payload)

    def test_repertory_launcher_reenabled_after_close_button(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        self.app._close_repertory_window()
        self.assertEqual(fake_worker.cancel_calls, 1)
        self.assertTrue(self.app._repertory_close_requested)

        fake_worker._running = False
        result = SimpleNamespace(
            interrupted=True,
            processed_source_files=1,
            total_source_files=1,
            counters={},
            session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            log_path="x.log",
        )
        with mock.patch("gui.messagebox.showinfo"):
            self.app._handle_repertory_worker_completed(result)

        self.assertIsNone(self.app._repertory_dialog)
        self.assertEqual(str(self.app.organize_repertory_button.cget("state")), "normal")

    def test_repertory_launcher_reenabled_after_close_x(self) -> None:
        self.app.open_repertory_organizer_window()
        fake_worker = _FakeRepertoryWorker(running=True)
        self.app.repertory_worker = fake_worker

        dialog = self.app._repertory_dialog
        self.assertIsNotNone(dialog)
        close_handler = dialog.protocol("WM_DELETE_WINDOW")
        self.assertIsNotNone(close_handler)
        dialog.tk.call(close_handler)
        self.assertEqual(fake_worker.cancel_calls, 1)

        fake_worker._running = False
        result = SimpleNamespace(
            interrupted=True,
            processed_source_files=1,
            total_source_files=1,
            counters={},
            session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            log_path="x.log",
        )
        with mock.patch("gui.messagebox.showinfo"):
            self.app._handle_repertory_worker_completed(result)

        self.assertIsNone(self.app._repertory_dialog)
        self.assertEqual(str(self.app.organize_repertory_button.cget("state")), "normal")

    def test_repertory_window_reopen_and_no_double_open(self) -> None:
        self.app.open_repertory_organizer_window()
        first_dialog = self.app._repertory_dialog
        self.assertIsNotNone(first_dialog)

        with mock.patch("gui.ManagedCTkToplevel") as top_mock:
            self.app.open_repertory_organizer_window()
            top_mock.assert_not_called()

        self.app._close_repertory_window()
        self.assertIsNone(self.app._repertory_dialog)

        self.app.open_repertory_organizer_window()
        second_dialog = self.app._repertory_dialog
        self.assertIsNotNone(second_dialog)
        self.assertIsNot(first_dialog, second_dialog)

    def test_repertory_mode_default_visible_label_and_internal_value(self) -> None:
        self.app.open_repertory_organizer_window()

        self.assertEqual(str(self.app._repertory_mode_var.get()), "update")
        self.assertEqual(str(self.app._repertory_mode_label_var.get()), "Aggiornamento Repertorio")
        self.assertEqual(str(self.app._repertory_start_button.cget("text")), "Avvia aggiornamento")
        self.assertEqual(str(self.app._repertory_updates_label.cget("text")), "Cartella aggiornamenti")

    def test_repertory_mode_select_diagnostics_updates_internal_and_layout(self) -> None:
        self.app.open_repertory_organizer_window()

        self.app._on_repertory_mode_selected("Diagnosi Repertorio")

        self.assertEqual(str(self.app._repertory_mode_var.get()), "diagnostics")
        self.assertEqual(str(self.app._repertory_mode_label_var.get()), "Diagnosi Repertorio")
        self.assertEqual(str(self.app._repertory_start_button.cget("text")), "Avvia diagnosi")
        self.assertEqual(str(self.app._repertory_updates_label.cget("text")), "Cartella Repertorio suddiviso")
        self.assertEqual(str(self.app._repertory_library_label.cget("text")), "Cartella Repertorio Generale")
        self.assertFalse(bool(self.app._repertory_results_label.winfo_ismapped()))
        self.assertFalse(bool(self.app._repertory_backup_check.winfo_ismapped()))
        self.assertFalse(bool(self.app._repertory_open_smartphone_button.winfo_ismapped()))
        self.assertFalse(bool(self.app._repertory_reset_smartphone_button.winfo_ismapped()))

    def test_repertory_mode_back_to_update_restores_update_layout(self) -> None:
        self.app.open_repertory_organizer_window()

        self.app._on_repertory_mode_selected("Diagnosi Repertorio")
        self.app._on_repertory_mode_selected("Aggiornamento Repertorio")

        self.assertEqual(str(self.app._repertory_mode_var.get()), "update")
        self.assertEqual(str(self.app._repertory_mode_label_var.get()), "Aggiornamento Repertorio")
        self.assertEqual(str(self.app._repertory_start_button.cget("text")), "Avvia aggiornamento")
        self.assertEqual(str(self.app._repertory_updates_label.cget("text")), "Cartella aggiornamenti")
        self.assertEqual(str(self.app._repertory_library_label.cget("text")), "Cartella repertorio suddiviso")
        self.assertEqual(str(self.app._repertory_open_results_button.cget("text")), "Apri cartella risultati")
        self.assertEqual(str(self.app._repertory_status_label.cget("text")), "Pronto")

    def test_repertory_mode_repeated_toggles_no_duplication_and_clean_close(self) -> None:
        self.app.open_repertory_organizer_window()

        start_button = self.app._repertory_start_button
        updates_entry = self.app._repertory_updates_entry
        library_entry = self.app._repertory_library_entry
        log_box = self.app._repertory_log_box

        for _ in range(5):
            self.app._on_repertory_mode_selected("Diagnosi Repertorio")
            self.app._on_repertory_mode_selected("Aggiornamento Repertorio")

        self.assertIs(start_button, self.app._repertory_start_button)
        self.assertIs(updates_entry, self.app._repertory_updates_entry)
        self.assertIs(library_entry, self.app._repertory_library_entry)
        self.assertIs(log_box, self.app._repertory_log_box)
        self.assertEqual(str(self.app._repertory_mode_label_var.get()), "Aggiornamento Repertorio")
        self.assertNotIn("UPDATE", str(self.app._repertory_mode_label_var.get()))
        self.assertNotIn("DIAGNOSTICS", str(self.app._repertory_mode_label_var.get()))

        self.app._close_repertory_window()
        self.assertIsNone(self.app._repertory_dialog)

    def test_repertory_mode_diagnostics_updates_labels(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        self.assertEqual(str(self.app._repertory_updates_label.cget("text")), "Cartella Repertorio suddiviso")
        self.assertEqual(str(self.app._repertory_library_label.cget("text")), "Cartella Repertorio Generale")
        self.assertEqual(str(self.app._repertory_start_button.cget("text")), "Avvia diagnosi")
        self.assertEqual(str(self.app._repertory_open_results_button.cget("text")), "Apri cartella Diagnosi")

    def test_open_results_button_is_mode_isolated_and_rep003_ready_requires_valid_session(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("update")
        self.app._set_repertory_results_folder_for_mode("update", "C:/tmp/update_session")
        self.app._apply_repertory_mode_layout()
        self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

        self.app._set_repertory_mode("diagnostics")
        self.app._set_repertory_results_folder_for_mode("diagnostics", "")
        self.app._apply_repertory_mode_layout()
        self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

        self.app._set_repertory_results_folder_for_mode("diagnostics", "C:/tmp/diagnostics_session")
        self.app._update_repertory_open_results_button_state()
        self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")
        self.assertEqual(str(self.app._repertory_open_results_button.cget("text")), "Apri cartella Diagnosi")

        self.app._set_repertory_mode("insert_tracks")
        self.app._apply_repertory_mode_layout()
        self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

        with tempfile.TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            self.app._set_repertory_results_folder_for_mode("insert_tracks", str(session_folder))
            self.app._rep003_session_state = "READY_FOR_NEW_SESSION"
            self.app._update_repertory_open_results_button_state()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

            session_folder.rmdir()
            self.app._update_repertory_open_results_button_state()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

    def test_repertory_paths_are_blank_at_open_and_cleared_on_mode_switch(self) -> None:
        self.app.settings["repertory_updates_folder"] = "C:/preset/updates"
        self.app.settings["repertory_library_folder"] = "C:/preset/split"
        self.app.settings["repertory_general_folder"] = "C:/preset/general"
        self.app.settings["repertory_results_folder"] = "C:/preset/results"
        self.app.settings["repertory_smartphone_folder"] = "C:/preset/smartphone"

        self.app.open_repertory_organizer_window()

        self.assertEqual(self.app._repertory_updates_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_library_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_general_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_results_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_smartphone_entry.get().strip(), "")

        self.app._replace_entry(self.app._repertory_updates_entry, "C:/temp/updates")
        self.app._replace_entry(self.app._repertory_library_entry, "C:/temp/split")
        self.app._replace_entry(self.app._repertory_general_entry, "C:/temp/general")
        self.app._replace_entry(self.app._repertory_results_entry, "C:/temp/results")
        self.app._replace_entry(self.app._repertory_smartphone_entry, "C:/temp/smartphone")

        self.app._on_repertory_mode_selected("Diagnosi Repertorio")

        self.assertEqual(self.app._repertory_updates_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_library_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_general_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_results_entry.get().strip(), "")
        self.assertEqual(self.app._repertory_smartphone_entry.get().strip(), "")

    def test_repertory_mode_diagnostics_start_uses_two_paths(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "root.mp3").write_bytes(b"root")
            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))

            with mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

        self.assertTrue(start_mock.called)
        self.assertEqual(start_mock.call_args.kwargs["split_repertory_dir"], str(split_dir))
        self.assertEqual(start_mock.call_args.kwargs["general_repertory_dir"], str(general_dir))
        self.assertEqual(start_mock.call_args.kwargs["results_dir"], str(general_dir))
        self.assertIn("selected_relative_roots", start_mock.call_args.kwargs)
        self.assertIn("excluded_relative_roots", start_mock.call_args.kwargs)
        self.assertIn("include_root_files", start_mock.call_args.kwargs)

    def test_repertory_update_start_uses_selected_smartphone_folder(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("update")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            updates_dir = root / "updates"
            repertory_dir = root / "split"
            general_dir = root / "general"
            results_dir = root / "results"
            smartphone_dir = root / "smartphone"
            updates_dir.mkdir()
            repertory_dir.mkdir()
            general_dir.mkdir()
            results_dir.mkdir()
            smartphone_dir.mkdir()

            self.app._replace_entry(self.app._repertory_updates_entry, str(updates_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(repertory_dir))
            self.app._replace_entry(self.app._repertory_general_entry, str(general_dir))
            self.app._replace_entry(self.app._repertory_results_entry, str(results_dir))
            self.app._replace_entry(self.app._repertory_smartphone_entry, str(smartphone_dir))

            with mock.patch.object(self.app, "_ensure_repertory_smartphone_folder_ready", return_value=True), mock.patch.object(self.app.repertory_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(start_mock.called)
            self.assertEqual(start_mock.call_args.kwargs["smartphone_tablet_dir"], str(smartphone_dir.resolve()))
            self.assertEqual(str(self.app.settings.get("repertory_smartphone_folder", "")), str(smartphone_dir.resolve()))

    def test_repertory_update_start_button_reenabled_after_completion(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("update")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            updates_dir = root / "updates"
            repertory_dir = root / "split"
            general_dir = root / "general"
            results_dir = root / "results"
            smartphone_dir = root / "smartphone"
            updates_dir.mkdir()
            repertory_dir.mkdir()
            general_dir.mkdir()
            results_dir.mkdir()
            smartphone_dir.mkdir()

            self.app._replace_entry(self.app._repertory_updates_entry, str(updates_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(repertory_dir))
            self.app._replace_entry(self.app._repertory_general_entry, str(general_dir))
            self.app._replace_entry(self.app._repertory_results_entry, str(results_dir))
            self.app._replace_entry(self.app._repertory_smartphone_entry, str(smartphone_dir))

            fake_worker = _FakeRepertoryWorker(running=True)
            self.app.repertory_worker = fake_worker

            result = SimpleNamespace(
                interrupted=False,
                processed_source_files=1,
                total_source_files=1,
                counters={},
                session_folder=str(root / "Organizzazione_Repertorio_20990101_010101"),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                smartphone_tablet_root=str(smartphone_dir),
                repertory_not_found_dir="",
            )

            with mock.patch("gui.messagebox.showinfo"):
                self.app._handle_repertory_worker_completed(result)
            fake_worker._running = False
            self.app.update()

            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_stop_button.cget("state")), "disabled")

    def test_repertory_diagnostics_start_button_reenabled_after_completion(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split_dir = root / "split"
            general_dir = root / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "disc").mkdir()
            (split_dir / "disc" / "A.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            fake_diag_worker = _FakeRepertoryWorker(running=True)
            self.app.repertory_diagnostics_worker = fake_diag_worker

            result = SimpleNamespace(
                session_folder=str(root / "Diagnosi_Repertorio_20990101_010101"),
                analyzed_general_files=0,
                analyzed_split_files=1,
                matched_both=0,
                only_general=0,
                only_split=1,
                split_duplicates=0,
                split_duplicate_extra_occurrences=0,
                read_errors=0,
                copy_errors=0,
                copied_files=1,
                is_perfect_alignment=False,
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                folder_report_paths={"csv": "f.csv"},
            )

            with mock.patch("gui.messagebox.showinfo"):
                self.app._handle_repertory_diagnostics_worker_completed(result)
            fake_diag_worker._running = False
            self.app.update()

            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_stop_button.cget("state")), "disabled")

    def test_diagnostics_start_button_reacts_to_select_deselect_reselect(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            sub = split_dir / "disc"
            sub.mkdir(parents=True)
            general_dir.mkdir(parents=True)
            (sub / "A.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            self.app._deselect_all_repertory_diagnostics_nodes()
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "disabled")

            self.assertIn("disc", self.app._repertory_diagnostics_tree_items)
            self.app._repertory_diagnostics_tree_items["disc"]["var"].set(True)
            self.app._on_repertory_diagnostics_tree_node_toggled("disc")
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")

            root_item = self.app._repertory_diagnostics_tree_items.get("__ROOT_FILES__")
            if root_item is not None:
                root_item["var"].set(False)
            self.app._update_repertory_primary_action_state()
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")

            self.app._deselect_all_repertory_diagnostics_nodes()
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "disabled")

            self.app._repertory_diagnostics_tree_items["disc"]["var"].set(True)
            self.app._on_repertory_diagnostics_tree_node_toggled("disc")
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")

    def test_mode_switch_rep003_to_diagnostics_uses_diagnostics_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "disc").mkdir()
            (split / "disc" / "x.mp3").write_bytes(b"x")
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            self.app._replace_entry(self.app._repertory_updates_entry, str(split))
            self.app._replace_entry(self.app._repertory_library_entry, str(general))

            self.app._on_repertory_mode_selected("Diagnosi Repertorio")
            self.assertEqual(str(self.app._repertory_mode_var.get()), "diagnostics")
            self.assertEqual(str(self.app._repertory_start_button.cget("text")), "Avvia diagnosi")
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "disabled")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split))
            self.app._replace_entry(self.app._repertory_library_entry, str(general))
            self.app._refresh_repertory_diagnostics_folder_tree()
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")

    def test_repertory_mode_diagnostics_tree_select_deselect_controls(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "discA").mkdir()
            (split_dir / "discB").mkdir()
            (split_dir / "root.mp3").write_bytes(b"root")
            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            self.assertGreaterEqual(len(self.app._repertory_diagnostics_tree_items), 2)
            self.app._deselect_all_repertory_diagnostics_nodes()
            payload = self.app._build_repertory_diagnostics_selection_payload()
            self.assertFalse(payload[2])

            self.app._select_all_repertory_diagnostics_nodes()
            payload_all = self.app._build_repertory_diagnostics_selection_payload()
            self.assertTrue(payload_all[2])

    def test_final_message_update_popup_is_compact_without_separators_or_paths(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            interrupted=False,
            processed_source_files=7,
            total_source_files=7,
            counters={
                "AGGIORNATO": 2,
                "AGGIORNATO_MULTIPLO": 0,
                "ERRORE_SORGENTE": 1,
                "ERRORE_BACKUP": 0,
                "ERRORE_COPIA": 0,
                "ERRORE_VERIFICA": 0,
                "AMBIGUO": 0,
                "FILE_MANTENUTI": 3,
                "SMARTPHONE_TABLET_COPIATI": 2,
                "SMARTPHONE_TABLET_ERRORI": 0,
                "FILE_NON_TROVATI_NEL_REPERTORIO": 2,
                "FILE_NON_TROVATI_COPIATI": 1,
                "FILE_NON_TROVATI_ERRORI_COPIA": 1,
                "BRANI_DA_INSERIRE": 1,
                "BRANI_DA_INSERIRE_ERRORI": 0,
            },
            session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            log_path="x.log",
            smartphone_tablet_root="C:/smartphone",
            repertory_not_found_dir="C:/repo/File Non trovati in Repertorio",
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_worker_completed(result)
            self.assertTrue(info_mock.called)
            rendered = str(info_mock.call_args.args[1])
            lines = rendered.splitlines()

            self.assertIn("ORGANIZZAZIONE REPERTORIO COMPLETATA", rendered)
            self.assertIn("Brani elaborati: 7", rendered)
            self.assertIn("Brani aggiornati: 2", rendered)
            self.assertIn("Copie aggiornate nel Repertorio: 2", rendered)
            self.assertIn("Brani mantenuti: 3", rendered)
            self.assertIn("Brani non trovati nel Repertorio: 2", rendered)
            self.assertIn("Brani non trovati da inserire: 1", rendered)
            self.assertIn("Copie in cartella per dispositivo Android: 2", rendered)
            self.assertIn("Errori: 2", rendered)
            self.assertIn("Consultare i report per il dettaglio degli errori.", rendered)
            self.assertIn("I dettagli completi sono disponibili nei report.", rendered)
            self.assertIn("Risultati salvati nella cartella della sessione.", rendered)

            self.assertNotIn("====", rendered)
            self.assertNotIn("----", rendered)
            self.assertNotIn("C:/tmp/", rendered)
            self.assertNotIn("C:/repo/", rendered)
            self.assertLessEqual(len(lines), 14)
            self.assertTrue(all(len(line) <= 90 for line in lines if line))

    def test_final_message_update_popup_without_errors_has_no_error_hint(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            interrupted=False,
            processed_source_files=5,
            total_source_files=5,
            counters={
                "AGGIORNATO": 2,
                "AGGIORNATO_MULTIPLO": 0,
                "ERRORE_SORGENTE": 0,
                "ERRORE_BACKUP": 0,
                "ERRORE_COPIA": 0,
                "ERRORE_VERIFICA": 0,
                "AMBIGUO": 0,
                "FILE_MANTENUTI": 0,
                "SMARTPHONE_TABLET_COPIATI": 2,
                "SMARTPHONE_TABLET_ERRORI": 0,
                "FILE_NON_TROVATI_NEL_REPERTORIO": 3,
                "FILE_NON_TROVATI_COPIATI": 2,
                "FILE_NON_TROVATI_ERRORI_COPIA": 0,
                "BRANI_DA_INSERIRE": 0,
                "BRANI_DA_INSERIRE_ERRORI": 0,
            },
            session_folder="C:/tmp/Organizzazione_Repertorio_20990101_010101",
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            log_path="x.log",
            smartphone_tablet_root="C:/smartphone",
            repertory_not_found_dir="C:/repo/File Non trovati in Repertorio",
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_worker_completed(result)
            self.assertTrue(info_mock.called)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("Copie in cartella per dispositivo Android: 2", rendered)
            self.assertIn("Errori: 0", rendered)
            self.assertNotIn("Consultare i report per il dettaglio degli errori.", rendered)

    def test_diagnostics_start_blocked_when_paths_are_identical(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            same = Path(temp_dir) / "same"
            same.mkdir()
            (same / "A.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(same))
            self.app._replace_entry(self.app._repertory_library_entry, str(same))

            with mock.patch("gui.messagebox.showerror") as error_mock, mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(error_mock.called)
            self.assertFalse(start_mock.called)

    def test_diagnostics_start_blocked_when_paths_match_with_syntax_variants(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            split_dir.mkdir()
            (split_dir / "A.mp3").write_bytes(b"A")
            alias_same = str(split_dir / ".")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, alias_same)

            with mock.patch("gui.messagebox.showerror") as error_mock, mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(error_mock.called)
            self.assertFalse(start_mock.called)

    def test_diagnostics_start_blocked_when_paths_match_case_variants_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("Case-insensitive canonical path comparison is Windows-specific.")

        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            split_dir.mkdir()
            (split_dir / "A.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(split_dir).upper())

            with mock.patch("gui.messagebox.showerror") as error_mock, mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(error_mock.called)
            self.assertFalse(start_mock.called)

    def test_diagnostics_start_allowed_with_different_paths(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "root.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            with mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(start_mock.called)

    def test_diagnostics_tree_contains_root_virtual_node_even_without_root_mp3(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "sub").mkdir()
            (split_dir / "sub" / "A.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            self.assertIn("__ROOT_FILES__", self.app._repertory_diagnostics_tree_items)

    def test_root_virtual_node_selected_by_default(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "root.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            include_root = self.app._build_repertory_diagnostics_selection_payload()[2]
            self.assertTrue(include_root)

    def test_deselect_all_turns_off_root_and_blocks_start(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "root.mp3").write_bytes(b"A")
            (split_dir / "disc").mkdir()
            (split_dir / "disc" / "B.mp3").write_bytes(b"B")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()
            self.app._deselect_all_repertory_diagnostics_nodes()

            include_root = self.app._build_repertory_diagnostics_selection_payload()[2]
            self.assertFalse(include_root)

            with mock.patch("gui.messagebox.showerror") as error_mock, mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(error_mock.called)
            self.assertFalse(start_mock.called)

    def test_root_on_but_zero_mp3_and_no_folder_selection_blocks_start(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            payload = self.app._build_repertory_diagnostics_selection_payload()
            self.assertTrue(payload[2])
            self.assertEqual(self.app._count_root_mp3_direct(str(split_dir)), 0)

            with mock.patch("gui.messagebox.showerror") as error_mock, mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(error_mock.called)
            self.assertFalse(start_mock.called)

    def test_root_on_with_direct_mp3_allows_start(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._repertory_mode_var.set("diagnostics")
        self.app._apply_repertory_mode_layout()

        with tempfile.TemporaryDirectory() as temp_dir:
            split_dir = Path(temp_dir) / "split"
            general_dir = Path(temp_dir) / "general"
            split_dir.mkdir()
            general_dir.mkdir()
            (split_dir / "root.mp3").write_bytes(b"A")

            self.app._replace_entry(self.app._repertory_updates_entry, str(split_dir))
            self.app._replace_entry(self.app._repertory_library_entry, str(general_dir))
            self.app._refresh_repertory_diagnostics_folder_tree()

            with mock.patch.object(self.app.repertory_diagnostics_worker, "start") as start_mock:
                self.app._start_repertory_organization()

            self.assertTrue(start_mock.called)

    def test_diagnostics_popup_not_aligned_is_simplified_and_hides_duplicates(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            session_folder="C:/tmp/Diagnosi_Repertorio_20990101_010101",
            analyzed_general_files=5,
            analyzed_split_files=8,
            matched_both=2,
            only_general=3,
            only_split=5,
            split_duplicates=1,
            split_duplicate_extra_occurrences=1,
            read_errors=0,
            copy_errors=0,
            copied_files=0,
            is_perfect_alignment=False,
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            folder_report_paths={"csv": "f.csv"},
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_diagnostics_worker_completed(result)
            self.assertTrue(info_mock.called)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("ESITO: REPERTORI NON ALLINEATI", rendered)
            self.assertIn("File presenti in entrambi i repertori: 2", rendered)
            self.assertIn("File mancanti nella Cartella Generale: 5", rendered)
            self.assertIn("File mancanti nel Repertorio suddiviso: 3", rendered)
            self.assertIn("Errori: 0", rendered)
            self.assertIn("I dettagli completi sono disponibili nei report.", rendered)
            self.assertNotIn("A) TITOLI UNICI", rendered)
            self.assertNotIn("B) DUPLICATI", rendered)
            self.assertNotIn("C) ELABORAZIONE", rendered)
            self.assertNotIn("Titoli duplicati nel suddiviso", rendered)
            self.assertNotIn("Occorrenze duplicate eccedenti", rendered)

    def test_diagnostics_popup_aligned_is_simplified(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            session_folder="C:/tmp/Diagnosi_Repertorio_20990101_010101",
            analyzed_general_files=2,
            analyzed_split_files=2,
            matched_both=1210,
            only_general=0,
            only_split=0,
            split_duplicates=11,
            split_duplicate_extra_occurrences=44,
            read_errors=0,
            copy_errors=0,
            copied_files=0,
            is_perfect_alignment=False,
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            folder_report_paths={"csv": "f.csv"},
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_diagnostics_worker_completed(result)
            self.assertTrue(info_mock.called)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("ESITO: QUADRATURA COMPLETA", rendered)
            self.assertNotIn("ESITO: REPERTORI NON ALLINEATI", rendered)
            self.assertIn("Errori: 0", rendered)
            self.assertIn("I dettagli completi sono disponibili nei report.", rendered)
            self.assertNotIn("Risultati disponibili in:", rendered)
            self.assertNotIn("Titoli duplicati nel suddiviso", rendered)
            self.assertNotIn("File analizzati", rendered)

    def test_diagnostics_popup_not_aligned_when_missing_in_general(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            session_folder="C:/tmp/Diagnosi_Repertorio_20990101_010101",
            analyzed_general_files=3,
            analyzed_split_files=3,
            matched_both=2,
            only_general=0,
            only_split=1,
            split_duplicates=0,
            split_duplicate_extra_occurrences=0,
            read_errors=0,
            copy_errors=0,
            copied_files=0,
            is_perfect_alignment=True,
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            folder_report_paths={"csv": "f.csv"},
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_diagnostics_worker_completed(result)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("ESITO: REPERTORI NON ALLINEATI", rendered)
            self.assertNotIn("ESITO: QUADRATURA COMPLETA", rendered)

    def test_diagnostics_popup_not_aligned_when_missing_in_split(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            session_folder="C:/tmp/Diagnosi_Repertorio_20990101_010101",
            analyzed_general_files=3,
            analyzed_split_files=3,
            matched_both=2,
            only_general=1,
            only_split=0,
            split_duplicates=0,
            split_duplicate_extra_occurrences=0,
            read_errors=0,
            copy_errors=0,
            copied_files=0,
            is_perfect_alignment=True,
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            folder_report_paths={"csv": "f.csv"},
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_diagnostics_worker_completed(result)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("ESITO: REPERTORI NON ALLINEATI", rendered)
            self.assertNotIn("ESITO: QUADRATURA COMPLETA", rendered)

    def test_diagnostics_popup_shows_single_error_total_and_report_hint(self) -> None:
        self.app.open_repertory_organizer_window()
        result = SimpleNamespace(
            session_folder="C:/tmp/Diagnosi_Repertorio_20990101_010101",
            analyzed_general_files=2,
            analyzed_split_files=2,
            matched_both=1,
            only_general=0,
            only_split=0,
            split_duplicates=0,
            split_duplicate_extra_occurrences=0,
            read_errors=2,
            copy_errors=3,
            copied_files=0,
            is_perfect_alignment=False,
            report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
            folder_report_paths={"csv": "f.csv"},
        )

        with mock.patch("gui.messagebox.showinfo") as info_mock:
            self.app._handle_repertory_diagnostics_worker_completed(result)
            self.assertTrue(info_mock.called)
            rendered = str(info_mock.call_args.args[1])
            self.assertIn("Errori: 5", rendered)
            self.assertIn("Consultare i report per il dettaglio degli errori.", rendered)
            self.assertNotIn("Errori di lettura", rendered)
            self.assertNotIn("Errori di copia", rendered)

    def test_rep003_load_assign_and_finalize_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone_missing"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "uno.mp3").write_bytes(b"1")
            (new_tracks / "due.txt").write_text("x", encoding="utf-8")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))

            self.app._rep003_load_sources()

            self.assertTrue(smartphone.exists())
            self.assertEqual(len(self.app._rep003_model.tracks), 1)
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "disabled")

            track_iids = list(self.app._rep003_tracks_tree.get_children(""))
            self.assertEqual(len(track_iids), 1)
            self.app._rep003_tracks_tree.selection_set(track_iids[0])
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            item = self.app._rep003_model.tracks[0]
            self.assertEqual(item.status, "Gestito")
            self.assertEqual(item.destinations, ("Balli",))
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "normal")

            self.app._rep003_show_managed_var.set(False)
            self.app._rep003_on_show_managed_toggle()
            self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 0)

    def test_rep003_multiselect_block_when_one_track_is_already_managed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")
            (new_tracks / "b.mp3").write_bytes(b"2")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            first_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(first_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            rows = list(self.app._rep003_tracks_tree.get_children(""))
            self.assertEqual(len(rows), 2)
            self.app._rep003_tracks_tree.selection_set(rows)
            self.app._rep003_folders_tree.selection_set("Balli")

            with mock.patch("gui.messagebox.showerror") as error_mock:
                self.app._rep003_assign_selected()
                self.assertTrue(error_mock.called)

            tracks_by_name = {item.file_name: item for item in self.app._rep003_model.tracks}
            self.assertEqual(tracks_by_name["a.mp3"].status, "Gestito")
            self.assertEqual(tracks_by_name["b.mp3"].status, "Da gestire")

    def test_rep003_status_column_shows_managed_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (split / "Lenti").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set(["Balli", "Lenti"])
            self.app._rep003_assign_selected()

            track_after = list(self.app._rep003_tracks_tree.get_children(""))[0]
            values = self.app._rep003_tracks_tree.item(track_after, "values")
            self.assertEqual(values[1], "Gestito (2)")

    def _prepare_rep003_tracks_for_folders_sort(self) -> None:
        self.app.open_repertory_new_tracks_window()
        folders = [
            RepertoryFolderItem(relative_path="", full_path="C:/rep", folder_name="ROOT", direct_mp3_count=0, direct_mp3_size_bytes=0),
            RepertoryFolderItem(relative_path="Italiano", full_path="C:/rep/Italiano", folder_name="Italiano", direct_mp3_count=0, direct_mp3_size_bytes=0),
            RepertoryFolderItem(relative_path="Balli", full_path="C:/rep/Balli", folder_name="Balli", direct_mp3_count=0, direct_mp3_size_bytes=0),
        ]
        tracks = [
            NewTrackItem(source_path="C:/new/a.mp3", file_name="a.mp3"),
            NewTrackItem(source_path="C:/new/b.mp3", file_name="b.mp3"),
            NewTrackItem(source_path="C:/new/c.mp3", file_name="c.mp3"),
            NewTrackItem(source_path="C:/new/d.mp3", file_name="d.mp3"),
        ]
        self.app._rep003_model.reset()
        self.app._rep003_model.load_folders(folders)
        self.app._rep003_model.load_tracks(tracks)
        self.app._rep003_model.assign_tracks(["C:/new/b.mp3"], ["Italiano"])
        self.app._rep003_model.assign_tracks(["C:/new/c.mp3"], ["."])
        self.app._rep003_model.assign_tracks(["C:/new/d.mp3"], [".", "Italiano"])
        self.app._rep003_show_managed_var.set(True)
        self.app._rep003_refresh_folders_tree(clear_selection=True)
        self.app._rep003_refresh_tracks_tree(clear_selection=True)

    def test_rep003_sort_cartelle_abbinate_ascending(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        self.app._rep003_sort_tracks_by("folders")

        rows = list(self.app._rep003_tracks_tree.get_children(""))
        rendered = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows]
        self.assertEqual(rendered, ["-", "Italiano", "ROOT", "ROOT, Italiano"])

    def test_rep003_sort_cartelle_abbinate_descending(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        self.app._rep003_sort_tracks_by("folders")
        self.app._rep003_sort_tracks_by("folders")

        rows = list(self.app._rep003_tracks_tree.get_children(""))
        rendered = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows]
        self.assertEqual(rendered, ["ROOT, Italiano", "ROOT", "Italiano", "-"])

    def test_rep003_sort_cartelle_abbinate_root_is_sorted_as_text(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        self.app._rep003_sort_tracks_by("folders")

        rows = list(self.app._rep003_tracks_tree.get_children(""))
        rendered = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows]
        self.assertLess(rendered.index("Italiano"), rendered.index("ROOT"))

    def test_rep003_sort_cartelle_abbinate_supports_multi_values(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        self.app._rep003_sort_tracks_by("folders")

        rows = list(self.app._rep003_tracks_tree.get_children(""))
        rendered = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows]
        self.assertIn("ROOT, Italiano", rendered)
        self.assertEqual(rendered[-1], "ROOT, Italiano")

    def test_rep003_sort_cartelle_abbinate_renders_unmanaged_as_dash(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        rows_before = list(self.app._rep003_tracks_tree.get_children(""))
        rendered_before = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows_before]
        self.assertIn("-", rendered_before)

        self.app._rep003_sort_tracks_by("folders")

        rows_after = list(self.app._rep003_tracks_tree.get_children(""))
        rendered_after = [str(self.app._rep003_tracks_tree.item(iid, "values")[2]) for iid in rows_after]
        self.assertEqual(rendered_after[0], "-")

    def test_rep003_sort_cartelle_abbinate_preserves_selection_filter_and_model(self) -> None:
        self._prepare_rep003_tracks_for_folders_sort()

        snapshot_before = self.app._rep003_model.assignments_snapshot()
        self.app._rep003_show_managed_var.set(True)

        target_iid = ""
        for iid in self.app._rep003_tracks_tree.get_children(""):
            values = self.app._rep003_tracks_tree.item(iid, "values")
            if str(values[0]) == "d.mp3":
                target_iid = str(iid)
                break
        self.assertTrue(bool(target_iid))

        self.app._rep003_tracks_tree.selection_set(target_iid)
        self.app._rep003_folders_tree.selection_set(["__ROOT__", "Italiano"])

        self.app._rep003_sort_tracks_by("folders")

        selected_tracks = self.app._rep003_selected_track_paths()
        selected_folders = set(self.app._rep003_selected_folder_relative_paths())
        snapshot_after = self.app._rep003_model.assignments_snapshot()

        self.assertEqual(selected_tracks, ["C:/new/d.mp3"])
        self.assertEqual(selected_folders, {".", "Italiano"})
        self.assertTrue(bool(self.app._rep003_show_managed_var.get()))
        self.assertEqual(snapshot_after, snapshot_before)

    def test_rep003_folders_columns_are_sortable(self) -> None:
        self.app.open_repertory_new_tracks_window()
        self.app._rep003_model.load_folders(
            [
                RepertoryFolderItem(relative_path="B", full_path="C:/rep/B", folder_name="B", direct_mp3_count=2, direct_mp3_size_bytes=20),
                RepertoryFolderItem(relative_path="A", full_path="C:/rep/A", folder_name="A", direct_mp3_count=5, direct_mp3_size_bytes=50),
                RepertoryFolderItem(relative_path="", full_path="C:/rep", folder_name="ROOT", direct_mp3_count=1, direct_mp3_size_bytes=10),
            ]
        )
        self.app._rep003_refresh_folders_tree(clear_selection=True)

        self.app._rep003_sort_folders_by("count")
        first_iid = self.app._rep003_folders_tree.get_children("")[0]
        first_values = self.app._rep003_folders_tree.item(first_iid, "values")
        self.assertEqual(first_values[1], ".")

        self.app._rep003_sort_folders_by("count")
        first_iid_desc = self.app._rep003_folders_tree.get_children("")[0]
        first_values_desc = self.app._rep003_folders_tree.item(first_iid_desc, "values")
        self.assertEqual(first_values_desc[1], "A")

        self.app._rep003_sort_folders_by("size")
        first_iid_size = self.app._rep003_folders_tree.get_children("")[0]
        first_values_size = self.app._rep003_folders_tree.item(first_iid_size, "values")
        self.assertEqual(first_values_size[1], ".")

    def test_rep003_tracks_column_shows_root_label_when_assigned_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "r.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set(["__ROOT__", "Balli"])
            self.app._rep003_assign_selected()

            track_after = list(self.app._rep003_tracks_tree.get_children(""))[0]
            values = self.app._rep003_tracks_tree.item(track_after, "values")
            self.assertIn("ROOT", str(values[2]))
            self.assertIn("Balli", str(values[2]))

    def test_rep003_window_geometry_is_balanced_and_within_screen(self) -> None:
        self.app.open_repertory_new_tracks_window()
        self.assertIsNotNone(self.app._rep003_window)
        window = self.app._rep003_window
        assert window is not None
        window.update_idletasks()
        width, height = self._parse_geometry_size(window.geometry())
        screen_w = int(window.winfo_screenwidth())
        screen_h = int(window.winfo_screenheight())

        self.assertLessEqual(width, screen_w)
        self.assertLessEqual(height, screen_h)
        self.assertGreaterEqual(width, 1020)
        self.assertGreaterEqual(height, 640)

    def test_rep003_stress_1000_tracks_200_folders_sort_switch_assign_remove(self) -> None:
        self.app.open_repertory_new_tracks_window()

        folders = [
            RepertoryFolderItem(
                relative_path=("" if index == 0 else f"Folder_{index:03d}"),
                full_path=f"C:/rep/{index:03d}",
                folder_name=("ROOT" if index == 0 else f"Folder_{index:03d}"),
                direct_mp3_count=index % 11,
                direct_mp3_size_bytes=(index + 1) * 1024,
            )
            for index in range(200)
        ]
        tracks = [
            NewTrackItem(source_path=f"C:/new/track_{index:04d}.mp3", file_name=f"track_{index:04d}.mp3")
            for index in range(1000)
        ]
        self.app._rep003_model.reset()
        self.app._rep003_model.load_folders(folders)
        self.app._rep003_model.load_tracks(tracks)
        self.app._rep003_refresh_folders_tree(clear_selection=True)
        self.app._rep003_refresh_tracks_tree(clear_selection=True)

        self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 1000)
        self.assertEqual(len(self.app._rep003_folders_tree.get_children("")), 200)

        self.app._rep003_sort_tracks_by("name")
        self.app._rep003_sort_tracks_by("status")
        self.app._rep003_sort_folders_by("relative")
        self.app._rep003_sort_folders_by("count")
        self.app._rep003_sort_folders_by("size")

        row_ids = list(self.app._rep003_tracks_tree.get_children(""))[:3]
        self.app._rep003_tracks_tree.selection_set(row_ids)
        folder_ids = list(self.app._rep003_folders_tree.get_children(""))[:5]
        self.app._rep003_folders_tree.selection_set(folder_ids)
        self.app._rep003_assign_selected()

        self.assertIn("Gestiti: 3", str(self.app._rep003_status_label.cget("text")))

        self.app._rep003_show_managed_var.set(False)
        self.app._rep003_on_show_managed_toggle()
        self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 997)

        self.app._rep003_show_managed_var.set(True)
        self.app._rep003_on_show_managed_toggle()
        self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 1000)

        managed_iids: list[str] = []
        for iid in self.app._rep003_tracks_tree.get_children(""):
            values = self.app._rep003_tracks_tree.item(iid, "values")
            if values and str(values[1]).startswith("Gestito"):
                managed_iids.append(str(iid))
            if len(managed_iids) == 3:
                break
        self.assertEqual(len(managed_iids), 3)
        self.app._rep003_tracks_tree.selection_set(managed_iids)
        self.app._rep003_remove_assignment()
        self.assertIn("Gestiti: 0", str(self.app._rep003_status_label.cget("text")))

    def test_rep003_treeview_style_is_readable(self) -> None:
        self.app.open_repertory_new_tracks_window()
        style = ttk.Style(self.app)

        tree_cfg = style.configure("Rep003.Treeview")
        head_cfg = style.configure("Rep003.Treeview.Heading")

        self.assertEqual(int(tree_cfg.get("rowheight", 0)), 34)
        self.assertTrue(str(tree_cfg.get("font", "")))
        self.assertTrue(str(head_cfg.get("font", "")))

        self.assertEqual(str(self.app._rep003_tracks_tree.cget("selectmode")), "extended")
        self.assertEqual(str(self.app._rep003_folders_tree.cget("selectmode")), "extended")

        folder_columns = tuple(self.app._rep003_folders_tree.cget("columns"))
        self.assertIn("size", folder_columns)
        self.assertGreaterEqual(int(self.app._rep003_folders_tree.column("size", "minwidth")), 190)

    def test_rep003_center_controls_order_and_switch_tooltip(self) -> None:
        self.app.open_repertory_new_tracks_window()
        self.app.update_idletasks()
        if self.app._rep003_window is not None:
            self.app._rep003_window.update_idletasks()

        load_button = self.app._rep003_load_button
        show_switch = self.app._rep003_show_managed_switch
        create_folder_button = self.app._rep003_create_folder_button
        refresh_folders_button = self.app._rep003_refresh_folders_button
        assign_button = self.app._rep003_assign_button
        remove_button = self.app._rep003_remove_button

        self.assertIsNotNone(load_button)
        self.assertIsNotNone(show_switch)
        self.assertIsNotNone(create_folder_button)
        self.assertIsNotNone(refresh_folders_button)
        self.assertIsNotNone(assign_button)
        self.assertIsNotNone(remove_button)

        self.assertTrue(bool(load_button.winfo_exists()))
        self.assertIs(load_button.master, show_switch.master)
        self.assertTrue(bool(load_button.winfo_ismapped()))
        self.assertTrue(bool(show_switch.winfo_ismapped()))
        self.assertTrue(bool(create_folder_button.winfo_ismapped()))
        self.assertTrue(bool(refresh_folders_button.winfo_ismapped()))
        self.assertTrue(bool(assign_button.winfo_ismapped()))
        self.assertTrue(bool(remove_button.winfo_ismapped()))

        self.assertEqual(int(load_button.grid_info().get("row", -1)), 0)
        self.assertEqual(int(show_switch.grid_info().get("row", -1)), 1)
        self.assertEqual(int(create_folder_button.grid_info().get("row", -1)), 2)
        self.assertEqual(int(refresh_folders_button.grid_info().get("row", -1)), 3)
        self.assertEqual(int(assign_button.grid_info().get("row", -1)), 4)
        self.assertEqual(int(remove_button.grid_info().get("row", -1)), 5)

        switch_tips = [tip for tip in self.app.tooltips if getattr(tip, "widget", None) is show_switch]
        self.assertTrue(any("Attivo: mostra anche i brani gia abbinati." in str(getattr(tip, "text", "")) for tip in switch_tips))

        self.assertEqual(str(remove_button.cget("text")), "Elimina abbinamento")
        self.assertEqual(str(remove_button.cget("state")), "disabled")
        self.assertLessEqual(int(load_button.cget("height")), 46)
        self.assertLessEqual(int(remove_button.cget("height")), 46)

    def test_rep003_create_folder_dialog_is_custom_modal_and_keyboard_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split = root / "split"
            general = root / "general"
            split.mkdir()
            general.mkdir()
            (split / "Balli").mkdir()

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._rep003_refresh_folders_only(select_relative="Balli")
            balli_iid = self._rep003_iid_for_relative("Balli")
            self.app._rep003_folders_tree.selection_set(balli_iid)
            self.assertIn("Balli", self.app._rep003_selected_folder_relative_paths())
            self.app._rep003_create_folder()

            dialog = self.app._rep003_create_folder_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            dialog.update()

            width, height = self._parse_geometry_size(dialog.geometry())
            self.assertGreaterEqual(width, 520)
            self.assertGreaterEqual(height, 260)
            self.assertEqual(dialog.title(), "Crea nuova cartella")
            self.assertIs(dialog.grab_current(), dialog)
            focused = dialog.focus_get()
            self.assertIn(focused, (dialog, self.app._rep003_create_folder_entry))

            self.assertTrue(bool(dialog.bind("<Return>")))
            self.assertTrue(bool(dialog.bind("<Escape>")))

            self.app._rep003_create_folder_entry.insert(0, "Nuova")
            create_button = self._find_button_by_text(dialog, "Crea")
            self.assertIsNotNone(create_button)
            create_button.invoke()
            self.app.update()
            self.assertTrue((split / "Balli" / "Nuova").is_dir())
            self.assertIsNone(self.app._rep003_create_folder_dialog)

    def test_rep003_create_folder_dialog_escape_cancels_without_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split = root / "split"
            general = root / "general"
            split.mkdir()
            general.mkdir()

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._rep003_create_folder()

            dialog = self.app._rep003_create_folder_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            if self.app._rep003_create_folder_entry is not None:
                self.app._rep003_create_folder_entry.insert(0, "Annullata")
            dialog.focus_force()
            self.app.update()
            dialog.event_generate("<Escape>", when="tail")
            self.app.update()

            self.assertFalse((split / "Annullata").exists())
            self.assertIsNone(self.app._rep003_create_folder_dialog)

    def test_rep003_create_folder_then_refresh_preserves_assignments_and_flags_missing_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            balli_iid = self._rep003_iid_for_relative("Balli")
            self.app._rep003_folders_tree.selection_set(balli_iid)
            self.app._rep003_assign_selected()

            self.app._rep003_folders_tree.selection_set(balli_iid)
            self.app._rep003_create_folder()
            self.assertIsNotNone(self.app._rep003_create_folder_dialog)
            if self.app._rep003_create_folder_entry is not None:
                self.app._rep003_create_folder_entry.insert(0, "Nuova")
            assert self.app._rep003_create_folder_dialog is not None
            create_button = self._find_button_by_text(self.app._rep003_create_folder_dialog, "Crea")
            self.assertIsNotNone(create_button)
            create_button.invoke()
            self.app.update()

            self.assertTrue((split / "Balli" / "Nuova").is_dir())
            self.assertIn("Balli/Nuova", set(self.app._rep003_folder_iid_by_relative.values()))
            selected_folders = self.app._rep003_selected_folder_relative_paths()
            self.assertIn("Balli/Nuova", selected_folders)

            (split / "Balli").rename(split / "Balli_removed")
            self.app._rep003_refresh_folders_only()

            rows = list(self.app._rep003_tracks_tree.get_children(""))
            self.assertEqual(len(rows), 1)
            values = self.app._rep003_tracks_tree.item(rows[0], "values")
            self.assertIn("NON DISPONIBILE", str(values[2]))
            self.assertIn("non disponibili: 1", str(values[1]))

    def test_rep003_create_folder_dialog_x_closes_and_clears_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split = root / "split"
            general = root / "general"
            split.mkdir()
            general.mkdir()

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._rep003_create_folder()

            dialog = self.app._rep003_create_folder_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            close_handler = dialog.protocol("WM_DELETE_WINDOW")
            self.assertTrue(bool(close_handler))
            dialog.tk.call(close_handler)
            self.app.update_idletasks()

            self.assertIsNone(self.app._rep003_create_folder_dialog)
            self.assertIsNone(self.app._rep003_create_folder_entry)
            self.assertEqual(str(self.app._rep003_create_folder_preview_var.get()), "")

    def test_rep003_create_folder_dialog_cancel_button_clears_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split = root / "split"
            general = root / "general"
            split.mkdir()
            general.mkdir()

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._rep003_create_folder()

            dialog = self.app._rep003_create_folder_dialog
            self.assertIsNotNone(dialog)
            assert dialog is not None
            cancel_button = self._find_button_by_text(dialog, "Annulla")
            self.assertIsNotNone(cancel_button)
            cancel_button.invoke()
            self.app.update_idletasks()

            self.assertIsNone(self.app._rep003_create_folder_dialog)
            self.assertIsNone(self.app._rep003_create_folder_entry)
            self.assertFalse((split / "Annullata").exists())

    def test_rep003_create_folder_dialog_enter_creates_folder_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            split = root / "split"
            general = root / "general"
            split.mkdir()
            general.mkdir()

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._rep003_refresh_folders_only(select_relative=".")
            self.app._rep003_folders_tree.selection_set("__ROOT__")
            self.app._rep003_create_folder()

            self.assertIsNotNone(self.app._rep003_create_folder_dialog)
            assert self.app._rep003_create_folder_entry is not None
            self.app._rep003_create_folder_entry.insert(0, "Nuova")
            create_button = self._find_button_by_text(self.app._rep003_create_folder_dialog, "Crea")
            self.assertIsNotNone(create_button)
            create_button.invoke()
            self.app.update()

            self.assertTrue((split / "Nuova").is_dir())
            self.assertIsNone(self.app._rep003_create_folder_dialog)
            selected_folders = self.app._rep003_selected_folder_relative_paths()
            self.assertIn("Nuova", selected_folders)

    def test_rep003_size_format_binary_units(self) -> None:
        self.assertEqual(self.app._rep003_format_binary_size(999), "999 B")
        self.assertEqual(self.app._rep003_format_binary_size(1024), "1.00 KB")
        self.assertEqual(self.app._rep003_format_binary_size(1024 * 1024), "1.00 MB")
        self.assertEqual(self.app._rep003_format_binary_size(1024 * 1024 * 1024), "1.00 GB")

    def test_mode_switch_with_all_managed_tracks_shows_no_warning(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("insert_tracks")
        self.app._apply_repertory_mode_layout()

        tracks = [NewTrackItem(source_path=f"C:/new/{idx}.mp3", file_name=f"{idx}.mp3") for idx in range(7)]
        folders = [
            RepertoryFolderItem(
                relative_path="Balli",
                full_path="C:/rep/Balli",
                folder_name="Balli",
                direct_mp3_count=1,
                direct_mp3_size_bytes=128,
            )
        ]
        self.app._rep003_model.reset()
        self.app._rep003_model.load_tracks(tracks)
        self.app._rep003_model.load_folders(folders)
        self.app._rep003_model.assign_tracks([row.source_path for row in tracks], ["Balli"])
        self.app._rep003_refresh_tracks_tree(clear_selection=True)
        self.app._rep003_refresh_folders_tree(clear_selection=True)
        self.app._rep003_update_status()

        self.app._rep003_show_managed_var.set(False)
        self.app._rep003_on_show_managed_toggle()
        self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 0)

        # All tracks are managed but not yet processed by phase 2: this is a pending-assignments state.
        self.app._rep003_update_session_state_from_model()
        self.assertEqual(self.app._rep003_session_state, "ASSIGNMENTS_PENDING")
        self.assertEqual(self.app._rep003_count_assigned_tracks(), 7)

        with mock.patch.object(self.app, "_show_rep003_confirmation_dialog", return_value=True) as confirm_mock:
            self.app._on_repertory_mode_selected("Aggiornamento Repertorio")
            self.assertTrue(confirm_mock.called)

        self.assertEqual(str(self.app._repertory_mode_var.get()), "update")

    def test_mode_switch_with_no_rep003_tracks_shows_no_warning(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("insert_tracks")
        self.app._apply_repertory_mode_layout()
        self.app._rep003_model.reset()
        self.app._rep003_refresh_tracks_tree(clear_selection=True)
        self.app._rep003_update_status()

        with mock.patch("gui.messagebox.askyesno") as ask_mock:
            self.app._on_repertory_mode_selected("Diagnosi Repertorio")
            ask_mock.assert_not_called()

        self.assertEqual(str(self.app._repertory_mode_var.get()), "diagnostics")

    def test_mode_switch_with_unmanaged_rep003_tracks_remains_non_blocking(self) -> None:
        self.app.open_repertory_organizer_window()
        self.app._set_repertory_mode("insert_tracks")
        self.app._apply_repertory_mode_layout()
        self.app._rep003_model.reset()
        self.app._rep003_model.load_tracks([NewTrackItem(source_path="C:/new/u.mp3", file_name="u.mp3")])
        self.app._rep003_refresh_tracks_tree(clear_selection=True)
        self.app._rep003_update_status()

        with mock.patch("gui.messagebox.askyesno") as ask_mock:
            self.app._on_repertory_mode_selected("Aggiornamento Repertorio")
            ask_mock.assert_not_called()

        self.assertEqual(str(self.app._repertory_mode_var.get()), "update")

    def test_mode_switch_with_pending_rep003_assignments_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog", return_value=False) as confirm_mock:
                self.app._on_repertory_mode_selected("Diagnosi Repertorio")
                self.assertTrue(confirm_mock.called)

            self.assertEqual(str(self.app._repertory_mode_var.get()), "insert_tracks")
            self.assertEqual(self.app._rep003_count_assigned_tracks(), 1)

    def test_mode_switch_with_pending_rep003_assignments_can_be_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog", return_value=True) as confirm_mock:
                self.app._on_repertory_mode_selected("Diagnosi Repertorio")
                self.assertTrue(confirm_mock.called)

            self.assertEqual(str(self.app._repertory_mode_var.get()), "diagnostics")
            self.assertEqual(self.app._rep003_count_assigned_tracks(), 0)

    def test_close_with_pending_rep003_assignments_uses_user_friendly_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog", return_value=False) as confirm_mock:
                self.app._close_repertory_window()
                self.assertTrue(confirm_mock.called)
                kwargs = confirm_mock.call_args.kwargs
                self.assertEqual(kwargs.get("title"), "Abbinamenti non ancora elaborati")
                self.assertNotIn("REP-003", str(kwargs.get("body", "")))

            self.assertIsNotNone(self.app._repertory_dialog)

    def test_rep003_reload_with_pending_assignments_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog", return_value=False) as confirm_mock:
                self.app._rep003_load_sources()
                self.assertTrue(confirm_mock.called)

            self.assertEqual(self.app._rep003_count_assigned_tracks(), 1)

    def test_rep003_reload_after_completion_does_not_prompt_discard_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()
            self.app._rep003_session_state = "COMPLETED"

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog") as confirm_mock:
                self.app._rep003_load_sources()
                confirm_mock.assert_not_called()

            self.assertEqual(self.app._rep003_count_assigned_tracks(), 0)
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

    def test_rep003_finalize_starts_worker_with_memorized_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            previous_session = root / "Diagnosi_Inserimento_Repertorio_20990101_000000"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            previous_session.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            fake_worker = _FakeRep003Worker()
            self.app.rep003_worker = fake_worker

            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            self.app._set_repertory_results_folder_for_mode("insert_tracks", str(previous_session))
            self.app._rep003_session_state = "READY_FOR_NEW_SESSION"
            self.app._update_repertory_open_results_button_state()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

            self.app._rep003_finalize_placeholder()

            self.assertEqual(len(fake_worker.start_calls), 1)
            start_args = fake_worker.start_calls[0]
            self.assertEqual(start_args["new_tracks_dir"], str(new_tracks))
            self.assertEqual(start_args["split_repertory_dir"], str(split))
            self.assertEqual(start_args["general_repertory_dir"], str(general))
            self.assertEqual(start_args["smartphone_tablet_dir"], str(smartphone))
            self.assertEqual(start_args["assignments_snapshot"][str(new_tracks / "a.mp3")]["status"], "Gestito")
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._active_repertory_results_folder()), "")

    def test_rep003_open_results_stays_disabled_in_phase1_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_organizer_window()
            self.app._set_repertory_mode("insert_tracks")
            self.app._apply_repertory_mode_layout()

            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

            self.app._rep003_finalize_placeholder()
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")

    def test_rep003_completed_enables_results_and_android_buttons(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as smartphone_temp_dir, tempfile.TemporaryDirectory() as session_temp_dir:
            smartphone_folder = Path(smartphone_temp_dir)
            session_folder = Path(session_temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            if self.app._rep003_smartphone_entry is not None:
                self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone_folder))

            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=3,
                processed_tracks=3,
                copied_tracks=2,
                updated_tracks=1,
                kept_tracks=1,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=1.2,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={
                    "tracks_inserted": 2,
                    "tracks_updated": 1,
                    "split_copied": 2,
                    "split_updated": 1,
                    "general_copied": 1,
                    "general_updated": 2,
                    "android_copied": 3,
                    "android_updated": 1,
                    "backups_created": 4,
                    "errors": 0,
                },
            )

            with mock.patch.object(self.app, "_show_rep003_completion_summary") as summary_mock:
                self.app._handle_rep003_worker_completed(result)
                summary_mock.assert_called_once_with(result)

            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "normal")

    def test_rep003_completion_popup_contains_only_close_button(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.2,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 0},
            )

            parent = self.app._rep003_window if self.app._rep003_window is not None else self.app
            button_texts: list[str] = []
            close_command_holder: dict[str, object] = {}

            def _button_factory(*args, **kwargs):
                text = str(kwargs.get("text", ""))
                button_texts.append(text)
                if text == "Chiudi":
                    close_command_holder["command"] = kwargs.get("command")
                button = mock.MagicMock()
                button.grid.return_value = None
                button.focus_force.return_value = None
                return button

            def _wait_window_side_effect(_dialog) -> None:
                close_command = close_command_holder.get("command")
                if callable(close_command):
                    close_command()

            with mock.patch.object(parent, "wait_window", side_effect=_wait_window_side_effect):
                with mock.patch("gui.ctk.CTkButton", side_effect=_button_factory):
                    self.app._show_rep003_completion_summary(result)

            self.assertEqual(button_texts.count("Chiudi"), 1)
            self.assertNotIn("Apri cartella risultati", button_texts)
            self.assertNotIn("Apri cartella Android", button_texts)

    def test_rep003_completion_popup_close_keeps_form_and_results_state(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.2,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 0},
            )

            with mock.patch.object(self.app, "_show_rep003_completion_summary"):
                self.app._handle_rep003_worker_completed(result)

            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")
            self.assertEqual(str(self.app._active_repertory_results_folder()), str(session_folder.resolve()))

            parent = self.app._rep003_window if self.app._rep003_window is not None else self.app

            close_command_holder: dict[str, object] = {}

            def _button_factory(*args, **kwargs):
                if str(kwargs.get("text", "")) == "Chiudi":
                    close_command_holder["command"] = kwargs.get("command")
                button = mock.MagicMock()
                button.grid.return_value = None
                button.focus_force.return_value = None
                return button

            def _wait_window_side_effect(_dialog) -> None:
                close_command = close_command_holder.get("command")
                if callable(close_command):
                    close_command()

            with mock.patch.object(parent, "focus_force", wraps=parent.focus_force) as focus_mock:
                with mock.patch.object(parent, "wait_window", side_effect=_wait_window_side_effect):
                    with mock.patch("gui.ctk.CTkButton", side_effect=_button_factory):
                        self.app._show_rep003_completion_summary(result)
                self.assertTrue(focus_mock.called)

            self.assertIsNotNone(self.app._repertory_dialog)
            self.assertEqual(str(self.app._active_repertory_results_folder()), str(session_folder.resolve()))
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

    def test_rep003_completion_resets_operational_state_and_keeps_results_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            new_tracks = root / "new_tracks"
            split = root / "split"
            general = root / "general"
            smartphone = root / "smartphone"
            new_tracks.mkdir()
            split.mkdir()
            general.mkdir()
            smartphone.mkdir()
            (split / "Balli").mkdir()
            (new_tracks / "a.mp3").write_bytes(b"1")

            self.app.open_repertory_new_tracks_window()
            self.app._replace_entry(self.app._rep003_new_tracks_entry, str(new_tracks))
            self.app._replace_entry(self.app._rep003_split_entry, str(split))
            self.app._replace_entry(self.app._rep003_general_entry, str(general))
            self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone))
            self.app._rep003_load_sources()

            track_iid = list(self.app._rep003_tracks_tree.get_children(""))[0]
            self.app._rep003_tracks_tree.selection_set(track_iid)
            self.app._rep003_folders_tree.selection_set("Balli")
            self.app._rep003_assign_selected()

            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.5,
                session_folder=str(root / "Diagnosi_Inserimento_Repertorio_20990101_010101"),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 0},
            )
            Path(result.session_folder).mkdir()

            with mock.patch.object(self.app, "_show_rep003_completion_summary"):
                self.app._handle_rep003_worker_completed(result)

            self.assertEqual(self.app._rep003_session_state, "READY_FOR_NEW_SESSION")
            self.assertEqual(len(self.app._rep003_model.tracks), 0)
            self.assertEqual(len(self.app._rep003_model.folders), 0)
            self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 0)
            self.assertEqual(len(self.app._rep003_folders_tree.get_children("")), 0)
            self.assertIn("Brani caricati: 0", str(self.app._rep003_status_label.cget("text")))
            self.assertIn("Carica cartelle e brani", str(self.app._rep003_status_label.cget("text")))
            self.assertEqual(str(self.app._rep003_assign_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._rep003_remove_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_start_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_stop_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "normal")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "normal")

            with mock.patch.object(self.app, "_show_rep003_confirmation_dialog") as confirm_mock:
                self.app._on_repertory_mode_selected("Diagnosi Repertorio")
                confirm_mock.assert_not_called()

    def test_rep003_completed_with_errors_enters_ready_for_new_session_state(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as smartphone_temp_dir, tempfile.TemporaryDirectory() as session_temp_dir:
            smartphone_folder = Path(smartphone_temp_dir)
            session_folder = Path(session_temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            if self.app._rep003_smartphone_entry is not None:
                self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone_folder))

            result = Rep003UpdateResult(
                success=False,
                interrupted=False,
                error=None,
                total_tracks=2,
                processed_tracks=2,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=1,
                error_tracks=1,
                elapsed_seconds=0.8,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 1},
            )

            with mock.patch.object(self.app, "_show_rep003_completion_summary"):
                self.app._handle_rep003_worker_completed(result)

            self.assertEqual(self.app._rep003_session_state, "READY_FOR_NEW_SESSION")
            self.assertEqual(len(self.app._rep003_model.tracks), 0)
            self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 0)
            self.assertIn("completata con errori", str(self.app._rep003_status_label.cget("text")).casefold())
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

    def test_rep003_close_after_completion_reopens_clean_session(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as smartphone_temp_dir, tempfile.TemporaryDirectory() as session_temp_dir:
            smartphone_folder = Path(smartphone_temp_dir)
            session_folder = Path(session_temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            if self.app._rep003_smartphone_entry is not None:
                self.app._replace_entry(self.app._rep003_smartphone_entry, str(smartphone_folder))

            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.5,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 0},
            )

            with mock.patch.object(self.app, "_show_rep003_completion_summary"):
                self.app._handle_rep003_worker_completed(result)

            model_id_before_close = id(self.app._rep003_model)
            self.app._close_repertory_window()
            self.assertIsNone(self.app._repertory_dialog)

            self.app.open_repertory_new_tracks_window()

            self.assertEqual(self.app._rep003_session_state, "NOT_LOADED")
            self.assertEqual(len(self.app._rep003_model.tracks), 0)
            self.assertEqual(len(self.app._rep003_tracks_tree.get_children("")), 0)
            self.assertEqual(len(self.app._rep003_folders_tree.get_children("")), 0)
            self.assertEqual(str(self.app._rep003_new_tracks_entry.get()).strip(), "")
            self.assertEqual(str(self.app._rep003_split_entry.get()).strip(), "")
            self.assertEqual(str(self.app._rep003_general_entry.get()).strip(), "")
            self.assertEqual(str(self.app._rep003_smartphone_entry.get()).strip(), "")
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_open_smartphone_button.cget("state")), "disabled")
            self.assertEqual(str(self.app._repertory_reset_smartphone_button.cget("state")), "disabled")
            self.assertNotEqual(id(self.app._rep003_model), model_id_before_close)

    def test_rep003_results_button_stays_enabled_after_mode_roundtrip(self) -> None:
        self.app.open_repertory_new_tracks_window()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir) / "Diagnosi_Inserimento_Repertorio_20990101_010101"
            session_folder.mkdir()
            result = Rep003UpdateResult(
                success=True,
                interrupted=False,
                error=None,
                total_tracks=1,
                processed_tracks=1,
                copied_tracks=1,
                updated_tracks=0,
                kept_tracks=0,
                skipped_tracks=0,
                error_tracks=0,
                elapsed_seconds=0.5,
                session_folder=str(session_folder),
                report_paths={"csv": "x.csv", "html": "x.html", "xlsx": "x.xlsx"},
                log_path="x.log",
                records=[],
                counters={"errors": 0},
            )

            with mock.patch.object(self.app, "_show_rep003_completion_summary"):
                self.app._handle_rep003_worker_completed(result)

            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")
            self.app._on_repertory_mode_selected("Diagnosi Repertorio")
            self.app._on_repertory_mode_selected("Inserimento nuovi brani")
            self.assertEqual(str(self.app._repertory_open_results_button.cget("state")), "normal")

    def test_rep003_global_decision_is_reused_without_dialog(self) -> None:
        self.app.open_repertory_new_tracks_window()
        fake_worker = _FakeRep003Worker()
        self.app.rep003_worker = fake_worker
        self.app._rep003_session_policy = "UPDATE_ALL"

        payload = {
            "request_id": "req-1",
            "source_path": "C:/new/a.mp3",
            "existing_paths": ["C:/split/a.mp3"],
        }
        self.app._handle_rep003_worker_decision_required(payload)
        self.assertEqual(fake_worker.submitted, [("req-1", "UPDATE_AND_BYPASS_SESSION")])
        self.assertIsNone(self.app._rep003_decision_dialog)

    def test_main_toolbar_hides_standalone_rep003_button(self) -> None:
        texts = self._collect_widget_texts(self.app)
        self.assertIn("Organizza repertorio", texts)
        self.assertNotIn("Inserimento nuovi brani", texts)
        self.assertIn("Progetto: Nessuno", texts)

    def test_repertory_window_mode_selector_includes_insert_tracks(self) -> None:
        self.app.open_repertory_organizer_window()
        radios = list(self.app._repertory_mode_radios)
        self.assertEqual(len(radios), 3)
        values = [str(radio.cget("text")) for radio in radios]
        self.assertIn("Aggiornamento Repertorio", values)
        self.assertIn("Diagnosi Repertorio", values)
        self.assertIn("Inserimento nuovi brani", values)

    def test_root_geometry_uses_stable_baseline_formula(self) -> None:
        width, height = self._parse_geometry_size(self.app.geometry())
        expected_width = min(1180, max(900, self.app.winfo_screenwidth() - 80))
        expected_height = min(790, max(650, self.app.winfo_screenheight() - 120))
        self.assertEqual(width, expected_width)
        self.assertEqual(height, expected_height)
        self.assertGreaterEqual(height, 650)


class MixCreatorAppLifecycleTests(unittest.TestCase):
    @staticmethod
    def _safe_destroy(app: MixCreatorApp | None) -> None:
        if app is None:
            return
        try:
            if app.winfo_exists():
                app.destroy()
        except Exception:
            pass

    @staticmethod
    def _is_closed(app: MixCreatorApp) -> bool:
        try:
            return app.winfo_exists() == 0
        except (tk.TclError, RuntimeError):
            return True

    def test_repeated_create_destroy_does_not_exhaust_tk_resources(self) -> None:
        for _ in range(20):
            app = None
            try:
                app = MixCreatorApp()
                app.withdraw()
                app.update_idletasks()
            finally:
                if app is not None:
                    try:
                        grab_widget = app.grab_current()
                    except (tk.TclError, RuntimeError, AttributeError):
                        grab_widget = None
                    if grab_widget is not None:
                        try:
                            grab_widget.grab_release()
                        except (tk.TclError, RuntimeError):
                            pass
                    app.destroy()

        app = MixCreatorApp()
        try:
            app.withdraw()
            app.update_idletasks()
        finally:
            app.destroy()

    def test_on_close_without_worker_closes_without_attribute_error(self) -> None:
        app = MixCreatorApp()
        try:
            app.withdraw()
            app.worker = None
            app.on_close()
            self.assertTrue(self._is_closed(app))
        finally:
            self._safe_destroy(app)

    def test_on_close_with_idle_worker_closes_normally(self) -> None:
        app = MixCreatorApp()
        try:
            app.withdraw()
            app.worker = _FakeClosableWorker(running=False)
            app.on_close()
            self.assertTrue(self._is_closed(app))
        finally:
            self._safe_destroy(app)

    def test_on_close_with_active_worker_requests_cancel_and_preserves_flow(self) -> None:
        app = MixCreatorApp()
        try:
            app.withdraw()
            active_worker = _FakeClosableWorker(running=True)
            app.worker = active_worker
            with mock.patch("gui.messagebox.askyesno", return_value=True) as ask_mock:
                app.on_close()
                self.assertTrue(ask_mock.called)
            self.assertEqual(active_worker.cancel_calls, 1)
            self.assertIsNotNone(getattr(app, "_shutdown_after_job", None))
            app.on_close(_shutdown_after_cancel=True)
            self.assertTrue(self._is_closed(app))
        finally:
            self._safe_destroy(app)


if __name__ == "__main__":
    unittest.main()
