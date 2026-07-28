# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from unittest import mock

import gui as gui_module
from gui import MixCreatorApp
from pathlib import Path


class _FakeRecoveryWorker:
    def __init__(self, running: bool = False) -> None:
        self._running = running
        self.cancel_calls = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancel_calls += 1


class GuiMp3RecoveryLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
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
        gui_module.messagebox.askyesno = self._orig_askyesno
        gui_module.messagebox.showinfo = self._orig_showinfo
        gui_module.messagebox.showwarning = self._orig_showwarning
        gui_module.messagebox.showerror = self._orig_showerror
        try:
            if self.app.winfo_exists():
                self.app.destroy()
        except Exception:
            pass

    def test_buttons_are_created(self) -> None:
        self.app.open_mp3_recovery_window()
        self.assertIsNotNone(self.app._recovery_start_button)
        self.assertIsNotNone(self.app._recovery_stop_button)
        self.assertIsNotNone(self.app._recovery_close_button)
        self.assertIsNotNone(self.app._recovery_open_results_button)
        self.assertIsNotNone(self.app._recovery_mode_normal_radio)
        self.assertIsNotNone(self.app._recovery_mode_forced_radio)

    def test_close_and_open_results_buttons_are_swapped(self) -> None:
        self.app.open_mp3_recovery_window()
        close_col = int(self.app._recovery_close_button.grid_info().get("column", -1))
        open_col = int(self.app._recovery_open_results_button.grid_info().get("column", -1))
        self.assertEqual(close_col, 3)
        self.assertEqual(open_col, 2)

    def test_recovery_window_reopen_builds_fresh_widgets(self) -> None:
        self.app.open_mp3_recovery_window()
        first = self.app._recovery_dialog
        self.assertIsNotNone(first)
        self.app._close_recovery_window()
        self.assertIsNone(self.app._recovery_dialog)

        self.app.open_mp3_recovery_window()
        second = self.app._recovery_dialog
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertIsNotNone(self.app._recovery_log_box)
        self.assertIsNotNone(self.app._recovery_start_button)

    def test_default_mode_is_normal(self) -> None:
        self.app.open_mp3_recovery_window()
        self.assertEqual(self.app._recovery_mode_var.get(), "normal")

    def test_command_bar_is_fixed_container_on_toplevel(self) -> None:
        self.app.open_mp3_recovery_window()
        self.assertIsNotNone(self.app._recovery_dialog)
        self.assertIsNotNone(self.app._recovery_command_bar)
        self.assertEqual(self.app._recovery_command_bar.master, self.app._recovery_dialog)
        self.assertNotEqual(self.app._recovery_command_bar, self.app._recovery_monitor_frame)
        self.assertNotEqual(self.app._recovery_command_bar, self.app._recovery_log_box)

    def test_log_row_has_weight_one(self) -> None:
        self.app.open_mp3_recovery_window()
        content_frame = self.app._recovery_monitor_frame.master.master
        row_weight = int(content_frame.grid_rowconfigure(6)["weight"])
        self.assertEqual(row_weight, 1)

    def test_command_bar_row_has_weight_zero(self) -> None:
        self.app.open_mp3_recovery_window()
        dialog = self.app._recovery_dialog
        self.assertIsNotNone(dialog)
        row_weight = int(dialog.grid_rowconfigure(1)["weight"])
        self.assertEqual(row_weight, 0)

    def test_vertical_resize_does_not_hide_buttons_programmatically(self) -> None:
        self.app.open_mp3_recovery_window()
        self.app._recovery_dialog.geometry("760x540")
        self.app._recovery_dialog.update_idletasks()
        self.assertEqual(str(self.app._recovery_start_button.winfo_manager()), "grid")
        self.assertEqual(str(self.app._recovery_stop_button.winfo_manager()), "grid")
        self.assertEqual(str(self.app._recovery_close_button.winfo_manager()), "grid")
        self.assertEqual(int(self.app._recovery_start_button.grid_info().get("row", -1)), 0)
        self.assertEqual(int(self.app._recovery_stop_button.grid_info().get("row", -1)), 0)
        self.assertEqual(int(self.app._recovery_close_button.grid_info().get("row", -1)), 0)

    def test_button_callbacks_remain_existing_ones(self) -> None:
        calls = {"start": 0, "stop": 0, "close": 0}

        def _start() -> None:
            calls["start"] += 1

        def _stop() -> None:
            calls["stop"] += 1

        def _close() -> None:
            calls["close"] += 1

        self.app._start_mp3_recovery = _start
        self.app._request_stop_mp3_recovery = _stop
        self.app._close_recovery_window = _close
        self.app.open_mp3_recovery_window()

        self.app._recovery_start_button.invoke()
        self.app._recovery_stop_button.configure(state="normal")
        self.app._recovery_stop_button.invoke()
        self.app._recovery_close_button.invoke()

        self.assertEqual(calls, {"start": 1, "stop": 1, "close": 1})

    def test_forced_confirmation_cancel_prevents_batch_start(self) -> None:
        self.app.open_mp3_recovery_window()
        self.app._recovery_problematic_entry.insert(0, str(self.app.input_folder or Path.cwd()))
        self.app._recovery_original_entry.insert(0, str(self.app.input_folder or Path.cwd()))
        self.app._recovery_output_entry.delete(0, "end")
        self.app._recovery_output_entry.insert(0, str(Path.cwd()))
        self.app._recovery_mode_var.set("forced")
        self.app._confirm_forced_recovery = lambda: False
        started = {"count": 0}

        def _start(**kwargs):
            started["count"] += 1

        self.app.recovery_worker.start = _start
        with mock.patch("gui.messagebox.showerror"):
            self.app._start_mp3_recovery()
        self.assertEqual(started["count"], 0)

    def test_window_respects_screen_margins(self) -> None:
        width, height, h_margin, v_margin = self.app._compute_recovery_window_geometry(1366, 768)
        self.assertLessEqual(width, 1366 - h_margin)
        self.assertLessEqual(height, 768 - v_margin)
        self.assertGreaterEqual(width, 860)
        self.assertGreaterEqual(height, 620)

    def test_monitor_and_log_are_in_resizable_split(self) -> None:
        self.app.open_mp3_recovery_window()
        monitor_parent = self.app._recovery_monitor_frame.master
        log_container = self.app._recovery_log_box.master
        self.assertEqual(type(monitor_parent).__name__, "PanedWindow")
        self.assertEqual(monitor_parent, log_container.master)

    def test_log_initial_height_is_not_single_line(self) -> None:
        self.app.open_mp3_recovery_window()
        configured_height = int(self.app._recovery_log_box.cget("height"))
        self.assertGreaterEqual(configured_height, 220)

    def test_monitor_fields_are_present(self) -> None:
        self.app.open_mp3_recovery_window()
        self.assertIsNotNone(self.app._recovery_examined_label)
        self.assertIsNotNone(self.app._recovery_completed_label)
        self.assertIsNotNone(self.app._recovery_batch_status_label)
        self.assertIsNotNone(self.app._recovery_current_file_label)
        self.assertIsNotNone(self.app._recovery_phase_label)
        self.assertIsNotNone(self.app._recovery_elapsed_label)
        self.assertIsNotNone(self.app._recovery_current_file_elapsed_label)
        self.assertIsNotNone(self.app._recovery_eta_label)
        self.assertIsNotNone(self.app._recovery_percent_label)

    def test_close_cancels_timer_and_respects_worker_state(self) -> None:
        self.app.open_mp3_recovery_window()
        fake_worker = _FakeRecoveryWorker(running=False)
        self.app.recovery_worker = fake_worker
        self.app._recovery_timer_job = "timer-id"
        cancelled: list[str] = []
        original_after_cancel = self.app.after_cancel

        def _after_cancel(job_id):
            cancelled.append(str(job_id))

        self.app.after_cancel = _after_cancel
        try:
            self.app._close_recovery_window()
        finally:
            self.app.after_cancel = original_after_cancel

        self.assertIn("timer-id", cancelled)
        self.assertIsNone(self.app._recovery_timer_job)
        self.assertEqual(fake_worker.cancel_calls, 0)


if __name__ == "__main__":
    unittest.main()
