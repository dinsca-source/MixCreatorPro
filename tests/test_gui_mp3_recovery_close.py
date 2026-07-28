# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Callable

import gui as gui_module
from gui import MixCreatorApp
from mp3_recovery_batch import MP3BatchOutcome, MP3RecoveryBatchResult


class _FakeThread:
    def __init__(self, owner: "_FakeRecoveryWorker") -> None:
        self._owner = owner

    def is_alive(self) -> bool:
        return self._owner._running


class _FakeRecoveryWorker:
    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.cancel_calls = 0
        self.join_calls = 0
        self.terminal_callback_count = 0
        self._thread = _FakeThread(self)

    @property
    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancel_calls += 1

    def join(self, timeout: float | None = None) -> None:
        self.join_calls += 1

    def emit_terminal_callback_once(self) -> None:
        if self.terminal_callback_count == 0:
            self.terminal_callback_count = 1


class GuiMp3RecoveryCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = MixCreatorApp()
        self.app.withdraw()
        self.app._reset_to_initial_state()

        self._orig_askyesno = gui_module.messagebox.askyesno
        self._orig_showinfo = gui_module.messagebox.showinfo
        self._orig_showwarning = gui_module.messagebox.showwarning
        self._orig_showerror = gui_module.messagebox.showerror

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

    def _fake_interrupted_result(self) -> MP3RecoveryBatchResult:
        return MP3RecoveryBatchResult(
            success=True,
            interrupted=True,
            error=None,
            total_problematic=1,
            processed_problematic=0,
            counters={MP3BatchOutcome.INTERRUPTED.value: 1},
            elapsed_seconds=0.1,
            report_paths={"csv": "mock.csv"},
            output_root="mock_out",
            items=[],
            originals_unchanged=True,
        )

    def test_gui_a_interrupt_then_close(self) -> None:
        self.app.open_mp3_recovery_window()
        fake_worker = _FakeRecoveryWorker(running=True)
        self.app.recovery_worker = fake_worker

        asks: list[str] = []

        def _askyesno(_title: str, message: str, **kwargs) -> bool:
            asks.append(message)
            return True

        gui_module.messagebox.askyesno = _askyesno

        self.app._set_recovery_ui_running_state(True)
        self.app._request_stop_mp3_recovery()
        self.assertEqual(fake_worker.cancel_calls, 1)

        fake_worker._running = False
        self.app._handle_recovery_worker_completed(self._fake_interrupted_result())

        self.assertFalse(self.app.recovery_worker.is_running)
        self.assertEqual(str(self.app._recovery_start_button.cget("state")), "normal")
        self.assertEqual(str(self.app._recovery_stop_button.cget("state")), "disabled")

        asks_before_close = len(asks)
        self.app._close_recovery_window()
        self.assertIsNone(self.app._recovery_dialog)
        self.assertEqual(len(asks), asks_before_close)

    def test_gui_b_main_close_with_active_recovery_polls_non_blocking(self) -> None:
        fake_worker = _FakeRecoveryWorker(running=True)
        self.app.recovery_worker = fake_worker

        gui_module.messagebox.askyesno = lambda *args, **kwargs: True
        self.app._confirm_save_if_dirty = lambda: True

        after_callbacks: list[Callable[[], None]] = []
        destroy_running_state: list[bool] = []
        save_calls = {"count": 0}
        destroy_calls = {"count": 0}

        original_after = self.app.after
        original_save_settings = self.app.save_settings
        original_destroy = self.app.destroy

        def _after(_delay_ms: int, callback):
            after_callbacks.append(callback)
            return "after-id"

        def _save_settings() -> None:
            save_calls["count"] += 1

        def _destroy() -> None:
            destroy_calls["count"] += 1
            destroy_running_state.append(fake_worker.is_running)

        self.app.after = _after
        self.app.save_settings = _save_settings
        self.app.destroy = _destroy
        try:
            self.app.on_close()
            self.assertEqual(fake_worker.cancel_calls, 1)
            self.assertEqual(destroy_calls["count"], 0)
            self.assertTrue(after_callbacks)

            first_poll = after_callbacks.pop(0)
            first_poll()
            self.assertEqual(destroy_calls["count"], 0)
            self.assertTrue(after_callbacks)

            fake_worker._running = False
            fake_worker.emit_terminal_callback_once()
            second_poll = after_callbacks.pop(0)
            second_poll()

            self.assertEqual(fake_worker.join_calls, 0)
            self.assertEqual(destroy_calls["count"], 1)
            self.assertEqual(save_calls["count"], 1)
            self.assertEqual(fake_worker.terminal_callback_count, 1)
            self.assertEqual(destroy_running_state, [False])
            self.assertFalse(fake_worker._thread.is_alive())
        finally:
            self.app.after = original_after
            self.app.save_settings = original_save_settings
            self.app.destroy = original_destroy


if __name__ == "__main__":
    unittest.main()
