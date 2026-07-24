# -*- coding: utf-8 -*-

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest import mock

import worker
from mp3_diagnostics import MP3DiagnosticsCancelled, MP3DiagnosticsError


class _ImmediateThread:
    def __init__(self, *, target, kwargs, daemon):
        self._target = target
        self._kwargs = kwargs
        self.daemon = daemon

    def start(self):
        self._target(**self._kwargs)


class _FakeDiagnosticsEngine:
    last_kwargs = None

    def run_diagnostics(self, **kwargs):
        _FakeDiagnosticsEngine.last_kwargs = kwargs
        return {
            "summary": {"analyzed_files": 1},
            "report_paths": {"csv": "x.csv"},
            "diagnostic_results": ["row-1"],
        }


class _FakeErrorDiagnosticsEngine:
    def run_diagnostics(self, **kwargs):
        _ = kwargs
        raise MP3DiagnosticsError("boom")


class _FakeCancelledDiagnosticsEngine:
    def run_diagnostics(self, **kwargs):
        _ = kwargs
        raise MP3DiagnosticsCancelled("cancelled")


class MP3DiagnosticsWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.completed_payload = None
        self.error_payload = None
        self.cancelled_payload = None
        self.progress_events = []

    def _build_worker(self) -> worker.MP3DiagnosticsWorker:
        return worker.MP3DiagnosticsWorker(
            on_progress=lambda c, t, m: self.progress_events.append((c, t, m)),
            on_completed=lambda payload: setattr(self, "completed_payload", payload),
            on_error=lambda message: setattr(self, "error_payload", message),
            on_cancelled=lambda message: setattr(self, "cancelled_payload", message),
        )

    def test_start_without_verify_winlive_keeps_compatibility(self) -> None:
        w = self._build_worker()

        with mock.patch.object(worker, "MP3DiagnosticsEngine", _FakeDiagnosticsEngine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                input_folder="in",
                include_subfolders=False,
                output_folder="out",
                repair_mode=False,
                placement_mode="copy",
            )

        self.assertIsNone(self.error_payload)
        self.assertIsNotNone(self.completed_payload)
        kwargs = _FakeDiagnosticsEngine.last_kwargs
        self.assertFalse(kwargs["verify_winlive"])
        self.assertIn("diagnostic_results", self.completed_payload)

    def test_verify_winlive_true_is_forwarded_and_callbacks_propagated(self) -> None:
        w = self._build_worker()

        with mock.patch.object(worker, "MP3DiagnosticsEngine", _FakeDiagnosticsEngine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                input_folder="in",
                include_subfolders=True,
                output_folder="out",
                repair_mode=True,
                placement_mode="move",
                selected_input_files=[Path("a.mp3")],
                verify_winlive=True,
            )

        kwargs = _FakeDiagnosticsEngine.last_kwargs
        self.assertTrue(kwargs["verify_winlive"])
        self.assertIs(kwargs["progress_callback"].__self__, w)
        self.assertEqual(kwargs["progress_callback"].__func__.__name__, "_emit_progress")
        self.assertIs(kwargs["cancel_event"], w._cancel_event)
        self.assertEqual(self.completed_payload["diagnostic_results"], ["row-1"])
        self.assertTrue(any("Inizializzazione diagnostica MP3" in msg for _, _, msg in self.progress_events))

    def test_error_is_reported_and_worker_state_resets(self) -> None:
        w = self._build_worker()

        with mock.patch.object(worker, "MP3DiagnosticsEngine", _FakeErrorDiagnosticsEngine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                input_folder="in",
                include_subfolders=False,
                output_folder="out",
                repair_mode=False,
                placement_mode="copy",
                verify_winlive=True,
            )

        self.assertIsNone(self.completed_payload)
        self.assertEqual(self.error_payload, "boom")
        self.assertFalse(w.is_running)
        self.assertFalse(w._cancel_event.is_set())

    def test_cancelled_is_reported_and_worker_state_resets(self) -> None:
        w = self._build_worker()

        with mock.patch.object(worker, "MP3DiagnosticsEngine", _FakeCancelledDiagnosticsEngine), mock.patch.object(
            worker.threading, "Thread", _ImmediateThread
        ):
            w.start(
                input_folder="in",
                include_subfolders=False,
                output_folder="out",
                repair_mode=False,
                placement_mode="copy",
                verify_winlive=True,
            )

        self.assertIsNone(self.completed_payload)
        self.assertEqual(self.cancelled_payload, "cancelled")
        self.assertFalse(w.is_running)
        self.assertFalse(w._cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
