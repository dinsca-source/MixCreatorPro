# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from mp3_diagnostics import (
    AUDIO_WINDOW_MS,
    AnalysisResult,
    AudioBounds,
    AudioWindowStats,
    DiagnosticIssue,
    DiagnosticCategory,
    IssuePosition,
    MP3DiagnosticsCancelled,
    MP3DiagnosticsEngine,
    OUTPUT_FOLDER_OK,
    OUTPUT_FOLDER_INTEGRITY_ROOT,
    OUTPUT_FOLDER_REPAIRED,
    OUTPUT_FOLDER_REPORT,
    OUTPUT_FOLDER_UNRECOVERABLE,
    OUTPUT_FOLDER_PROCESSED_ORIGINALS,
    OUTPUT_SESSION_PREFIX,
    OUTPUT_FOLDER_TEMP,
    PLACEMENT_MODE_COPY,
    PLACEMENT_MODE_MOVE,
    PRECISION_500MS,
    PRECISION_UNKNOWN,
    SILENCE_PEAK_THRESHOLD_DB,
    SILENCE_RMS_THRESHOLD_DB,
    STATUS_PERFECT,
    STATUS_REPAIRED,
    STATUS_UNRECOVERABLE,
)


class _FakeFFmpegManager:
    def __init__(self) -> None:
        self.ffmpeg_path = Path("ffmpeg")

    def validate(self) -> None:
        return

    def get_duration(self, _path: Path) -> float:
        return 30.0

    @staticmethod
    def _creation_flags() -> int:
        return 0


class _ScenarioEngine(MP3DiagnosticsEngine):
    """Deterministic integration-style test engine (not real MP3 E2E)."""

    def __init__(
        self,
        *,
        before_issues: list[DiagnosticIssue],
        after_issues: list[DiagnosticIssue],
        repair_ok: bool,
        significant_blocking_before: bool,
        significant_blocking_after: bool,
        bounds: AudioBounds,
        issue_stats: dict[str, tuple[float, float]],
    ) -> None:
        super().__init__(ffmpeg=_FakeFFmpegManager())
        self.before_issues = before_issues
        self.after_issues = after_issues
        self.repair_ok = repair_ok
        self.significant_blocking_before = significant_blocking_before
        self.significant_blocking_after = significant_blocking_after
        self.bounds = bounds
        self.issue_stats = issue_stats

    def detect_significant_audio_bounds(self, file_path: Path) -> AudioBounds:
        _ = file_path
        return self.bounds

    def _analyze_significant_segment(self, *, file_path: Path, bounds: AudioBounds, cancel_event):
        _ = (file_path, bounds, cancel_event)
        is_repaired_file = "_reenc" in file_path.stem
        return {
            "header_missing": 0,
            "corrupted_frames": 0,
            "crc_errors": 0,
            "sync_errors": 1 if (self.significant_blocking_after if is_repaired_file else self.significant_blocking_before) else 0,
            "undecodable_frames": 0,
            "invalid_data": 0,
            "xing_issues": 0,
            "vbr_issues": 0,
            "id3_issues": 0,
        }

    def _analyze_mp3(self, *, file_path: Path, cancel_event, segment_ss, segment_t) -> AnalysisResult:
        _ = cancel_event
        is_repaired_file = "_reenc" in file_path.stem
        issues = self.after_issues if is_repaired_file else self.before_issues
        if segment_ss is not None and segment_t is not None:
            metrics = {
                "header_missing": 0,
                "corrupted_frames": 0,
                "crc_errors": 0,
                "sync_errors": 1 if (self.significant_blocking_after if is_repaired_file else self.significant_blocking_before) else 0,
                "undecodable_frames": 0,
                "invalid_data": 0,
                "xing_issues": 0,
                "vbr_issues": 0,
                "id3_issues": 0,
            }
        else:
            metrics = self._metrics_from_issues(issues)

        return AnalysisResult(
            command=["ffmpeg", "-i", str(file_path)],
            command_text=f"ffmpeg -i {file_path}",
            return_code=0,
            decode_log="deterministic",
            issues=issues,
            metrics=metrics,
            integrity_index=self._calculate_integrity_index(metrics),
            total_errors=sum(metrics.values()),
        )

    def _attempt_repair(self, *, source: Path, temp_dir: Path, cancel_event):
        _ = cancel_event
        if not self.repair_ok:
            return {
                "ok": False,
                "output_path": None,
                "command": "ffmpeg -repair",
                "return_code": 1,
                "mode": "safe-reencode",
                "error": "repair failed",
            }

        temp_dir.mkdir(parents=True, exist_ok=True)
        out = temp_dir / f"{source.stem}_reenc.mp3"
        out.write_bytes(source.read_bytes())
        return {
            "ok": True,
            "output_path": str(out),
            "command": "ffmpeg -repair",
            "return_code": 0,
            "mode": "safe-reencode",
            "error": "",
        }

    def _measure_issue_audio_stats(self, *, file_path: Path, issue: DiagnosticIssue, duration_seconds: float):
        _ = (file_path, duration_seconds)
        rms, peak = self.issue_stats.get(issue.detail, (-60.0, -60.0))
        return {
            "rms_dbfs": rms,
            "peak_dbfs": peak,
            "segment_duration": 0.5,
        }

    @staticmethod
    def _metrics_from_issues(issues: list[DiagnosticIssue]) -> dict[str, int]:
        metrics = {
            "header_missing": 0,
            "corrupted_frames": 0,
            "crc_errors": 0,
            "sync_errors": 0,
            "undecodable_frames": 0,
            "invalid_data": 0,
            "xing_issues": 0,
            "vbr_issues": 0,
            "id3_issues": 0,
        }
        for issue in issues:
            if issue.problem_key in metrics:
                metrics[issue.problem_key] += 1
        return metrics


class _FixedSessionScenarioEngine(_ScenarioEngine):
    @staticmethod
    def _session_timestamp_token() -> str:
        return "2026-01-02_03-04-05"


class MP3DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.input_dir / "song.mp3").write_bytes(b"fake-mp3-data")

        self.default_bounds = AudioBounds(
            file_duration_ms=30_000,
            significant_start_ms=1_000,
            significant_end_ms=29_000,
            leading_silence_ms=1_000,
            trailing_silence_ms=1_000,
            detection_confidence=0.95,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _summary_rows(self, result: dict[str, object]) -> list[dict[str, str]]:
        path = Path(result["report_paths"]["csv_summary"])
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _problem_rows(self, result: dict[str, object]) -> list[dict[str, str]]:
        path = Path(result["report_paths"]["csv_problems"])
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _session_root(self, result: dict[str, object]) -> Path:
        return Path(result["summary"]["output_folder"])

    def _issue(self, key: str, start: str, detail: str) -> DiagnosticIssue:
        return DiagnosticIssue(
            problem_key=key,
            problem_type="Frame MP3 non decodificabile",
            start=start,
            end=start,
            precision=PRECISION_UNKNOWN,
            detail=detail,
        )

    def _localized_issue(self, key: str, start: str, end: str, detail: str) -> DiagnosticIssue:
        return DiagnosticIssue(
            problem_key=key,
            problem_type="Frame MP3 non decodificabile",
            start=start,
            end=end,
            precision=PRECISION_500MS,
            detail=detail,
        )

    @staticmethod
    def _ffmpeg_path() -> Path:
        return MP3DiagnosticsEngine().ffmpeg.ffmpeg_path

    @staticmethod
    def _run_ffmpeg(command: list[str]) -> None:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _make_silent_tail_regression_mp3(self, target_path: Path) -> None:
        ffmpeg = self._ffmpeg_path()
        base_path = target_path.with_name(f"{target_path.stem}_base.mp3")
        self._run_ffmpeg(
            [
                str(ffmpeg),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=14",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100:duration=2",
                "-filter_complex",
                "[0:a]afade=t=out:st=12:d=2[a0];[a0][1:a]concat=n=2:v=0:a=1[a]",
                "-map",
                "[a]",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(base_path),
            ]
        )
        data = bytearray(base_path.read_bytes())
        tail_start = max(0, len(data) - 128 - 1800)
        tail_end = max(tail_start + 64, len(data) - 128)
        for index in range(tail_start, tail_end, 17):
            data[index] = 255
        target_path.write_bytes(data)
        base_path.unlink(missing_ok=True)

    def _run_single_file_diagnostics(self, source_file: Path, output_root: Path) -> dict[str, object]:
        temp_input = output_root / "input"
        temp_input.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, temp_input / source_file.name)
        engine = MP3DiagnosticsEngine()
        return engine.run_diagnostics(
            input_folder=str(temp_input),
            include_subfolders=False,
            output_folder=str(output_root / "out"),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )

    def test_error_last_500ms_below_threshold_is_ok(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:29.600", "tail-silent")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"tail-silent": (-70.0, -60.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]
        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)

    def test_error_first_500ms_below_threshold_is_ok(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:00.200", "lead-silent")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"lead-silent": (-68.0, -58.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]
        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)

    def test_error_at_center_audible_is_unrecoverable_if_not_fixed(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:15.000", "center-audible")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=True,
            significant_blocking_after=True,
            bounds=self.default_bounds,
            issue_stats={"center-audible": (-18.0, -9.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)

    def test_error_at_center_fixed_with_clean_verification_is_repaired(self) -> None:
        before = self._issue("undecodable_frames", "00:00:15.000", "center-before")
        engine = _ScenarioEngine(
            before_issues=[before],
            after_issues=[],
            repair_ok=True,
            significant_blocking_before=True,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"center-before": (-16.0, -8.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]
        self.assertEqual(row["Stato finale file"], STATUS_REPAIRED)

    def test_terminal_low_impact_header_missing_is_ignored_for_classification(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.500", "tail-hm")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-hm": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)
        self.assertIn("coda finale", row["Motivo classificazione finale"].lower())
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["Motivo / dettaglio essenziale"], "tail-hm")
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "SI")
        self.assertIn("coda finale", problems[0]["Motivo esclusione"].lower())

    def test_terminal_low_impact_invalid_data_is_ignored_for_classification(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("invalid_data", "00:02:59.000", "00:02:59.500", "tail-id")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-id": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["Motivo / dettaglio essenziale"], "tail-id")
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "SI")

    def test_two_terminal_low_impact_warnings_are_both_ignored(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue_h = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.500", "tail-h")
        issue_i = self._localized_issue("invalid_data", "00:02:59.000", "00:02:59.500", "tail-i")
        engine = _ScenarioEngine(
            before_issues=[issue_h, issue_i],
            after_issues=[issue_h, issue_i],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-h": (-26.0, -14.0), "tail-i": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)
        details = " | ".join(p["Motivo / dettaglio essenziale"] for p in problems)
        self.assertIn("tail-h", details)
        self.assertIn("tail-i", details)
        self.assertTrue(all(p["Problema ignorato ai fini dello stato"] == "SI" for p in problems))

    def test_terminal_issue_high_level_is_not_tolerated(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.500", "tail-loud")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-loud": (-20.0, -6.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "NO")

    def test_issue_in_song_body_is_not_tolerated(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("header_missing", "00:01:30.000", "00:01:30.500", "mid-hm")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"mid-hm": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "NO")

    def test_terminal_issue_too_long_is_not_tolerated(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.900", "tail-long")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-long": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "NO")

    def test_terminal_low_impact_plus_central_issue_keeps_central_blocking(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        tail_issue = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.500", "tail-hm")
        mid_issue = self._localized_issue("sync_errors", "00:01:20.000", "00:01:20.500", "mid-sync")
        engine = _ScenarioEngine(
            before_issues=[tail_issue, mid_issue],
            after_issues=[tail_issue, mid_issue],
            repair_ok=False,
            significant_blocking_before=True,
            significant_blocking_after=True,
            bounds=bounds,
            issue_stats={"tail-hm": (-26.0, -14.0), "mid-sync": (-18.0, -7.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=180.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)
        tail_rows = [p for p in problems if p["Motivo / dettaglio essenziale"] == "tail-hm"]
        mid_rows = [p for p in problems if p["Motivo / dettaglio essenziale"] == "mid-sync"]
        self.assertEqual(len(tail_rows), 1)
        self.assertEqual(len(mid_rows), 1)
        self.assertEqual(tail_rows[0]["Problema ignorato ai fini dello stato"], "SI")
        self.assertEqual(mid_rows[0]["Problema ignorato ai fini dello stato"], "NO")

    def test_terminal_tolerance_not_applied_when_decoding_is_globally_unusable(self) -> None:
        bounds = AudioBounds(
            file_duration_ms=180_000,
            significant_start_ms=0,
            significant_end_ms=180_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )
        issue = self._localized_issue("header_missing", "00:02:59.000", "00:02:59.500", "tail-hm")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=bounds,
            issue_stats={"tail-hm": (-26.0, -14.0)},
        )
        with mock.patch.object(engine, "_safe_duration_seconds", return_value=0.0):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
            )

        row = self._summary_rows(result)[0]
        problems = self._problem_rows(result)
        self.assertEqual(row["Stato finale file"], STATUS_UNRECOVERABLE)
        self.assertEqual(problems[0]["Problema ignorato ai fini dello stato"], "NO")

    def test_boundary_overlap_not_ignored_automatically(self) -> None:
        issue = DiagnosticIssue(
            problem_key="undecodable_frames",
            problem_type="Frame MP3 non decodificabile",
            start="00:00:00.900",
            end="",
            precision=PRECISION_UNKNOWN,
            detail="boundary",
        )
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=True,
            significant_blocking_after=True,
            bounds=self.default_bounds,
            issue_stats={"boundary": (-58.0, -49.0)},
        )
        evaluated = engine._evaluate_issues(
            file_path=self.input_dir / "song.mp3",
            issues=[issue],
            bounds=self.default_bounds,
            significant_segment_metrics={
                "header_missing": 0,
                "corrupted_frames": 0,
                "crc_errors": 0,
                "sync_errors": 1,
                "undecodable_frames": 0,
                "invalid_data": 0,
                "xing_issues": 0,
                "vbr_issues": 0,
                "id3_issues": 0,
            },
            duration_seconds=30.0,
        )
        self.assertEqual(evaluated[0].position, IssuePosition.BOUNDARY_OVERLAP)
        self.assertFalse(evaluated[0].ignored_for_classification)

    def test_no_partial_status_or_category(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:15.000", "center")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=True,
            significant_blocking_after=True,
            bounds=self.default_bounds,
            issue_stats={"center": (-20.0, -10.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]
        self.assertNotIn("Parzial", row["Categoria finale"])
        self.assertNotEqual(row["Stato finale file"], "Riparato parzialmente")

    def test_no_new_parziali_folder_created(self) -> None:
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )
        engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        self.assertFalse((self.output_dir / "Parziali").exists())

    def test_old_parziali_folder_excluded_from_scan(self) -> None:
        nested = self.input_dir / "Parziali"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "old.mp3").write_bytes(b"legacy")

        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=True,
            output_folder=str(self.input_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        self.assertEqual(result["summary"]["analyzed_files"], 1)

    def test_run_diagnostics_without_selected_input_files_keeps_folder_scan(self) -> None:
        (self.input_dir / "song2.mp3").write_bytes(b"fake-mp3-data-2")
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )

        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )

        self.assertEqual(result["summary"]["analyzed_files"], 2)

    def test_run_diagnostics_with_selected_input_files_only_processes_selection(self) -> None:
        selected = self.input_dir / "selected.mp3"
        ignored = self.input_dir / "ignored.mp3"
        selected.write_bytes(b"selected")
        ignored.write_bytes(b"ignored")
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )

        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
            selected_input_files=[selected],
        )

        self.assertEqual(result["summary"]["analyzed_files"], 1)
        row = self._summary_rows(result)[0]
        self.assertEqual(row["File"], "selected.mp3")

    def test_selected_input_files_deduplicate_case_variants(self) -> None:
        selected = self.input_dir / "DupeCase.mp3"
        selected.write_bytes(b"dupe")
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )

        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
            selected_input_files=[selected, Path(str(selected).replace("DupeCase", "dupecase"))],
        )

        self.assertEqual(result["summary"]["analyzed_files"], 1)

    def test_selected_input_files_skips_non_mp3_and_missing(self) -> None:
        txt_file = self.input_dir / "not_audio.txt"
        txt_file.write_text("x", encoding="utf-8")
        missing_file = self.input_dir / "missing.mp3"

        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )

        with self.assertRaisesRegex(Exception, "Nessun file MP3 valido disponibile"):
            engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
                selected_input_files=[txt_file, missing_file],
            )

    def test_selected_input_files_uses_standard_placement_folders(self) -> None:
        selected = self.input_dir / "placed.mp3"
        selected.write_bytes(b"placed")
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )

        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=True,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
            selected_input_files=[selected],
        )
        row = self._summary_rows(result)[0]
        session_root = self._session_root(result)
        integrity_root = session_root / OUTPUT_FOLDER_INTEGRITY_ROOT

        self.assertTrue(session_root.name.startswith(OUTPUT_SESSION_PREFIX))
        self.assertTrue((integrity_root / OUTPUT_FOLDER_OK).is_dir())
        self.assertTrue((integrity_root / OUTPUT_FOLDER_REPAIRED).is_dir())
        self.assertTrue((integrity_root / OUTPUT_FOLDER_UNRECOVERABLE).is_dir())
        self.assertTrue((session_root / OUTPUT_FOLDER_PROCESSED_ORIGINALS).is_dir())
        self.assertTrue((session_root / OUTPUT_FOLDER_REPORT).is_dir())
        self.assertEqual(row["Categoria finale"], OUTPUT_FOLDER_OK)

    def test_session_timestamp_is_reused_across_output_paths(self) -> None:
        engine = _FixedSessionScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        session_root = self._session_root(result)
        row = self._summary_rows(result)[0]

        self.assertEqual(session_root.name, "Diagnostica_MP3_2026-01-02_03-04-05")
        self.assertTrue(Path(result["report_paths"]["csv_summary"]).is_relative_to(session_root))
        self.assertEqual(Path(result["report_paths"]["csv_summary"]).name, "report_diagnostica_2026-01-02_03-04-05.csv")
        self.assertEqual(Path(result["report_paths"]["xlsx"]).name, "report_diagnostica_2026-01-02_03-04-05.xlsx")
        self.assertEqual(Path(result["report_paths"]["html"]).name, "report_diagnostica.html")
        self.assertTrue(Path(row["Percorso finale"]).is_relative_to(session_root))

    def test_final_counts_three_categories_are_consistent(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:15.000", "center")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[],
            repair_ok=True,
            significant_blocking_before=True,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"center": (-20.0, -10.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        summary = result["summary"]
        self.assertEqual(
            summary["category_ok_files"] + summary["category_repaired_files"] + summary["category_unrecoverable_files"],
            summary["analyzed_files"],
        )

    def test_report_distinguishes_relevant_and_ignored(self) -> None:
        issue_ignored = self._issue("undecodable_frames", "00:00:29.700", "tail-silent")
        issue_relevant = self._issue("undecodable_frames", "00:00:15.200", "mid")
        engine = _ScenarioEngine(
            before_issues=[issue_ignored, issue_relevant],
            after_issues=[issue_ignored, issue_relevant],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={
                "tail-silent": (-70.0, -60.0),
                "mid": (-17.0, -7.0),
            },
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        rows = self._problem_rows(result)
        ignored = [r for r in rows if r["Problema ignorato ai fini dello stato"] == "SI"]
        relevant = [r for r in rows if r["Problema ignorato ai fini dello stato"] == "NO"]
        self.assertGreaterEqual(len(ignored), 1)
        self.assertGreaterEqual(len(relevant), 1)

    def test_real_regression_file_trailing_silence_is_ignored(self) -> None:
        source = Path(r"C:\BASI Organizzate\Generico\Cinque Giorni (Michele Zarrillo).mp3")
        if not source.exists():
            self.skipTest("Real regression file is not available on this machine")

        engine = MP3DiagnosticsEngine()
        temp_root = Path(self.temp.name) / "real_regression"
        temp_input = temp_root / "input"
        temp_output = temp_root / "output"
        temp_input.mkdir(parents=True, exist_ok=True)
        temp_output.mkdir(parents=True, exist_ok=True)
        copied = temp_input / source.name
        shutil.copy2(source, copied)

        result = engine.run_diagnostics(
            input_folder=str(temp_input),
            include_subfolders=False,
            output_folder=str(temp_output),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )

        summary_rows = self._summary_rows(result)
        problem_rows = self._problem_rows(result)
        row = next(r for r in summary_rows if r["File"] == source.name)
        problem = next((r for r in problem_rows if r["File"] == source.name), None)

        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)
        self.assertEqual(row["Categoria finale"], OUTPUT_FOLDER_OK)
        self.assertEqual(row["Originale conservato"], "SI")
        self.assertEqual(row["Operazione eseguita"], "Copiato originale")
        self.assertIsNotNone(problem)
        if problem is not None:
            self.assertEqual(problem["Posizione rispetto all'audio significativo"], "TRAILING_SILENCE")
            self.assertEqual(problem["Problema ignorato ai fini dello stato"], "SI")
            self.assertLessEqual(float(problem["RMS segmento (dBFS)"]), SILENCE_RMS_THRESHOLD_DB)
            self.assertLessEqual(float(problem["Picco segmento (dBFS)"]), SILENCE_PEAK_THRESHOLD_DB)

    def test_synthetic_fadeout_tail_silence_corruption_is_ignored(self) -> None:
        source_file = Path(r"c:\MixCreatorPro\e2e_authentic_silence\input\A_tail_silence_corrupt_last500ms.mp3")
        if not source_file.exists():
            self.skipTest("Synthetic tail-silence fixture is not available on this machine")

        temp_root = Path(self.temp.name) / "synthetic_tail"
        temp_input = temp_root / "input"
        temp_output = temp_root / "output"
        temp_input.mkdir(parents=True, exist_ok=True)
        temp_output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, temp_input / source_file.name)

        engine = MP3DiagnosticsEngine()
        result = engine.run_diagnostics(
            input_folder=str(temp_input),
            include_subfolders=False,
            output_folder=str(temp_output),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )

        summary_rows = self._summary_rows(result)
        problem_rows = self._problem_rows(result)
        row = summary_rows[0]
        problem = problem_rows[0]

        self.assertEqual(row["Stato finale file"], STATUS_PERFECT)
        self.assertEqual(row["Categoria finale"], OUTPUT_FOLDER_OK)
        self.assertEqual(row["Originale conservato"], "SI")
        self.assertEqual(problem["Posizione rispetto all'audio significativo"], "TRAILING_SILENCE")
        self.assertEqual(problem["Problema ignorato ai fini dello stato"], "SI")
        self.assertLessEqual(float(problem["RMS segmento (dBFS)"]), SILENCE_RMS_THRESHOLD_DB)
        self.assertLessEqual(float(problem["Picco segmento (dBFS)"]), SILENCE_PEAK_THRESHOLD_DB)

    def test_move_mode_repaired_keeps_original_in_safety_folder(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:15.000", "center")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[],
            repair_ok=True,
            significant_blocking_before=True,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"center": (-20.0, -10.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_MOVE,
        )
        row = self._summary_rows(result)[0]
        self.assertEqual(row["Categoria finale"], OUTPUT_FOLDER_REPAIRED)
        self.assertEqual(row["Originale conservato"], "SI")
        self.assertIn(OUTPUT_FOLDER_PROCESSED_ORIGINALS, row["Percorso originale conservato"])

    def test_copy_mode_repaired_keeps_original_in_safety_folder(self) -> None:
        issue = self._issue("undecodable_frames", "00:00:15.000", "center")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[],
            repair_ok=True,
            significant_blocking_before=True,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"center": (-20.0, -10.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        row = self._summary_rows(result)[0]

        self.assertEqual(row["Categoria finale"], OUTPUT_FOLDER_REPAIRED)
        self.assertEqual(row["Originale conservato"], "SI")
        self.assertIn(OUTPUT_FOLDER_PROCESSED_ORIGINALS, row["Percorso originale conservato"])
        self.assertTrue(Path(row["Percorso originale conservato"]).is_file())

    def test_copy_mode_unmodified_does_not_create_safety_backup(self) -> None:
        engine = _ScenarioEngine(
            before_issues=[],
            after_issues=[],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        session_root = self._session_root(result)
        safety_dir = session_root / OUTPUT_FOLDER_PROCESSED_ORIGINALS

        self.assertTrue(safety_dir.is_dir())
        self.assertEqual(list(safety_dir.rglob("*.mp3")), [])

    def test_safety_backup_handles_duplicate_names_without_overwrite(self) -> None:
        first = self.input_dir / "disc_a" / "song.mp3"
        second = self.input_dir / "disc_b" / "song.mp3"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"broken-a")
        second.write_bytes(b"broken-b")

        issue = self._issue("undecodable_frames", "00:00:15.000", "center")
        engine = _ScenarioEngine(
            before_issues=[issue],
            after_issues=[],
            repair_ok=True,
            significant_blocking_before=True,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"center": (-20.0, -10.0)},
        )
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
            selected_input_files=[first, second],
        )
        session_root = self._session_root(result)
        safety_dir = session_root / OUTPUT_FOLDER_PROCESSED_ORIGINALS
        safety_files = sorted(safety_dir.glob("song*.mp3"), key=lambda p: p.name)

        self.assertEqual(len(safety_files), 2)
        self.assertNotEqual(safety_files[0].name, safety_files[1].name)

    def test_cancel_during_segmented_localization(self) -> None:
        class _CancelEngine(_ScenarioEngine):
            def _segment_has_problem(self, file_path: Path, start_seconds: float, length_seconds: float, problem_key: str, cancel_event):
                if cancel_event is not None:
                    cancel_event.set()
                return False

        issue = self._issue("undecodable_frames", "Tempo non determinabile", "x")
        engine = _CancelEngine(
            before_issues=[issue],
            after_issues=[issue],
            repair_ok=False,
            significant_blocking_before=False,
            significant_blocking_after=False,
            bounds=self.default_bounds,
            issue_stats={"x": (-70.0, -60.0)},
        )
        cancel_event = threading.Event()
        with self.assertRaises(MP3DiagnosticsCancelled):
            engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                placement_mode=PLACEMENT_MODE_COPY,
                cancel_event=cancel_event,
            )

    def test_bounds_click_before_music_does_not_define_start(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = []
        windows.append(AudioWindowStats(0, AUDIO_WINDOW_MS, -18.0, -8.0))  # isolated click
        for i in range(1, 8):
            windows.append(AudioWindowStats(i * AUDIO_WINDOW_MS, (i + 1) * AUDIO_WINDOW_MS, -70.0, -60.0))
        for i in range(8, 20):
            windows.append(AudioWindowStats(i * AUDIO_WINDOW_MS, (i + 1) * AUDIO_WINDOW_MS, -20.0, -8.0))
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=2_000)
        self.assertGreaterEqual(bounds.significant_start_ms, 500)

    def test_bounds_immediate_start_is_near_zero(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = [AudioWindowStats(i * 100, (i + 1) * 100, -20.0, -8.0) for i in range(20)]
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=2_000)
        self.assertLessEqual(bounds.significant_start_ms, 250)

    def test_bounds_long_leading_silence_detected(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = [AudioWindowStats(i * 100, (i + 1) * 100, -70.0, -60.0) for i in range(10)]
        windows.extend([AudioWindowStats(i * 100, (i + 1) * 100, -22.0, -10.0) for i in range(10, 30)])
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=3_000)
        self.assertGreaterEqual(bounds.significant_start_ms, 700)

    def test_bounds_no_trailing_silence_keeps_end_near_duration(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = [AudioWindowStats(i * 100, (i + 1) * 100, -24.0, -10.0) for i in range(30)]
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=3_000)
        self.assertGreaterEqual(bounds.significant_end_ms, 2_750)

    def test_bounds_fadeout_remains_significant(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = [AudioWindowStats(i * 100, (i + 1) * 100, -22.0, -10.0) for i in range(20)]
        windows.extend([
            AudioWindowStats(2_000, 2_100, -40.0, -30.0),
            AudioWindowStats(2_100, 2_200, -43.0, -34.0),
            AudioWindowStats(2_200, 2_300, -47.0, -39.0),
            AudioWindowStats(2_300, 2_400, -50.0, -42.0),
        ])
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=2_400)
        self.assertGreaterEqual(bounds.significant_end_ms, 2_200)

    def test_bounds_weak_reverb_tail_remains_significant(self) -> None:
        engine = MP3DiagnosticsEngine(ffmpeg=_FakeFFmpegManager())
        windows = [AudioWindowStats(i * 100, (i + 1) * 100, -20.0, -9.0) for i in range(18)]
        windows.extend([
            AudioWindowStats(1_800, 1_900, -52.0, -41.0),
            AudioWindowStats(1_900, 2_000, -53.0, -42.0),
            AudioWindowStats(2_000, 2_100, -54.0, -43.0),
        ])
        bounds = engine._detect_significant_bounds_from_windows(windows=windows, duration_ms=2_100)
        self.assertGreaterEqual(bounds.significant_end_ms, 1_950)


if __name__ == "__main__":
    unittest.main()
