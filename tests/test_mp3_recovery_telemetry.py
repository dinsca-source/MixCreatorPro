# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mp3_recovery_telemetry import HashTelemetrySink, RecoveryTelemetrySession
from winlive_validation import MpegFrame, _scan_mpeg_frames


class _CollectingSink:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.events: list[tuple[str, dict[str, object], bool]] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)

    def telemetry_event(self, event_type: str, payload: dict[str, object], *, critical: bool = False) -> None:
        self.events.append((event_type, dict(payload), critical))


class Mp3RecoveryTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_heartbeat_not_more_than_once_per_second(self) -> None:
        sink = _CollectingSink()
        clock = {"value": 0.0}

        def _next_time() -> float:
            current = clock["value"]
            clock["value"] += 0.4
            return current

        with mock.patch("winlive_validation.time.monotonic", side_effect=_next_time):
            _scan_mpeg_frames(b"\x00" * 12, 0, 12, debug_callback=sink, source_label="test")

        heartbeat_elapsed = [float(payload.get("monotonic_elapsed") or 0.0) for event, payload, _ in sink.events if event == "MPEG_SCAN_HEARTBEAT"]
        self.assertGreaterEqual(len(heartbeat_elapsed), 2)
        deltas = [heartbeat_elapsed[index] - heartbeat_elapsed[index - 1] for index in range(1, len(heartbeat_elapsed))]
        self.assertTrue(all(delta >= 1.0 for delta in deltas), deltas)

    def test_consecutive_heartbeats_can_report_same_offset(self) -> None:
        sink = _CollectingSink()
        clock = {"value": 0.0}

        def _next_time() -> float:
            current = clock["value"]
            clock["value"] += 0.6
            return current

        def _fake_parse(_data: bytes, offset: int):
            if offset == 0:
                return MpegFrame(offset=0, length=2, bitrate_kbps=128, sample_rate_hz=44100, version="MPEG1", layer="Layer III", padding=0, has_crc=False), None
            if 2 <= offset < 30:
                return MpegFrame(offset=offset, length=1, bitrate_kbps=128, sample_rate_hz=44100, version="MPEG1", layer="Layer III", padding=0, has_crc=False), None
            return None, None

        with mock.patch("winlive_validation.time.monotonic", side_effect=_next_time):
            with mock.patch("winlive_validation._parse_frame_at", side_effect=_fake_parse):
                _scan_mpeg_frames(b"X" * 40, 0, 40, debug_callback=sink, source_label="test")

        heartbeat_offsets = [int(payload.get("offset") or 0) for event, payload, _ in sink.events if event == "MPEG_SCAN_HEARTBEAT"]
        self.assertGreaterEqual(len(heartbeat_offsets), 2)
        self.assertTrue(any(heartbeat_offsets[index] == heartbeat_offsets[index - 1] for index in range(1, len(heartbeat_offsets))))

    def test_jsonl_created_with_valid_json_lines(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        try:
            session.emit_event("FILE_START", problematic_file="A.mp3", full_path="C:/A.mp3", file_size=123, phase="File", message="start")
            session.emit_event("FILE_END", problematic_file="A.mp3", full_path="C:/A.mp3", file_size=123, phase="File", message="end")
        finally:
            session.close()

        self.assertTrue(session.jsonl_path.is_file())
        lines = session.jsonl_path.read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 3)
        for line in lines:
            payload = json.loads(line)
            self.assertIn("event_type", payload)
            self.assertIn("session_id", payload)

    def test_custom_session_timestamp_and_diagnostics_folder(self) -> None:
        session = RecoveryTelemetrySession(
            self.root / "Recupero MP3",
            session_timestamp="2026-01-02_03-04-05",
            diagnostics_dir_name="Diagnostica Scanner MPEG",
        )
        try:
            self.assertEqual(session.timestamp, "2026-01-02_03-04-05")
            self.assertEqual(session.diagnostics_dir.name, "Diagnostica Scanner MPEG")
            self.assertTrue(session.diagnostics_dir.is_dir())
            self.assertTrue(session.current_state_path.parent.samefile(session.diagnostics_dir))
        finally:
            session.close()

    def test_critical_events_are_flushed_and_fsynced(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        try:
            with mock.patch.object(session._handle, "flush", wraps=session._handle.flush) as flush_mock:
                with mock.patch("mp3_recovery_telemetry.os.fsync") as fsync_mock:
                    session.emit_event("NON_PROGRESS", phase="Scansione frame MPEG", message="OFFSET_NON_AVANZA", critical=True)
            self.assertTrue(flush_mock.called)
            self.assertTrue(fsync_mock.called)
        finally:
            session.close()

    def test_current_state_is_updated_atomically(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        payload = session.emit_event("MPEG_SCAN_HEARTBEAT", problematic_file="B.mp3", full_path="C:/B.mp3", file_size=456, phase="Scansione frame MPEG", offset=10, previous_offset=5, next_offset=20, frame_length=4, outer_iteration=1, inner_iteration=2, frames_found=3, frames_valid=3, frames_rejected=0, bytes_processed=10, percent=50.0, speed_mb_s=1.2, message="hb")
        try:
            with mock.patch("mp3_recovery_telemetry.os.replace") as replace_mock:
                session.update_current_state(payload)
            self.assertTrue(replace_mock.called)
        finally:
            session.close()

    def test_current_state_contains_final_outcome_fields(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        payload = session.emit_event(
            "BATCH_FINAL_STATE",
            problematic_file="Song.mp3",
            full_path="C:/Song.mp3",
            phase="File completato",
            last_phase="Ricerca originale",
            batch_status="Completato",
            files_examined=1,
            files_completed=1,
            files_total=1,
            final_result="Originale non trovato",
            reason="Nessun originale con lo stesso nome",
            session_folder="C:/Session",
            result_folder="C:/Session/Esito Recupero File/Originale non trovato",
            recovered_file="",
            result_json="C:/Session/Esito Recupero File/Originale non trovato/Song.mp3.esito.json",
            final_timestamp="2026-01-02T03:04:05",
            message="done",
        )
        try:
            session.update_current_state(payload)
            state = json.loads(session.current_state_path.read_text(encoding="utf-8"))
            self.assertEqual(state.get("final_result"), "Originale non trovato")
            self.assertEqual(state.get("reason"), "Nessun originale con lo stesso nome")
            self.assertEqual(state.get("session_folder"), "C:/Session")
            self.assertIn("Song.mp3.esito.json", str(state.get("result_json")))
            self.assertEqual(state.get("final_timestamp"), "2026-01-02T03:04:05")
        finally:
            session.close()

    def test_cancel_leaves_jsonl_current_state_and_cancel_detected(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        problematic = self.root / "Recupero MP3" / "A.mp3"
        problematic.parent.mkdir(parents=True, exist_ok=True)
        problematic.write_bytes(b"dummy")
        sink = HashTelemetrySink(session, problematic_path=problematic, full_path=problematic, file_size=5, role="problematico")
        try:
            sink.telemetry_event(
                "MPEG_SCAN_HEARTBEAT",
                {
                    "phase": "Scansione frame MPEG",
                    "offset": 11,
                    "previous_offset": 11,
                    "next_offset": 11,
                    "frame_length": 0,
                    "outer_iteration": 10,
                    "inner_iteration": 20,
                    "frames_found": 1,
                    "frames_valid": 1,
                    "frames_rejected": 0,
                    "bytes_processed": 11,
                    "percent": 22.0,
                    "speed_mb_s": 0.0,
                    "message": "Heartbeat scanner MPEG",
                    "monotonic_elapsed": 2.0,
                },
            )
            sink.telemetry_event(
                "CANCEL_DETECTED",
                {
                    "phase": "Scansione frame MPEG",
                    "offset": 11,
                    "previous_offset": 11,
                    "next_offset": 11,
                    "frame_length": 0,
                    "outer_iteration": 10,
                    "inner_iteration": 20,
                    "frames_found": 1,
                    "frames_valid": 1,
                    "frames_rejected": 0,
                    "bytes_processed": 11,
                    "percent": 22.0,
                    "speed_mb_s": 0.0,
                    "message": "cancel",
                    "cancel_requested": True,
                    "monotonic_elapsed": 3.0,
                },
                critical=True,
            )
        finally:
            session.close(final_message="cancel", cancelled=True)

        self.assertTrue(session.jsonl_path.is_file())
        self.assertTrue(session.current_state_path.is_file())
        payloads = [json.loads(line) for line in session.jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(item.get("event_type") == "CANCEL_DETECTED" for item in payloads))

    def test_logging_is_not_per_frame_or_iteration_normal(self) -> None:
        sink = _CollectingSink()
        clock = {"value": 0.0}

        def _next_time() -> float:
            current = clock["value"]
            clock["value"] += 0.1
            return current

        with mock.patch("winlive_validation.time.monotonic", side_effect=_next_time):
            _scan_mpeg_frames(b"\x00" * 8, 0, 8, debug_callback=sink, source_label="quiet")

        self.assertEqual(sink.messages, [])
        heartbeat_count = sum(1 for event, _, _ in sink.events if event == "MPEG_SCAN_HEARTBEAT")
        self.assertLessEqual(heartbeat_count, 1)

    def test_summary_csv_contains_phase_duration_columns(self) -> None:
        session = RecoveryTelemetrySession(self.root / "Recupero MP3")
        problematic = self.root / "Recupero MP3" / "Song.mp3"
        problematic.parent.mkdir(parents=True, exist_ok=True)
        problematic.write_bytes(b"dummy")
        try:
            session.ensure_file(problematic, 5)
            session.set_phase_duration(problematic, "search_original", 1.0)
            session.set_phase_duration(problematic, "hash_problematic", 2.0)
            session.set_phase_duration(problematic, "hash_original", 3.0)
            session.set_phase_duration(problematic, "recovery", 4.0)
            session.set_phase_duration(problematic, "verification_final", 5.0)
            session.set_phase_duration(problematic, "total_file", 6.0)
            session.update_hash_summary(problematic, "problematico", {"plan_elapsed_seconds": 0.1, "scan_elapsed_seconds": 0.2, "sha_elapsed_seconds": 0.3, "hash_total_elapsed_seconds": 0.4, "frames_found": 7, "audio_bytes_hashed": 8, "offset": 9, "outer_iteration": 10, "inner_iteration": 11, "speed_mb_s": 1.5})
            session.emit_file_end(problematic, "OK", "")
        finally:
            session.close()

        content = session.summary_csv_path.read_text(encoding="utf-8")
        self.assertIn("tempo_ricerca_originale", content)
        self.assertIn("tempo_hash_problematico", content)
        self.assertIn("tempo_hash_originale", content)
        self.assertIn("tempo_recupero", content)
        self.assertIn("tempo_verifica_finale", content)
        self.assertIn("tempo_totale_file", content)
        self.assertIn("parse_calls_totali_problematico", content)
        self.assertIn("offset_unici_problematico", content)
        self.assertIn("cache_hit_problematico", content)
        self.assertIn("cache_miss_problematico", content)
        self.assertIn("rapporto_inner_frame_validi_problematico", content)
        self.assertIn("velocita_media_scanner_mb_s_problematico", content)
        self.assertIn("durata_scanner_secondi_problematico", content)


if __name__ == "__main__":
    unittest.main()
