# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mp3_recovery_batch import (
    MP3BatchOutcome,
    MP3BatchItemResult,
    MP3RecoveryBatchResult,
    CHECK_COMPATIBLE,
    CHECK_INCOMPATIBLE,
    CHECK_NOT_DETERMINABLE,
    CHECK_TECHNICAL_ERROR,
    OVERALL_COMPATIBLE,
    OVERALL_INCOMPATIBLE,
    _copy_with_collision,
    _build_problem_rows,
    _compute_compatibility_analysis,
    _cleanup_empty_session_dirs,
    recover_mp3_batch_from_folders,
)
from mp3_recovery import MP3RecoveryStatus, RecoveryMode
from winlive_validation import AudioHashStatus
from worker import MP3RecoveryWorker


def _frame(length: int = 417, payload_byte: int = 0x41) -> bytes:
    return b"\xFF\xFB\x90\x64" + bytes([payload_byte]) * (length - 4)


def _build_mp3(
    *,
    prefix: bytes = b"",
    audio_payloads: tuple[int, int, int] = (0x11, 0x22, 0x33),
    synct_text: bytes | None = None,
    chord_text: bytes | None = None,
    tail: bytes = b"TAIL",
) -> bytes:
    audio = b"".join(_frame(payload_byte=payload) for payload in audio_payloads)
    parts = [prefix, audio]
    if synct_text is not None:
        parts.append(b"<WL5SYNCT>" + synct_text + b"/<WL5SYNCT>")
    if chord_text is not None:
        parts.append(b"<WL5CHORD>" + chord_text + b"/<WL5CHORD>")
    parts.append(tail)
    return b"".join(parts)


class _CancelAfterOne:
    def __init__(self) -> None:
        self._cancelled = False

    def is_set(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class MP3RecoveryBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.problematic_dir = self.root / "problematic"
        self.originals_dir = self.root / "originals"
        self.destination_dir = self.root / "destination"
        self.problematic_dir.mkdir(parents=True, exist_ok=True)
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self.destination_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, folder: Path, name: str, data: bytes) -> Path:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _sha(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _run_batch(self, cancel_event: object | None = None, destination: Path | None = None):
        return recover_mp3_batch_from_folders(
            problematic_dir=self.problematic_dir,
            originals_dir=self.originals_dir,
            destination_dir=destination or self.destination_dir,
            cancel_event=cancel_event,
        )

    def _assert_negative_item_has_problematic_copy(self, item: MP3BatchItemResult, expected_outcome: MP3BatchOutcome) -> None:
        self.assertEqual(item.outcome, expected_outcome)
        self.assertTrue(item.esito_json_path)
        self.assertTrue(item.copied_problematic_path)
        self.assertTrue(item.problematic_copy_created)
        self.assertTrue(item.problematic_copy_byte_identical)

        copied_path = Path(item.copied_problematic_path)
        original_problematic = Path(item.problematic_path)
        self.assertTrue(copied_path.is_file())
        self.assertEqual(copied_path.read_bytes(), original_problematic.read_bytes())

        esito_path = Path(item.esito_json_path)
        self.assertTrue(esito_path.is_file())
        payload = json.loads(esito_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("final_result"), expected_outcome.value)
        self.assertEqual(payload.get("copied_problematic_path"), str(copied_path))
        self.assertEqual(payload.get("copied_problematic_file"), copied_path.name)
        self.assertIs(payload.get("problematic_copy_created"), True)
        self.assertIs(payload.get("problematic_copy_byte_identical"), True)
        self.assertIn(payload.get("recovered_file"), (None, ""))

        self.assertEqual(esito_path.name, f"{copied_path.name}.esito.json")

    def _hash_lookup(self, status: AudioHashStatus, value: str | None):
        return SimpleNamespace(
            status=status,
            audio_hash_sha256=value,
            frames_count=10,
            first_frame_offset=0,
            last_frame_end_offset=1000,
            anomalies=[],
        )

    def test_batch_1_all_associable_and_report_complete(self) -> None:
        for idx in range(3):
            payloads = (0x10 + idx, 0x20 + idx, 0x30 + idx)
            self._write(self.originals_dir, f"Track{idx}.mp3", _build_mp3(audio_payloads=payloads))
            self._write(
                self.problematic_dir,
                f"Track{idx}.mp3",
                _build_mp3(audio_payloads=payloads, synct_text=b"|100|A|200|", chord_text=b"|100|C|200|"),
            )

        result = self._run_batch()

        self.assertTrue(result.success)
        self.assertEqual(result.total_problematic, 3)
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_TAGS.value], 3)
        self.assertTrue(Path(result.report_paths["csv"]).is_file())

    def test_a_no_subfolder_scan(self) -> None:
        self._write(self.problematic_dir, "Top.mp3", _build_mp3())
        self._write(self.originals_dir, "Top.mp3", _build_mp3())
        self._write(self.problematic_dir / "sub", "Inner.mp3", _build_mp3())
        self._write(self.originals_dir / "sub", "Inner.mp3", _build_mp3())

        result = self._run_batch()

        self.assertEqual(result.total_problematic, 1)
        self.assertTrue(all(item.problematic_name != "Inner.mp3" for item in result.items))

    def test_b_same_name_case_insensitive(self) -> None:
        self._write(self.problematic_dir, "Volare.mp3", _build_mp3(audio_payloads=(0x11, 0x22, 0x33)))
        self._write(self.originals_dir, "VOLARE.MP3", _build_mp3(audio_payloads=(0x11, 0x22, 0x33)))

        result = self._run_batch()

        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value], 1)

    def test_c_similar_name_but_not_identical(self) -> None:
        self._write(self.problematic_dir, "Volare.mp3", _build_mp3())
        self._write(self.originals_dir, "Volare_.mp3", _build_mp3())

        result = self._run_batch()

        self.assertEqual(result.counters[MP3BatchOutcome.ORIGINAL_NOT_FOUND.value], 1)

    def test_d_destination_equal_problematic(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3())
        self._write(self.originals_dir, "Song.mp3", _build_mp3())

        result = self._run_batch(destination=self.problematic_dir)

        self.assertTrue(any(path.name.startswith("Diagnosi Recupero ") for path in self.problematic_dir.iterdir() if path.is_dir()))
        self.assertEqual(result.total_problematic, 1)
        self.assertEqual(len(result.items), 1)

    def test_e_single_file_terminates_once_and_single_recovery_call(self) -> None:
        self._write(self.problematic_dir, "Solo.mp3", _build_mp3())
        self._write(self.originals_dir, "Solo.mp3", _build_mp3())

        calls = {"count": 0}
        from mp3_recovery_batch import recover_mp3_from_original as real_recover

        def _wrapped(*args, **kwargs):
            calls["count"] += 1
            return real_recover(*args, **kwargs)

        with mock.patch("mp3_recovery_batch.recover_mp3_from_original", side_effect=_wrapped):
            result = self._run_batch()

        self.assertEqual(result.total_problematic, 1)
        self.assertEqual(result.processed_problematic, 1)
        self.assertEqual(calls["count"], 1)

        with Path(result.report_paths["csv"]).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)

    def test_f_no_repeated_processing_when_duplicate_path_seen(self) -> None:
        problematic = self._write(self.problematic_dir, "Dup.mp3", _build_mp3())
        self._write(self.originals_dir, "Dup.mp3", _build_mp3())

        from mp3_recovery_batch import _scan_mp3_files_non_recursive as real_scan

        def _scan_with_duplicate(root: Path):
            values = real_scan(root)
            if root == self.problematic_dir.resolve() and values:
                return [values[0], values[0]]
            return values

        calls = {"count": 0}
        from mp3_recovery_batch import recover_mp3_from_original as real_recover

        def _wrapped(*args, **kwargs):
            calls["count"] += 1
            return real_recover(*args, **kwargs)

        with mock.patch("mp3_recovery_batch._scan_mp3_files_non_recursive", side_effect=_scan_with_duplicate):
            with mock.patch("mp3_recovery_batch.recover_mp3_from_original", side_effect=_wrapped):
                result = self._run_batch()

        self.assertEqual(calls["count"], 1)
        self.assertEqual(result.total_problematic, 2)
        self.assertEqual(result.counters[MP3BatchOutcome.ERROR.value], 1)
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value], 1)
        self.assertTrue(problematic.is_file())

    def test_g_interruption_reaches_final_state(self) -> None:
        for idx in range(3):
            payloads = (0x10 + idx, 0x20 + idx, 0x30 + idx)
            self._write(self.problematic_dir, f"K{idx}.mp3", _build_mp3(audio_payloads=payloads))
            self._write(self.originals_dir, f"K{idx}.mp3", _build_mp3(audio_payloads=payloads))

        cancel_event = _CancelAfterOne()

        def _progress(current: int, total: int, message: str) -> None:
            if current >= 1:
                cancel_event.cancel()

        result = recover_mp3_batch_from_folders(
            problematic_dir=self.problematic_dir,
            originals_dir=self.originals_dir,
            destination_dir=self.destination_dir,
            progress_callback=_progress,
            cancel_event=cancel_event,
        )

        self.assertTrue(result.interrupted)
        self.assertGreaterEqual(result.counters[MP3BatchOutcome.INTERRUPTED.value], 1)

    def test_original_incompatible(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3(audio_payloads=(0x11, 0x22, 0x33)))
        self._write(self.originals_dir, "Song.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = self._run_batch()

        self.assertEqual(result.counters[MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value], 1)

    def test_name_collision_in_output(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3())
        self._write(self.originals_dir, "Song.mp3", _build_mp3())

        result = self._run_batch()

        recovered_items = [item for item in result.items if item.outcome == MP3BatchOutcome.RECOVERED_UNCHANGED]
        self.assertEqual(len(recovered_items), 1)
        recovered_path = Path(recovered_items[0].recovered_path)
        self.assertTrue(recovered_path.is_file())
        self.assertIn("Esito Recupero File", str(recovered_path))
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value], 1)

    def test_forced_mode_creates_dedicated_output_and_report_fields(self) -> None:
        original = self._write(self.originals_dir, "Force.mp3", _build_mp3())
        problematic = self._write(self.problematic_dir, "Force.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC), synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))

        result = recover_mp3_batch_from_folders(
            problematic_dir=self.problematic_dir,
            originals_dir=self.originals_dir,
            destination_dir=self.destination_dir,
            recovery_mode=RecoveryMode.FORCED,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_FORCED.value], 1)
        item = result.items[0]
        self.assertEqual(item.recovery_mode, RecoveryMode.FORCED.value)
        self.assertTrue(item.forced_recovery)
        self.assertFalse(item.audio_comparison_executed)
        self.assertEqual(item.outcome, MP3BatchOutcome.RECOVERED_FORCED)
        self.assertTrue(Path(result.report_paths["csv"]).is_file())
        self.assertIn("Recuperati forzatamente", Path(result.report_paths["csv"]).read_text(encoding="utf-8"))
        self.assertTrue(original.is_file())
        self.assertTrue(problematic.is_file())

        esito_json = Path(item.esito_json_path)
        payload = json.loads(esito_json.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("recovery_mode"), "forced")
        self.assertTrue(payload.get("forced_recovery"))
        self.assertIsInstance(payload.get("audio_comparison"), dict)
        self.assertFalse(payload["audio_comparison"].get("executed"))

    def test_forced_mode_without_tags_keeps_byte_identical_original_copy(self) -> None:
        original_bytes = _build_mp3(prefix=b"PRE", tail=b"TAIL")
        original = self._write(self.originals_dir, "Plain.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "Plain.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = recover_mp3_batch_from_folders(
            problematic_dir=self.problematic_dir,
            originals_dir=self.originals_dir,
            destination_dir=self.destination_dir,
            recovery_mode=RecoveryMode.FORCED,
        )

        self.assertTrue(result.success)
        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.RECOVERED_FORCED)
        self.assertEqual(Path(item.recovered_path).read_bytes(), original_bytes)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(problematic.read_bytes(), _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

    def test_forced_mode_collision_is_numbered(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3())
        self._write(self.originals_dir, "Song.mp3", _build_mp3())
        fixed_now = __import__("datetime").datetime(2026, 7, 27, 9, 0, 0)
        session_root = self.destination_dir / "Diagnosi Recupero 2026-07-27_09-00-00"
        session_forced = session_root / "Esito Recupero File" / "Recuperati forzatamente"
        session_forced.mkdir(parents=True, exist_ok=True)
        (session_forced / "Song.mp3").write_bytes(b"existing")

        with mock.patch("mp3_recovery_batch.datetime") as datetime_mock:
            datetime_mock.now.return_value = fixed_now
            datetime_mock.side_effect = lambda *args, **kwargs: __import__("datetime").datetime(*args, **kwargs)
            result = recover_mp3_batch_from_folders(
                problematic_dir=self.problematic_dir,
                originals_dir=self.originals_dir,
                destination_dir=self.destination_dir,
                recovery_mode=RecoveryMode.FORCED,
            )

        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_FORCED.value], 1)
        self.assertTrue(Path(result.items[0].recovered_path).name.startswith("Song_"))

    def test_rec_tail_1_normal_mode_tail_only_non_blocking(self) -> None:
        self._write(self.problematic_dir, "TailNormal.mp3", _build_mp3(synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))
        self._write(self.originals_dir, "TailNormal.mp3", _build_mp3())

        with mock.patch(
            "mp3_recovery_batch._assess_recovered_integrity_policy",
            return_value={
                "assessed": True,
                "integrity_certified": True,
                "classification_reason": "Anomalie solo in coda finale non bloccanti.",
                "blocking_issues_count": 0,
                "non_blocking_tail_issues_count": 1,
            },
        ):
            result = recover_mp3_batch_from_folders(
                problematic_dir=self.problematic_dir,
                originals_dir=self.originals_dir,
                destination_dir=self.destination_dir,
                recovery_mode=RecoveryMode.NORMAL,
            )

        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.RECOVERED_TAGS)
        self.assertIn("Avvertimenti di coda non bloccanti", item.note)
        self.assertEqual(item.non_blocking_tail_issues_count, 1)
        self.assertEqual(item.blocking_issues_count, 0)

    def test_rec_tail_2_forced_mode_tail_only_non_blocking(self) -> None:
        self._write(self.problematic_dir, "TailForced.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC), synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))
        self._write(self.originals_dir, "TailForced.mp3", _build_mp3())

        with mock.patch(
            "mp3_recovery_batch._assess_recovered_integrity_policy",
            return_value={
                "assessed": True,
                "integrity_certified": True,
                "classification_reason": "Anomalie solo in coda finale non bloccanti.",
                "blocking_issues_count": 0,
                "non_blocking_tail_issues_count": 1,
            },
        ):
            result = recover_mp3_batch_from_folders(
                problematic_dir=self.problematic_dir,
                originals_dir=self.originals_dir,
                destination_dir=self.destination_dir,
                recovery_mode=RecoveryMode.FORCED,
            )

        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.RECOVERED_FORCED)
        self.assertIn("Avvertimenti di coda non bloccanti", item.note)
        self.assertEqual(item.non_blocking_tail_issues_count, 1)
        self.assertEqual(item.blocking_issues_count, 0)

    def test_rec_tail_3_boundary_crossing_remains_blocking(self) -> None:
        self._write(self.problematic_dir, "TailBoundary.mp3", _build_mp3(synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))
        self._write(self.originals_dir, "TailBoundary.mp3", _build_mp3())

        with mock.patch(
            "mp3_recovery_batch._assess_recovered_integrity_policy",
            return_value={
                "assessed": True,
                "integrity_certified": False,
                "classification_reason": "Anomalia attraversa il confine della finestra finale.",
                "blocking_issues_count": 1,
                "non_blocking_tail_issues_count": 0,
            },
        ):
            result = recover_mp3_batch_from_folders(
                problematic_dir=self.problematic_dir,
                originals_dir=self.originals_dir,
                destination_dir=self.destination_dir,
                recovery_mode=RecoveryMode.NORMAL,
            )

        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.FINAL_VERIFICATION_FAILED)
        self.assertEqual(item.blocking_issues_count, 1)
        self.assertEqual(item.non_blocking_tail_issues_count, 0)

    def test_rec_tail_4_tail_and_previous_anomaly_is_negative(self) -> None:
        self._write(self.problematic_dir, "TailMixed.mp3", _build_mp3(synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))
        self._write(self.originals_dir, "TailMixed.mp3", _build_mp3())

        with mock.patch(
            "mp3_recovery_batch._assess_recovered_integrity_policy",
            return_value={
                "assessed": True,
                "integrity_certified": False,
                "classification_reason": "Anomalia bloccante fuori coda + anomalia finale tollerata.",
                "blocking_issues_count": 1,
                "non_blocking_tail_issues_count": 1,
            },
        ):
            result = recover_mp3_batch_from_folders(
                problematic_dir=self.problematic_dir,
                originals_dir=self.originals_dir,
                destination_dir=self.destination_dir,
                recovery_mode=RecoveryMode.NORMAL,
            )

        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.FINAL_VERIFICATION_FAILED)
        self.assertEqual(item.blocking_issues_count, 1)
        self.assertEqual(item.non_blocking_tail_issues_count, 1)

    def test_multiple_same_name_originals_is_ambiguous(self) -> None:
        self._write(self.problematic_dir, "A.mp3", _build_mp3())

        p1 = self._write(self.originals_dir, "A.mp3", _build_mp3())
        p2 = self._write(self.originals_dir, "B.mp3", _build_mp3())

        def _index_override(_files: list[Path]):
            return {"a.mp3": [p1, p2]}

        with mock.patch("mp3_recovery_batch._build_original_index_by_full_name", side_effect=_index_override):
            result = self._run_batch()

        self.assertEqual(result.counters[MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS.value], 1)

    def test_original_immutability_massive(self) -> None:
        originals: list[Path] = []
        for idx in range(4):
            payloads = (0x10 + idx, 0x20 + idx, 0x30 + idx)
            originals.append(self._write(self.originals_dir, f"M{idx}.mp3", _build_mp3(audio_payloads=payloads)))
            self._write(self.problematic_dir, f"M{idx}.mp3", _build_mp3(audio_payloads=payloads))

        before = {
            path: (self._sha(path), path.stat().st_size, int(path.stat().st_mtime))
            for path in originals
        }

        result = self._run_batch()
        self.assertTrue(result.originals_unchanged)

        after = {
            path: (self._sha(path), path.stat().st_size, int(path.stat().st_mtime))
            for path in originals
        }

        self.assertEqual(before, after)

    def test_matching_performance_1117_originals_hash_only_for_candidate(self) -> None:
        self._write(self.problematic_dir, "Alpha.mp3", _build_mp3())

        for idx in range(1116):
            self._write(self.originals_dir, f"noise_{idx:04d}.mp3", _build_mp3())
        matching_original = self._write(self.originals_dir, "ALPHA.MP3", _build_mp3())

        candidate_and_problematic_reads: list[str] = []
        hash_calls = {"count": 0}

        original_read_bytes = Path.read_bytes

        def _spy_read_bytes(path_obj: Path):
            resolved = str(path_obj.resolve())
            candidate_and_problematic_reads.append(resolved)
            return original_read_bytes(path_obj)

        def _fake_hash(_data: bytes, **kwargs):
            hash_calls["count"] += 1
            return SimpleNamespace(status=AudioHashStatus.VALID_AUDIO_STREAM, audio_hash_sha256="SAME_HASH")

        def _fake_snapshot(_path: Path):
            return ("sha", 1, 1)

        def _fake_recover(**kwargs):
            return SimpleNamespace(
                success=False,
                status=MP3RecoveryStatus.ORIGINAL_FILE_NOT_COMPATIBLE,
                error=None,
                notes=[],
                original_path=str(kwargs["original_path"]),
                problematic_path=str(kwargs["problematic_path"]),
                output_path=None,
                temporary_path=None,
                strategy=MP3RecoveryStatus.ORIGINAL_FILE_NOT_COMPATIBLE.value,
                destination_renamed=False,
                compatibility_ok=False,
                problematic_winlive_present=False,
                original_winlive_present=False,
                tags_transferred=False,
                verification_ok=False,
                original_sha256_before="x",
                original_sha256_after="x",
                original_audio_hash="SAME_HASH",
                problematic_audio_hash="SAME_HASH",
                recovered_audio_hash="SAME_HASH",
            )

        with mock.patch("pathlib.Path.read_bytes", new=_spy_read_bytes):
            with mock.patch("mp3_recovery_batch.compute_mpeg_audio_hash", side_effect=_fake_hash):
                with mock.patch("mp3_recovery_batch._snapshot_file", side_effect=_fake_snapshot):
                    with mock.patch("mp3_recovery_batch.recover_mp3_from_original", side_effect=_fake_recover):
                        result = self._run_batch()

        self.assertTrue(result.success)
        self.assertEqual(result.total_problematic, 1)
        self.assertEqual(hash_calls["count"], 2)

        problematic_resolved = str((self.problematic_dir / "Alpha.mp3").resolve())
        candidate_resolved = str(matching_original.resolve())
        read_set = set(candidate_and_problematic_reads)
        self.assertIn(problematic_resolved, read_set)
        self.assertIn(candidate_resolved, read_set)
        self.assertEqual(len(candidate_and_problematic_reads), 2)

        non_candidate_reads = [
            path
            for path in candidate_and_problematic_reads
            if path not in {problematic_resolved, candidate_resolved}
        ]
        self.assertEqual(non_candidate_reads, [])

    def test_interrupted_hash_keeps_partial_times_and_state(self) -> None:
        self._write(self.problematic_dir, "Stop.mp3", _build_mp3())
        self._write(self.originals_dir, "Stop.mp3", _build_mp3())

        calls = {"count": 0}

        def _cancel_on_first_hash(_data: bytes, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return SimpleNamespace(status=AudioHashStatus.CANCELLED, audio_hash_sha256=None)
            return SimpleNamespace(status=AudioHashStatus.VALID_AUDIO_STREAM, audio_hash_sha256="OK")

        monotonic_tick = {"t": 0.0}

        def _next_time() -> float:
            monotonic_tick["t"] += 0.05
            return monotonic_tick["t"]

        with mock.patch("mp3_recovery_batch.compute_mpeg_audio_hash", side_effect=_cancel_on_first_hash):
            with mock.patch("mp3_recovery_batch.time.monotonic", side_effect=_next_time):
                result = self._run_batch()

        self.assertTrue(result.interrupted)
        self.assertEqual(result.examined_problematic, 1)
        self.assertEqual(result.completed_problematic, 0)
        self.assertEqual(result.processed_problematic, 1)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].outcome, MP3BatchOutcome.INTERRUPTED)
        self.assertGreater(result.items[0].total_file_seconds, 0.0)
        self.assertGreater(result.items[0].hash_problematic_seconds, 0.0)

        current_state = Path(result.output_root) / "Diagnostica Scanner MPEG" / "scanner_mpeg_current_state.json"
        self.assertTrue(current_state.is_file())
        payload = current_state.read_text(encoding="utf-8")
        self.assertIn('"phase": "File completato"', payload)
        self.assertIn('"last_phase": "Calcolo hash problematico"', payload)
        self.assertIn('"batch_status": "Interrotto"', payload)

    def test_session_timestamp_tree_is_created(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3())
        self._write(self.originals_dir, "Song.mp3", _build_mp3())

        result = self._run_batch()
        session_root = Path(result.session_folder)

        self.assertTrue(session_root.is_dir())
        self.assertEqual(session_root.name[:17], "Diagnosi Recupero")
        self.assertTrue((session_root / "Esito Recupero File").is_dir())
        self.assertTrue((session_root / "Report").is_dir())
        self.assertTrue((session_root / "Diagnostica Scanner MPEG").is_dir())
        self.assertTrue((session_root / "Riepilogo sessione.txt").is_file())

        csv_path = Path(result.report_paths["csv"])
        self.assertTrue(csv_path.is_file())
        self.assertEqual(csv_path.parent.name, "Report")

    def test_negative_outcome_writes_esito_json(self) -> None:
        self._write(self.problematic_dir, "Volare.mp3", _build_mp3())
        self._write(self.originals_dir, "Volare_.mp3", _build_mp3())

        result = self._run_batch()
        self.assertEqual(result.counters[MP3BatchOutcome.ORIGINAL_NOT_FOUND.value], 1)
        self.assertEqual(len(result.items), 1)
        item = result.items[0]

        self.assertTrue(item.esito_json_path)
        esito_path = Path(item.esito_json_path)
        self.assertTrue(esito_path.is_file())

        payload = json.loads(esito_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("final_result"), MP3BatchOutcome.ORIGINAL_NOT_FOUND.value)
        self.assertEqual(payload.get("problematic_file_name"), "Volare.mp3")
        self.assertEqual(payload.get("session_folder"), result.session_folder)
        self.assertEqual(payload.get("outcome_folder"), item.outcome_folder_path)
        self.assertTrue(payload.get("reason"))
        self._assert_negative_item_has_problematic_copy(item, MP3BatchOutcome.ORIGINAL_NOT_FOUND)

    def test_final_current_state_contains_outcome_and_session_fields(self) -> None:
        self._write(self.problematic_dir, "Song.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        self._write(self.originals_dir, "Song.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = self._run_batch()

        state_path = Path(result.session_folder) / "Diagnostica Scanner MPEG" / "scanner_mpeg_current_state.json"
        self.assertTrue(state_path.is_file())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state.get("batch_status"), "Completato")
        self.assertEqual(state.get("final_result"), MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value)
        self.assertEqual(state.get("session_folder"), result.session_folder)
        self.assertTrue(state.get("result_folder"))
        self.assertTrue(state.get("result_json"))
        self.assertTrue(state.get("final_timestamp"))

    def test_cleanup_removes_empty_outcome_leaf_directories(self) -> None:
        self._write(self.problematic_dir, "One.mp3", _build_mp3())
        self._write(self.originals_dir, "One.mp3", _build_mp3())

        result = self._run_batch()
        esiti_root = Path(result.session_folder) / "Esito Recupero File"
        self.assertTrue(esiti_root.is_dir())

        empty_dirs = [p for p in esiti_root.rglob("*") if p.is_dir() and not any(p.iterdir())]
        self.assertEqual(empty_dirs, [])

    def test_incompatible_outcome_writes_real_esito_json_with_expected_fields(self) -> None:
        self._write(self.problematic_dir, "Incompatibile.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        original_path = self._write(self.originals_dir, "Incompatibile.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))
        original_before = original_path.read_bytes()

        result = self._run_batch()
        original_after = original_path.read_bytes()

        self.assertEqual(result.counters[MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value], 1)
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_TAGS.value], 0)
        self.assertEqual(result.counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value], 0)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(original_before, original_after)

        item = result.items[0]
        self.assertEqual(item.outcome, MP3BatchOutcome.ORIGINAL_INCOMPATIBLE)
        self.assertEqual(item.recovered_path, "")
        self.assertTrue(item.esito_json_path)

        esito_path = Path(item.esito_json_path)
        self.assertTrue(esito_path.is_file())
        self.assertEqual(esito_path.parent.name, "Originale incompatibile")
        self.assertEqual(esito_path.parent.parent.name, "Esito Recupero File")

        payload = json.loads(esito_path.read_text(encoding="utf-8"))
        problematic_file = payload.get("problematic_file") or payload.get("problematic_file_name")
        problematic_path = payload.get("problematic_path") or payload.get("problematic_file_path")
        original_file = payload.get("original_file") or payload.get("original_file_name")
        original_path = payload.get("original_path") or payload.get("original_file_path")
        recovered_file = payload.get("recovered_file")
        if recovered_file is None:
            recovered_file = payload.get("recovered_file_path")

        self.assertEqual(problematic_file, "Incompatibile.mp3")
        self.assertEqual(Path(problematic_path).name, "Incompatibile.mp3")
        self.assertEqual(original_file, "Incompatibile.mp3")
        self.assertEqual(Path(original_path).name, "Incompatibile.mp3")
        self.assertEqual(payload.get("final_result"), MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value)
        self.assertTrue(payload.get("reason"))
        self.assertTrue(payload.get("problematic_audio_hash"))
        self.assertTrue(payload.get("original_audio_hash"))
        self.assertIn(recovered_file, (None, ""))
        self.assertIs(payload.get("originals_unchanged"), True)
        self.assertEqual(payload.get("session_folder"), result.session_folder)
        self.assertTrue(payload.get("outcome_folder"))
        self.assertEqual(payload.get("error", ""), "")
        self.assertTrue(payload.get("session_timestamp"))

        phase_durations = payload.get("phase_durations_seconds", {})
        self.assertIn("search_original", phase_durations)
        self.assertIn("hash_problematic", phase_durations)
        self.assertIn("hash_original", phase_durations)
        self.assertIn("recovery", phase_durations)
        self.assertIn("verification_final", phase_durations)
        self.assertGreaterEqual(float(payload.get("duration_total_seconds", 0.0) or 0.0), 0.0)

        with Path(result.report_paths["csv"]).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("Nome file problematico"), "Incompatibile.mp3")
        self.assertEqual(rows[0].get("Conferma originali invariati"), "SI")
        self.assertEqual(rows[0].get("File recuperato"), "NO")
        self.assertEqual(rows[0].get("Copia problematico"), "SI")

        self._assert_negative_item_has_problematic_copy(item, MP3BatchOutcome.ORIGINAL_INCOMPATIBLE)

    def test_original_not_found_sets_originals_unchanged_null_in_esito_json(self) -> None:
        self._write(self.problematic_dir, "SenzaOriginale.mp3", _build_mp3())
        self._write(self.originals_dir, "Diverso.mp3", _build_mp3())

        result = self._run_batch()
        self.assertEqual(result.counters[MP3BatchOutcome.ORIGINAL_NOT_FOUND.value], 1)
        self.assertEqual(len(result.items), 1)

        item = result.items[0]
        self.assertTrue(item.esito_json_path)
        esito_payload = json.loads(Path(item.esito_json_path).read_text(encoding="utf-8"))
        self.assertIsNone(esito_payload.get("originals_unchanged"))
        self._assert_negative_item_has_problematic_copy(item, MP3BatchOutcome.ORIGINAL_NOT_FOUND)

    def test_negative_outcome_collision_creates_numbered_problematic_copy_and_json(self) -> None:
        source = self._write(self.problematic_dir, "Collision.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        target_dir = self.root / "collision_target"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "Collision.mp3").write_bytes(b"already-there")

        copied = _copy_with_collision(source, target_dir, "Collision.mp3")

        self.assertEqual(copied.name, "Collision_1.mp3")
        self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_interrupted_outcome_copies_problematic_file(self) -> None:
        self._write(self.problematic_dir, "StopCopy.mp3", _build_mp3())
        self._write(self.originals_dir, "StopCopy.mp3", _build_mp3())

        def _cancel_hash(_data: bytes, **kwargs):
            return SimpleNamespace(status=AudioHashStatus.CANCELLED, audio_hash_sha256=None)

        with mock.patch("mp3_recovery_batch.compute_mpeg_audio_hash", side_effect=_cancel_hash):
            result = self._run_batch()

        self.assertTrue(result.interrupted)
        self.assertEqual(len(result.items), 1)
        self._assert_negative_item_has_problematic_copy(result.items[0], MP3BatchOutcome.INTERRUPTED)

    def test_summary_file_contains_expected_no_recovery_content(self) -> None:
        self._write(self.problematic_dir, "NoRec.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        self._write(self.originals_dir, "NoRec.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = self._run_batch()
        summary_path = Path(result.session_folder) / "Riepilogo sessione.txt"
        self.assertTrue(summary_path.is_file())
        content = summary_path.read_text(encoding="utf-8")

        self.assertIn("File totali: 1", content)
        self.assertIn("File esaminati: 1", content)
        self.assertIn("File completati: 1", content)
        self.assertIn("Recuperati con TAG: 0", content)
        self.assertIn("Recuperati come copia invariata: 0", content)
        self.assertIn("Originali incompatibili: 1", content)
        self.assertIn("CONFIGURAZIONE DELLA SESSIONE", content)
        self.assertIn("Tipo di elaborazione:", content)
        self.assertIn("Recupero MP3", content)
        self.assertIn("RIEPILOGO RISULTATI", content)
        self.assertIn("Percorso completo esito della sessione:", content)
        self.assertIn(result.session_folder, content)
        self.assertIn("Data e ora inizio sessione:", content)
        self.assertIn("Data e ora fine sessione:", content)
        self.assertIn("Durata complessiva (s):", content)
        self.assertIn("Nessun file MP3 recuperato.", content)

    def test_final_current_state_for_incompatible_has_required_terminal_fields(self) -> None:
        self._write(self.problematic_dir, "StateIncompat.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        self._write(self.originals_dir, "StateIncompat.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = self._run_batch()
        state_path = Path(result.session_folder) / "Diagnostica Scanner MPEG" / "scanner_mpeg_current_state.json"
        self.assertTrue(state_path.is_file())
        state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(state.get("phase"), "File completato")
        self.assertEqual(state.get("final_result"), MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value)
        self.assertTrue(state.get("reason"))
        self.assertIsNone(state.get("recovered_file"))
        self.assertTrue(state.get("result_json"))
        self.assertTrue(state.get("session_folder"))
        self.assertTrue(state.get("result_folder"))
        self.assertEqual(state.get("files_examined"), 1)
        self.assertEqual(state.get("files_completed"), 1)
        self.assertEqual(state.get("files_total"), 1)
        self.assertEqual(state.get("batch_status"), "Completato")
        self.assertTrue(state.get("final_timestamp"))
        self.assertNotEqual(state.get("phase"), "Scansione frame MPEG")

    def test_cleanup_session_dirs_preserves_external_and_populated_folders(self) -> None:
        session_root = self.root / "destination" / "Diagnosi Recupero 2026-07-27_12-00-00"
        esiti_root = session_root / "Esito Recupero File"
        report_root = session_root / "Report"
        diagnostics_root = session_root / "Diagnostica Scanner MPEG"

        outcome_names = [
            "Recuperati con TAG WinLive trasferiti",
            "Recuperati come copia originale invariata",
            "Originale non trovato",
            "Originale incompatibile",
            "Piu originali compatibili",
            "Errori",
            "Interrotti",
        ]
        for name in outcome_names:
            (esiti_root / name).mkdir(parents=True, exist_ok=True)
        report_root.mkdir(parents=True, exist_ok=True)
        diagnostics_root.mkdir(parents=True, exist_ok=True)

        kept_outcome = esiti_root / "Originale incompatibile"
        (kept_outcome / "song.mp3.esito.json").write_text("{}", encoding="utf-8")
        (report_root / "report.csv").write_text("h1,h2\n", encoding="utf-8")
        (diagnostics_root / "scanner_mpeg_current_state.json").write_text("{}", encoding="utf-8")

        outside_empty = self.root / "outside_empty"
        outside_empty.mkdir(parents=True, exist_ok=True)
        outside_preexisting = self.root / "destination" / "Preesistente Fuori Sessione"
        outside_preexisting.mkdir(parents=True, exist_ok=True)

        symlink_created = False
        link_path = session_root / "LinkEsterno"
        external_target = self.root / "external_target"
        external_target.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(external_target), str(link_path), target_is_directory=True)
            symlink_created = True
        except (AttributeError, NotImplementedError, OSError):
            symlink_created = False

        _cleanup_empty_session_dirs(session_root, log_callback=None)

        self.assertTrue(session_root.is_dir())
        self.assertTrue(esiti_root.is_dir())
        self.assertTrue(kept_outcome.is_dir())
        self.assertTrue(report_root.is_dir())
        self.assertTrue(diagnostics_root.is_dir())

        self.assertFalse((esiti_root / "Recuperati con TAG WinLive trasferiti").exists())
        self.assertFalse((esiti_root / "Recuperati come copia originale invariata").exists())
        self.assertFalse((esiti_root / "Originale non trovato").exists())
        self.assertFalse((esiti_root / "Piu originali compatibili").exists())
        self.assertFalse((esiti_root / "Errori").exists())
        self.assertFalse((esiti_root / "Interrotti").exists())

        self.assertTrue(outside_empty.is_dir())
        self.assertTrue(outside_preexisting.is_dir())
        self.assertTrue(external_target.is_dir())
        if symlink_created:
            self.assertTrue(link_path.exists())

    def test_phase_timings_are_compatible_with_total_using_controlled_clock(self) -> None:
        self._write(self.problematic_dir, "Times.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        self._write(self.originals_dir, "Times.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))

        tick = {"t": 0.0}

        def _next_time() -> float:
            tick["t"] += 0.001
            return tick["t"]

        with mock.patch("mp3_recovery_batch.time.monotonic", side_effect=_next_time):
            result = self._run_batch()

        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        phases_sum = (
            item.search_original_seconds
            + item.hash_problematic_seconds
            + item.hash_original_seconds
            + item.recovery_seconds
            + item.verification_final_seconds
        )
        self.assertGreaterEqual(item.search_original_seconds, 0.0)
        self.assertGreaterEqual(item.hash_problematic_seconds, 0.0)
        self.assertGreaterEqual(item.hash_original_seconds, 0.0)
        self.assertGreaterEqual(item.recovery_seconds, 0.0)
        self.assertGreaterEqual(item.verification_final_seconds, 0.0)
        self.assertGreater(item.total_file_seconds, 0.0)
        self.assertGreaterEqual(item.total_file_seconds + 1e-9, phases_sum)
        self.assertLessEqual(item.total_file_seconds - phases_sum, 0.2)

    def test_analysis_1_content_incompatible_tonality_compatible_no_short_circuit(self) -> None:
        problematic = self.problematic_dir / "A.mp3"
        original = self.originals_dir / "A.mp3"
        duration_cache: dict[Path, tuple[int | None, str | None]] = {}

        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_COMPATIBLE,
            "detail": "Tonalita allineata.",
            "problematic_value": "C#m",
            "original_value": "C#m",
            "semitone_difference": 0,
            "confidence": "Alta",
            "technical_error": "",
        }) as tonality_mock:
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(180000, None), (180000, None)]) as duration_mock:
                analysis = _compute_compatibility_analysis(
                    problematic_path=problematic,
                    original_path=original,
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "H1"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "H2"),
                    duration_cache=duration_cache,
                )

        self.assertEqual(analysis["overall_status"], OVERALL_INCOMPATIBLE)
        self.assertEqual(analysis["reasons"], ["Contenuto audio differente"])
        self.assertEqual(analysis["checks"]["tonality"]["status"], CHECK_COMPATIBLE)
        self.assertEqual(tonality_mock.call_count, 1)
        self.assertEqual(duration_mock.call_count, 2)

    def test_analysis_2_content_compatible_tonality_incompatible(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_INCOMPATIBLE,
            "detail": "Differenza di tonalita stimata.",
            "problematic_value": "Cm",
            "original_value": "Dm",
            "semitone_difference": 2,
            "confidence": "Media",
            "technical_error": "",
        }):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(200000, None), (200000, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "B.mp3",
                    original_path=self.originals_dir / "B.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "HX"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "HX"),
                    duration_cache={},
                )

        self.assertEqual(analysis["overall_status"], OVERALL_INCOMPATIBLE)
        self.assertIn("Tonalita differente", analysis["reasons"])
        self.assertEqual(analysis["checks"]["tonality"]["semitone_difference"], 2)

    def test_analysis_3_content_and_tonality_incompatible_two_problem_rows(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_INCOMPATIBLE,
            "detail": "Tonalita diversa.",
            "problematic_value": "Am",
            "original_value": "Bm",
            "semitone_difference": 2,
            "confidence": "Alta",
            "technical_error": "",
        }):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(180000, None), (180200, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "C.mp3",
                    original_path=self.originals_dir / "C.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "H1"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "H2"),
                    duration_cache={},
                )

        self.assertIn("Contenuto audio differente", analysis["reasons"])
        self.assertIn("Tonalita differente", analysis["reasons"])
        item = MP3BatchItemResult(
            index=1,
            problematic_name="C.mp3",
            problematic_path=str(self.problematic_dir / "C.mp3"),
            original_name="C.mp3",
            original_path=str(self.originals_dir / "C.mp3"),
            outcome=MP3BatchOutcome.ORIGINAL_INCOMPATIBLE,
            strategy="",
            problematic_winlive_present="",
            original_winlive_present="",
            recovered_path="",
            duration_seconds=0.0,
            note="",
            original_unchanged="",
            problematic_audio_hash="H1",
            original_audio_hash="H2",
            compatibility_analysis=analysis,
        )
        problems = _build_problem_rows(item)
        self.assertEqual(len(problems), 2)

    def test_analysis_4_duration_over_tolerance(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={"status": CHECK_COMPATIBLE, "detail": "", "technical_error": ""}):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(222518, None), (224061, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "D.mp3",
                    original_path=self.originals_dir / "D.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "HH"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "HH"),
                    duration_cache={},
                )

        self.assertEqual(analysis["checks"]["duration"]["status"], CHECK_INCOMPATIBLE)
        self.assertEqual(analysis["problematic_duration_ms"], 222518)
        self.assertEqual(analysis["original_duration_ms"], 224061)
        self.assertEqual(analysis["duration_difference_ms"], 1543)
        self.assertAlmostEqual(float(analysis["duration_difference_percent"]), 0.6887, places=3)

    def test_analysis_5_duration_within_tolerance(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={"status": CHECK_COMPATIBLE, "detail": "", "technical_error": ""}):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(100000, None), (100300, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "E.mp3",
                    original_path=self.originals_dir / "E.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "SAME"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "SAME"),
                    duration_cache={},
                )

        self.assertEqual(analysis["checks"]["duration"]["status"], CHECK_COMPATIBLE)
        self.assertNotIn("Durata differente", analysis["reasons"])

    def test_analysis_6_single_check_not_determinable_others_execute(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_NOT_DETERMINABLE,
            "detail": "Segnale insufficiente.",
            "technical_error": "",
        }):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(120000, None), (120000, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "F.mp3",
                    original_path=self.originals_dir / "F.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "EQ"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "EQ"),
                    duration_cache={},
                )

        self.assertEqual(analysis["overall_status"], OVERALL_COMPATIBLE)
        self.assertEqual(analysis["checks"]["tonality"]["status"], CHECK_NOT_DETERMINABLE)
        self.assertEqual(analysis["checks"]["duration"]["status"], CHECK_COMPATIBLE)
        self.assertEqual(analysis["checks"]["audio_content"]["status"], CHECK_COMPATIBLE)

    def test_analysis_7_tonality_technical_error_does_not_stop_other_checks(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_TECHNICAL_ERROR,
            "detail": "Errore calcolo tonalita.",
            "technical_error": "FFT non disponibile",
        }):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(130000, None), (130000, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "G.mp3",
                    original_path=self.originals_dir / "G.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "EQ2"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "EQ2"),
                    duration_cache={},
                )

        self.assertEqual(analysis["checks"]["audio_content"]["status"], CHECK_COMPATIBLE)
        self.assertEqual(analysis["checks"]["duration"]["status"], CHECK_COMPATIBLE)
        self.assertIn("tonality: FFT non disponibile", analysis["technical_errors"])

    def test_analysis_8_three_concurrent_causes(self) -> None:
        with mock.patch("mp3_recovery_batch._estimate_tonality", return_value={
            "status": CHECK_INCOMPATIBLE,
            "detail": "Tonalita differente.",
            "problematic_value": "Am",
            "original_value": "Cm",
            "semitone_difference": 3,
            "confidence": "Alta",
            "technical_error": "",
        }):
            with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(180000, None), (185000, None)]):
                analysis = _compute_compatibility_analysis(
                    problematic_path=self.problematic_dir / "H.mp3",
                    original_path=self.originals_dir / "H.mp3",
                    problematic_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "A1"),
                    original_hash_result=self._hash_lookup(AudioHashStatus.VALID_AUDIO_STREAM, "A2"),
                    duration_cache={},
                )

        self.assertIn("Contenuto audio differente", analysis["reasons"])
        self.assertIn("Tonalita differente", analysis["reasons"])
        self.assertIn("Durata differente", analysis["reasons"])

    def test_analysis_9_session_tree_without_intermediate_folder(self) -> None:
        self._write(self.problematic_dir, "Tree.mp3", _build_mp3())
        self._write(self.originals_dir, "Tree.mp3", _build_mp3())

        with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(180000, None), (180000, None)]):
            result = self._run_batch()

        session_root = Path(result.session_folder)
        self.assertEqual(session_root.parent.resolve(), self.destination_dir.resolve())
        self.assertFalse((self.destination_dir / "Recupero MP3").exists())

    def test_analysis_10_destination_equals_problematic_no_recursive_reprocessing(self) -> None:
        self._write(self.problematic_dir, "One.mp3", _build_mp3())
        self._write(self.problematic_dir, "Two.mp3", _build_mp3(audio_payloads=(0x31, 0x32, 0x33)))
        self._write(self.originals_dir, "One.mp3", _build_mp3())
        self._write(self.originals_dir, "Two.mp3", _build_mp3(audio_payloads=(0x31, 0x32, 0x33)))

        with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(100000, None), (100000, None), (110000, None), (110000, None)]):
            result = self._run_batch(destination=self.problematic_dir)

        self.assertEqual(result.total_problematic, 2)
        self.assertEqual(result.processed_problematic, 2)
        processed_names = sorted(item.problematic_name for item in result.items)
        self.assertEqual(processed_names, ["One.mp3", "Two.mp3"])

    def test_analysis_11_complete_compatibility_reports_durations(self) -> None:
        self._write(self.problematic_dir, "Compat.mp3", _build_mp3())
        self._write(self.originals_dir, "Compat.mp3", _build_mp3())

        with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(222518, None), (222518, None)]):
            result = self._run_batch()

        item = result.items[0]
        analysis = item.compatibility_analysis
        self.assertEqual(analysis.get("overall_status"), OVERALL_COMPATIBLE)
        self.assertEqual(analysis.get("reasons"), [])
        self.assertEqual(analysis.get("problematic_duration_formatted"), "00:03:42.518")
        self.assertEqual(analysis.get("original_duration_formatted"), "00:03:42.518")

        with Path(result.report_paths["csv"]).open("r", encoding="utf-8", newline="") as handle:
            headers = list(csv.DictReader(handle).fieldnames or [])
        self.assertIn("Esito complessivo", headers)
        self.assertIn("Durata file problematico", headers)
        self.assertIn("Durata file originale", headers)
        self.assertIn("Cause incompatibilita", headers)

    def test_analysis_12_json_backward_compatible_with_compatibility_section(self) -> None:
        self._write(self.problematic_dir, "JsonCase.mp3", _build_mp3(audio_payloads=(0x10, 0x20, 0x30)))
        self._write(self.originals_dir, "JsonCase.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        with mock.patch("mp3_recovery_batch._probe_duration_ms", side_effect=[(222518, None), (224061, None)]):
            result = self._run_batch()

        item = result.items[0]
        self.assertTrue(item.esito_json_path)
        payload = json.loads(Path(item.esito_json_path).read_text(encoding="utf-8"))
        self.assertIn("problematic_file", payload)
        self.assertIn("final_result", payload)
        self.assertIn("compatibility_analysis", payload)
        compatibility = payload["compatibility_analysis"]
        self.assertTrue(compatibility.get("completed"))
        self.assertIn("overall_status", compatibility)
        self.assertIn("checks", compatibility)
        self.assertEqual(compatibility.get("problematic_duration_ms"), 222518)
        self.assertEqual(compatibility.get("original_duration_ms"), 224061)


class MP3RecoveryWorkerCallbackTests(unittest.TestCase):
    def test_single_completion_callback_emitted_once(self) -> None:
        done = threading.Event()
        calls = {"completed": 0, "errors": 0}

        fake_result = MP3RecoveryBatchResult(
            success=True,
            interrupted=False,
            error=None,
            total_problematic=1,
            processed_problematic=1,
            counters={
                MP3BatchOutcome.RECOVERED_UNCHANGED.value: 1,
            },
            elapsed_seconds=0.1,
            report_paths={"csv": "x"},
            output_root="x",
            items=[
                MP3BatchItemResult(
                    index=1,
                    problematic_name="Solo.mp3",
                    problematic_path="a",
                    original_name="Solo.mp3",
                    original_path="b",
                    outcome=MP3BatchOutcome.RECOVERED_UNCHANGED,
                    strategy="UNCHANGED_ORIGINAL_COPY",
                    problematic_winlive_present="NO",
                    original_winlive_present="NO",
                    recovered_path="c",
                    duration_seconds=0.1,
                    note="ok",
                    original_unchanged="SI",
                    problematic_audio_hash="h1",
                    original_audio_hash="h2",
                )
            ],
            originals_unchanged=True,
        )

        def _on_completed(_result):
            calls["completed"] += 1
            done.set()

        def _on_error(_msg: str):
            calls["errors"] += 1
            done.set()

        worker = MP3RecoveryWorker(on_completed=_on_completed, on_error=_on_error)

        with mock.patch("worker.recover_mp3_batch_from_folders", return_value=fake_result):
            worker.start(problematic_dir="a", originals_dir="b", destination_dir="c")
            done.wait(timeout=3)

        self.assertEqual(calls["errors"], 0)
        self.assertEqual(calls["completed"], 1)

    def test_interrupted_result_emits_completed_once_without_cancelled_callback(self) -> None:
        done = threading.Event()
        calls = {"completed": 0, "errors": 0, "cancelled": 0}

        fake_result = MP3RecoveryBatchResult(
            success=True,
            interrupted=True,
            error=None,
            total_problematic=1,
            processed_problematic=0,
            counters={
                MP3BatchOutcome.INTERRUPTED.value: 1,
            },
            elapsed_seconds=0.1,
            report_paths={"csv": "x"},
            output_root="x",
            items=[],
            originals_unchanged=True,
        )

        def _on_completed(_result):
            calls["completed"] += 1
            done.set()

        def _on_error(_msg: str):
            calls["errors"] += 1
            done.set()

        def _on_cancelled(_msg: str):
            calls["cancelled"] += 1

        worker = MP3RecoveryWorker(on_completed=_on_completed, on_error=_on_error, on_cancelled=_on_cancelled)

        with mock.patch("worker.recover_mp3_batch_from_folders", return_value=fake_result):
            worker.start(problematic_dir="a", originals_dir="b", destination_dir="c")
            done.wait(timeout=3)

        self.assertEqual(calls["errors"], 0)
        self.assertEqual(calls["completed"], 1)
        self.assertEqual(calls["cancelled"], 0)

    def test_cancel_during_hash_scan_terminates_thread_and_emits_single_final_callback(self) -> None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        problematic_dir = root / "problematic"
        originals_dir = root / "originals"
        destination_dir = root / "destination"
        problematic_dir.mkdir(parents=True, exist_ok=True)
        originals_dir.mkdir(parents=True, exist_ok=True)
        destination_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Non-audio prefix creates a meaningful scan window; cancellation is triggered exactly after scan start.
            big_payload = (b"X" * (2 * 1024 * 1024)) + _build_mp3(audio_payloads=(0x31, 0x32, 0x33))
            (problematic_dir / "Live.mp3").write_bytes(big_payload)
            (originals_dir / "Live.mp3").write_bytes(big_payload)

            done = threading.Event()
            scan_started = threading.Event()
            calls = {"completed": 0, "cancelled": 0, "errors": 0}
            logs: list[str] = []

            def _on_completed(_result):
                calls["completed"] += 1
                done.set()

            def _on_cancelled(_message: str):
                calls["cancelled"] += 1
                done.set()

            def _on_error(_message: str):
                calls["errors"] += 1
                done.set()

            worker = MP3RecoveryWorker(
                on_completed=_on_completed,
                on_cancelled=_on_cancelled,
                on_error=_on_error,
                on_log=logs.append,
            )

            def _on_log_cancel(msg: str) -> None:
                logs.append(msg)
                if "[HASH] Scanner MPEG avviato problematico=" in msg:
                    scan_started.set()

            worker.on_log = _on_log_cancel

            def _trigger_cancel_after_scan_start() -> None:
                scan_started.wait(timeout=0.3)
                worker.cancel()

            canceller = threading.Thread(target=_trigger_cancel_after_scan_start, daemon=True)
            canceller.start()

            worker.start(
                problematic_dir=str(problematic_dir),
                originals_dir=str(originals_dir),
                destination_dir=str(destination_dir),
            )

            self.assertTrue(done.wait(timeout=20))
            if worker._thread is not None:
                worker._thread.join(timeout=5)

            self.assertFalse(worker.is_running)
            self.assertIsNotNone(worker._thread)
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(calls["errors"], 0)
            self.assertEqual(calls["cancelled"], 0)
            self.assertEqual(calls["completed"], 1)
            self.assertTrue(any("Cancellato" in msg for msg in logs))

            tmp_files = list(destination_dir.rglob("*.tmp"))
            self.assertEqual(tmp_files, [])
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
