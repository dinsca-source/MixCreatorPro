# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mp3_diagnostics import (
    AnalysisResult,
    AudioBounds,
    DiagnosticIssue,
    MP3DiagnosticsEngine,
    OUTPUT_FOLDER_INTEGRITY_WINLIVE,
    OUTPUT_FOLDER_INTEGRITY_ROOT,
    OUTPUT_FOLDER_PROCESSED_ORIGINALS,
    OUTPUT_FOLDER_TEMP,
    OUTPUT_FOLDER_WINLIVE,
    PRECISION_UNKNOWN,
    STATUS_PERFECT,
    EvaluatedIssue,
    IssuePosition,
    WinLiveDecodeAssessment,
)
from winlive_classification import WinLiveOutcome
from winlive_safe_write import WinLiveWriteErrorCode, WinLiveWriteValidationResult


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


class _WinLiveScenarioEngine(MP3DiagnosticsEngine):
    def __init__(self) -> None:
        super().__init__(ffmpeg=_FakeFFmpegManager())
        self.bounds = AudioBounds(
            file_duration_ms=30_000,
            significant_start_ms=0,
            significant_end_ms=30_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=-55.0,
            threshold_peak_db=-45.0,
        )

    def detect_significant_audio_bounds(self, file_path: Path) -> AudioBounds:
        _ = file_path
        return self.bounds

    def _analyze_significant_segment(self, *, file_path: Path, bounds: AudioBounds, cancel_event):
        _ = (file_path, bounds, cancel_event)
        return {
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

    def _analyze_mp3(self, *, file_path: Path, cancel_event, segment_ss, segment_t) -> AnalysisResult:
        _ = (file_path, cancel_event, segment_ss, segment_t)
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
        return AnalysisResult(
            command=["ffmpeg", "-i", str(file_path)],
            command_text=f"ffmpeg -i {file_path}",
            return_code=0,
            decode_log="deterministic",
            issues=[],
            metrics=metrics,
            integrity_index=100,
            total_errors=0,
        )


def _frame(length: int = 417, payload_byte: int = 0x11) -> bytes:
    return b"\xFF\xFB\x90\x64" + bytes([payload_byte]) * (length - 4)


def _mp3_blob(synct: bytes | None = None, chord: bytes | None = None, trailing: bytes = b"\x00") -> bytes:
    audio = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33)
    parts = [audio]
    if synct is not None:
        parts.append(b"<WL5SYNCT>" + synct + b"/<WL5SYNCT>")
    if chord is not None:
        parts.append(b"<WL5CHORD>" + chord + b"/<WL5CHORD>")
    parts.append(trailing)
    return b"".join(parts)


class MP3DiagnosticsWinLiveIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_input(self, name: str, data: bytes) -> Path:
        path = self.input_dir / name
        path.write_bytes(data)
        return path

    def _run(
        self,
        engine: MP3DiagnosticsEngine,
        *,
        verify_winlive: bool,
        repair_mode: bool = False,
        verify_mp3_integrity: bool = True,
    ) -> dict[str, object]:
        return engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=repair_mode,
            verify_mp3_integrity=verify_mp3_integrity,
            verify_winlive=verify_winlive,
        )

    def _first_result(self, result: dict[str, object]):
        return result["diagnostic_results"][0]

    @staticmethod
    def _assessment(
        *,
        path: str,
        rule: str,
        positive: bool,
        is_decodable: bool = True,
        rc: int = 0,
        stderr: str = "",
        duration: float = 30.0,
        timestamps: list[str] | None = None,
    ) -> WinLiveDecodeAssessment:
        return WinLiveDecodeAssessment(
            candidate_path=path,
            command_text=f"ffmpeg -v error -i {path} -f null -",
            return_code=rc,
            decode_log_excerpt=stderr,
            duration_seconds=duration,
            error_timestamps=list(timestamps or []),
            decision_rule=rule,
            has_positive_corruption=positive,
            is_decodable=is_decodable,
        )

    def test_verify_winlive_false_skips_engine_and_keeps_mp3_result(self) -> None:
        self._write_input("song.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch("mp3_diagnostics.parse_winlive_blocks_strict") as parse_mock:
            result = self._run(engine, verify_winlive=False)

        parse_mock.assert_not_called()
        row = self._first_result(result)
        self.assertEqual(row.repair_outcome, STATUS_PERFECT)
        self.assertFalse(row.winlive.verifica_winlive_eseguita)

    def test_valid_synct_and_chord_is_file_already_ok(self) -> None:
        self._write_input("ok.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.FILE_ALREADY_OK)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Conformi")
        self.assertTrue(Path(row.winlive.esito_percorso_winlive).is_file())

    def test_normalizable_synct_without_repair_requires_normalization(self) -> None:
        self._write_input("to_normalize.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=False))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.REQUIRES_NORMALIZATION)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Conformi")
        self.assertFalse(row.winlive.normalizzazione_tentata)

    def test_normalizable_synct_becomes_file_normalized_after_validation(self) -> None:
        self._write_input("normalize.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.FILE_NORMALIZED)
        self.assertTrue(row.winlive.normalizzazione_tentata)
        self.assertTrue(row.winlive.normalizzazione_validata)
        self.assertTrue(row.winlive.audio_hash_preservato)
        self.assertTrue(row.winlive.metadati_preservati)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Normalizzati")
        self.assertTrue(Path(row.winlive.esito_percorso_winlive).is_file())
        self.assertGreaterEqual(float(row.winlive.tempi_fasi_ms.get("costruzione_nuovi_bytes", 0.0)), 0.0)
        self.assertGreaterEqual(float(row.winlive.tempi_fasi_ms.get("scrittura_temporaneo", 0.0)), 0.0)
        self.assertGreaterEqual(float(row.winlive.tempi_fasi_ms.get("rilettura_temporaneo", 0.0)), 0.0)

    def test_question_mark_in_chord_is_unrecognized_chords(self) -> None:
        self._write_input("chord_q.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C?|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.UNRECOGNIZED_CHORDS)
        self.assertEqual(row.winlive.accordi_non_riconosciuti, 1)

    def test_missing_synct_is_missing_text_only(self) -> None:
        self._write_input("missing_synct.mp3", _mp3_blob(None, b"|100|C|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MISSING_TEXT_ONLY)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Senza TAG di Testo")

    def test_missing_chord_is_missing_chords_only(self) -> None:
        self._write_input("missing_chord.mp3", _mp3_blob(b"|100|CIAO|200|", None))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MISSING_CHORDS_ONLY)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Senza TAG Accordi")

    def test_missing_both_is_missing_text_and_chords(self) -> None:
        self._write_input("missing_both.mp3", _mp3_blob(None, None))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MISSING_TEXT_AND_CHORDS)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Senza TAG di Testo e di Accordi")

    def test_missing_synct_and_question_mark_is_missing_text_and_unrecognized(self) -> None:
        self._write_input("missing_synct_q.mp3", _mp3_blob(None, b"|100|C?|200|"))
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True))
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS)

    def test_ambiguous_structure_has_typed_error_and_original_unchanged(self) -> None:
        source = self._write_input(
            "ambiguous.mp3",
            _frame() + _frame() + _frame() + b"<WL5SYNCT>|1|A|2|<WL5SYNCT>|3|B|4|/<WL5SYNCT>",
        )
        before = source.read_bytes()
        row = self._first_result(self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=True))
        after = source.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.STRUCTURE_ERROR)
        self.assertTrue(row.winlive.errore_winlive_code in {"STRUCTURE_ERROR", "AMBIGUOUS_STRUCTURE"})
        self.assertFalse(row.winlive.normalizzazione_tentata)

    def test_validation_failure_is_non_integral_after_modification(self) -> None:
        self._write_input("validation_fail.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        failed_write = WinLiveWriteValidationResult(
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            text_matches_expected=True,
            chords_match_expected=True,
            normalization_idempotent=True,
            original_audio_hash=None,
            copy_audio_hash=None,
            audio_identical=False,
            metadata_preserved=False,
            prefix_preserved=False,
            postfix_preserved=False,
            error_code=WinLiveWriteErrorCode.METADATA_MISMATCH,
            error="forced failure",
            notes=["forced failure"],
            temporary_path=None,
            suggested_outcome=WinLiveOutcome.MODIFICATION_NOT_INTEGRAL,
            encoding_detected="utf-8",
            encoding_used="utf-8",
            encoding_converted=False,
            encoding_lossless=True,
            rewrite_metrics={},
            canonicalization_iterations=1,
            canonicalization_stabilized=True,
            canonicalization_cycle_detected=False,
            canonicalization_cycle_at_iteration=0,
            canonicalization_state_hashes=[],
            canonicalization_change_log=[],
            first_residual_diff={},
            phase_times_ms={},
            diagnostic_counters={},
        )

        with mock.patch("mp3_diagnostics.write_normalized_winlive_copy", return_value=failed_write):
            row = self._first_result(self._run(engine, verify_winlive=True, repair_mode=True))

        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MODIFICATION_NOT_INTEGRAL)
        self.assertEqual(row.winlive.errore_winlive_code, WinLiveWriteErrorCode.METADATA_MISMATCH.value)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Non integro dopo modifica")

    def test_corrupt_normalizable_wins_only_mp3_corrupted_folder(self) -> None:
        self._write_input("corrupt_normalizable.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_assess(path: Path) -> WinLiveDecodeAssessment:
            return self._assessment(
                path=str(path),
                rule="A_REAL_ERROR_IN_SIGNIFICANT_AUDIO",
                positive=True,
                is_decodable=False,
                rc=1,
                stderr="error while decoding",
                timestamps=["00:00:02.000"],
            )

        with mock.patch.object(engine, "_assess_minimal_decode_for_winlive", side_effect=fake_assess):
            row = self._first_result(self._run(engine, verify_winlive=True, repair_mode=True, verify_mp3_integrity=False))

        self.assertEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")
        self.assertEqual(Path(row.winlive.esito_percorso_winlive).name, "corrupt_normalizable.mp3")

    def test_corrupt_unrecognized_wins_only_mp3_corrupted_folder(self) -> None:
        self._write_input("corrupt_unrecognized.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C?|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_assess(path: Path) -> WinLiveDecodeAssessment:
            return self._assessment(
                path=str(path),
                rule="B_DIFFUSE_OR_REPEATED_STREAM_ERRORS",
                positive=True,
                is_decodable=False,
                rc=1,
                stderr="multiple decode errors",
                timestamps=["00:00:03.000", "00:00:04.000"],
            )

        with mock.patch.object(engine, "_assess_minimal_decode_for_winlive", side_effect=fake_assess):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False))

        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.UNRECOGNIZED_CHORDS)
        self.assertEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")

    def test_winlive_only_valid_tags_and_trailing_or_terminal_warnings_do_not_route_corrupted(self) -> None:
        self._write_input("valid_synct.mp3", _mp3_blob(b"|100|CIAO|200|", None))
        self._write_input("valid_chord.mp3", _mp3_blob(None, b"|100|C|200|"))
        self._write_input("valid_both.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        self._write_input("valid_trailing.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|", trailing=b"<WL5TAIL>"))
        self._write_input("valid_tail_warning.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_assess(path: Path) -> WinLiveDecodeAssessment:
            name = path.name
            if name == "valid_trailing.mp3":
                return self._assessment(
                    path=str(path),
                    rule="D_TRAILING_DATA_OR_WINLIVE_TAG_AFTER_AUDIO",
                    positive=False,
                    is_decodable=True,
                    rc=1,
                    stderr="trailing data after audio stream",
                )
            if name == "valid_tail_warning.mp3":
                return self._assessment(
                    path=str(path),
                    rule="C_TERMINAL_OR_NON_BLOCKING_WARNING",
                    positive=False,
                    is_decodable=True,
                    rc=1,
                    stderr="non monotonic dts warning",
                )
            return self._assessment(
                path=str(path),
                rule="PASS_NO_POSITIVE_CORRUPTION_EVIDENCE",
                positive=False,
                is_decodable=True,
                rc=0,
            )

        with mock.patch.object(engine, "_assess_minimal_decode_for_winlive", side_effect=fake_assess):
            result = self._run(engine, verify_winlive=True, verify_mp3_integrity=False)

        by_name = {row.file_name: row for row in result["diagnostic_results"]}
        self.assertEqual(by_name["valid_synct.mp3"].winlive.esito_cartella_winlive, "Senza TAG Accordi")
        self.assertEqual(by_name["valid_chord.mp3"].winlive.esito_cartella_winlive, "Senza TAG di Testo")
        self.assertEqual(by_name["valid_both.mp3"].winlive.esito_cartella_winlive, "Conformi")
        self.assertNotEqual(by_name["valid_trailing.mp3"].winlive.esito_cartella_winlive, "MP3 corrotti")
        self.assertNotEqual(by_name["valid_tail_warning.mp3"].winlive.esito_cartella_winlive, "MP3 corrotti")

    def test_wl_tail_1_normalization_tail_warning_is_confirmed(self) -> None:
        self._write_input("wl_tail_norm.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_assess(path: Path) -> WinLiveDecodeAssessment:
            return self._assessment(
                path=str(path),
                rule="C_TERMINAL_OR_NON_BLOCKING_WARNING",
                positive=False,
                is_decodable=True,
                rc=1,
                stderr="tail warning",
                timestamps=["00:00:29.800"],
            )

        with mock.patch.object(engine, "_assess_minimal_decode_for_winlive", side_effect=fake_assess):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False, repair_mode=True))

        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.FILE_NORMALIZED)
        self.assertNotEqual(row.winlive.esito_cartella_winlive, "Non integro dopo modifica")
        self.assertNotEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")

    def test_wl_tail_2_boundary_crossing_remains_blocking(self) -> None:
        self._write_input("wl_tail_cross.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_assess(path: Path) -> WinLiveDecodeAssessment:
            return self._assessment(
                path=str(path),
                rule="A_REAL_ERROR_IN_SIGNIFICANT_AUDIO",
                positive=True,
                is_decodable=False,
                rc=1,
                stderr="boundary-crossing error",
                timestamps=["00:00:28.999"],
            )

        with mock.patch.object(engine, "_assess_minimal_decode_for_winlive", side_effect=fake_assess):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False, repair_mode=True))

        self.assertEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")

    def test_wl_tail_3_pre_modify_tail_warning_allows_processing(self) -> None:
        self._write_input("wl_tail_pre.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(
            engine,
            "_assess_minimal_decode_for_winlive",
            return_value=self._assessment(
                path="wl_tail_pre.mp3",
                rule="C_TERMINAL_OR_NON_BLOCKING_WARNING",
                positive=False,
                is_decodable=True,
                rc=1,
                stderr="tail warning",
                timestamps=["00:00:29.900"],
            ),
        ):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False, repair_mode=False))

        self.assertNotEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")

    def test_wl_tail_4_post_modify_tail_warning_keeps_output_confirmed(self) -> None:
        self._write_input("wl_tail_post.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(
            engine,
            "_assess_minimal_decode_for_winlive",
            return_value=self._assessment(
                path="wl_tail_post.mp3",
                rule="C_TERMINAL_OR_NON_BLOCKING_WARNING",
                positive=False,
                is_decodable=True,
                rc=1,
                stderr="tail warning",
                timestamps=["00:00:29.700"],
            ),
        ):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False, repair_mode=True))

        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.FILE_NORMALIZED)
        self.assertNotEqual(row.winlive.esito_cartella_winlive, "Non integro dopo modifica")

    def test_winlive_only_real_significant_corruption_is_routed_to_mp3_corrotti(self) -> None:
        self._write_input("real_corrupt.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(
            engine,
            "_assess_minimal_decode_for_winlive",
            return_value=self._assessment(
                path="real_corrupt.mp3",
                rule="A_REAL_ERROR_IN_SIGNIFICANT_AUDIO",
                positive=True,
                is_decodable=False,
                rc=1,
                stderr="error while decoding frame",
                timestamps=["00:00:01.120"],
            ),
        ):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False))

        self.assertEqual(row.winlive.esito_cartella_winlive, "MP3 corrotti")
        self.assertIn("A_REAL_ERROR_IN_SIGNIFICANT_AUDIO", row.winlive.esito_operazione_winlive + " " + row.output_routing_reason)

    def test_winlive_only_without_tags_keeps_winlive_category(self) -> None:
        self._write_input("no_tags.mp3", _mp3_blob(None, None))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(
            engine,
            "_assess_minimal_decode_for_winlive",
            return_value=self._assessment(
                path="no_tags.mp3",
                rule="PASS_NO_POSITIVE_CORRUPTION_EVIDENCE",
                positive=False,
                is_decodable=True,
                rc=0,
            ),
        ):
            row = self._first_result(self._run(engine, verify_winlive=True, verify_mp3_integrity=False))

        self.assertEqual(row.winlive.esito_cartella_winlive, "Senza TAG di Testo e di Accordi")

    def test_normalized_temp_candidate_keeps_original_filename_and_no_temp_names_escape(self) -> None:
        self._write_input("temp_name.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        def fake_write_normalized_winlive_copy(**kwargs):
            temp_dir = Path(str(kwargs["temp_dir"]))
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / "temp_name_stream.mp3"
            temp_path.write_bytes(_mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
            return WinLiveWriteValidationResult(
                write_succeeded=True,
                readback_succeeded=True,
                winlive_structure_valid=True,
                text_matches_expected=True,
                chords_match_expected=True,
                normalization_idempotent=True,
                original_audio_hash="hash-a",
                copy_audio_hash="hash-a",
                audio_identical=True,
                metadata_preserved=True,
                prefix_preserved=True,
                postfix_preserved=True,
                error_code=None,
                error="",
                notes=["forced temp candidate"],
                temporary_path=str(temp_path),
                suggested_outcome=WinLiveOutcome.FILE_NORMALIZED,
                encoding_detected="utf-8",
                encoding_used="utf-8",
                encoding_converted=False,
                encoding_lossless=True,
                rewrite_metrics={},
                canonicalization_iterations=1,
                canonicalization_stabilized=True,
                canonicalization_cycle_detected=False,
                canonicalization_cycle_at_iteration=0,
                canonicalization_state_hashes=[],
                canonicalization_change_log=[],
                first_residual_diff={},
                phase_times_ms={},
                diagnostic_counters={},
            )

        with mock.patch("mp3_diagnostics.write_normalized_winlive_copy", side_effect=fake_write_normalized_winlive_copy):
            row = self._first_result(self._run(engine, verify_winlive=True, repair_mode=True, verify_mp3_integrity=False))

        session_output = Path(row.winlive.esito_percorso_winlive).parents[2]
        winlive_root = session_output / OUTPUT_FOLDER_WINLIVE
        self.assertEqual(Path(row.winlive.esito_percorso_winlive).name, "temp_name.mp3")
        self.assertEqual(len(list(winlive_root.rglob("temp_name.mp3"))), 1)
        self.assertEqual(len(list(winlive_root.rglob("*_stream.mp3"))), 0)
        self.assertEqual(len(list(self.input_dir.rglob("*_stream.mp3"))), 0)

    def test_winlive_only_outputs_one_file_per_input(self) -> None:
        self._write_input("one_a.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        self._write_input("one_b.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=True, verify_mp3_integrity=False)

        winlive_root = Path(result["summary"]["output_folder"]) / OUTPUT_FOLDER_WINLIVE
        self.assertEqual(len(list(winlive_root.rglob("one_a.mp3"))), 1)
        self.assertEqual(len(list(winlive_root.rglob("one_b.mp3"))), 1)

    def test_infrastructure_failure_routes_to_read_write_errors(self) -> None:
        self._write_input("infra_fail.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        failed_write = WinLiveWriteValidationResult(
            write_succeeded=False,
            readback_succeeded=False,
            winlive_structure_valid=True,
            text_matches_expected=False,
            chords_match_expected=False,
            normalization_idempotent=False,
            original_audio_hash=None,
            copy_audio_hash=None,
            audio_identical=False,
            metadata_preserved=False,
            prefix_preserved=False,
            postfix_preserved=False,
            error_code=WinLiveWriteErrorCode.WRITE_FAILED,
            error="forced infrastructure failure",
            notes=["forced infrastructure failure"],
            temporary_path=None,
            suggested_outcome=WinLiveOutcome.MODIFICATION_NOT_INTEGRAL,
            encoding_detected="utf-8",
            encoding_used="utf-8",
            encoding_converted=False,
            encoding_lossless=True,
            rewrite_metrics={},
            canonicalization_iterations=1,
            canonicalization_stabilized=True,
            canonicalization_cycle_detected=False,
            canonicalization_cycle_at_iteration=0,
            canonicalization_state_hashes=[],
            canonicalization_change_log=[],
            first_residual_diff={},
            phase_times_ms={},
            diagnostic_counters={},
        )

        with mock.patch("mp3_diagnostics.write_normalized_winlive_copy", return_value=failed_write):
            row = self._first_result(self._run(engine, verify_winlive=True, repair_mode=True))

        self.assertEqual(row.winlive.certificazione_finale_mp3, "Errore infrastrutturale")
        self.assertEqual(row.winlive.esito_cartella_winlive, "Errori lettura-scrittura")

    def test_unknown_timestamp_issue_is_not_definitively_nonrecoverable(self) -> None:
        self._write_input("unknown_issue.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        unknown_issue = DiagnosticIssue(
            problem_key="undecodable_frames",
            problem_type="Frame MP3 non decodificabile",
            start="Tempo non determinabile",
            end="",
            precision=PRECISION_UNKNOWN,
            detail="Header missing / Invalid data found when processing input",
        )
        analysis = AnalysisResult(
            command=["ffmpeg", "-i", "unknown_issue.mp3"],
            command_text="ffmpeg -i unknown_issue.mp3",
            return_code=0,
            decode_log="Header missing\nInvalid data found when processing input",
            issues=[unknown_issue],
            metrics={
                "header_missing": 1,
                "corrupted_frames": 0,
                "crc_errors": 0,
                "sync_errors": 0,
                "undecodable_frames": 0,
                "invalid_data": 1,
                "xing_issues": 0,
                "vbr_issues": 0,
                "id3_issues": 0,
            },
            integrity_index=0,
            total_errors=2,
        )
        evaluated = [
            EvaluatedIssue(
                issue=unknown_issue,
                position=IssuePosition.UNKNOWN,
                ignored_for_classification=False,
                exclusion_reason="",
                zone_label="Zona non determinabile",
                rms_dbfs=-80.0,
                peak_dbfs=-70.0,
                impact_label="Nessun impatto verificabile",
                segment_start_ms=None,
                segment_end_ms=None,
            )
        ]

        with mock.patch.object(engine, "_analyze_mp3", return_value=analysis):
            with mock.patch.object(engine, "_evaluate_issues", return_value=evaluated):
                with mock.patch.object(engine, "_analyze_significant_segment", return_value={
                    "header_missing": 0,
                    "corrupted_frames": 0,
                    "crc_errors": 0,
                    "sync_errors": 0,
                    "undecodable_frames": 0,
                    "invalid_data": 0,
                    "xing_issues": 0,
                    "vbr_issues": 0,
                    "id3_issues": 0,
                }):
                    cert = engine._certify_mp3_candidate(
                        file_path=self.input_dir / "unknown_issue.mp3",
                        repair_mode=True,
                        cancel_event=None,
                    )

        self.assertEqual(cert["final_outcome"], "Classificazione non determinabile")
        self.assertIn("Warning FFmpeg rilevati", cert["classification_reason"])
        self.assertEqual(cert["certificazione_finale_mp3"], "Non certificato")

    def test_winlive_exception_does_not_interrupt_mp3_diagnostics(self) -> None:
        self._write_input("boom.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        with mock.patch("mp3_diagnostics.parse_winlive_blocks_strict", side_effect=RuntimeError("boom")):
            row = self._first_result(self._run(engine, verify_winlive=True))
        self.assertEqual(row.repair_outcome, STATUS_PERFECT)
        self.assertIn("RuntimeError", row.winlive.errore_winlive)
        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.STRUCTURE_ERROR)

    def test_old_api_without_verify_winlive_still_works(self) -> None:
        self._write_input("compat.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        result = engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=False,
        )
        row = self._first_result(result)
        self.assertEqual(row.repair_outcome, STATUS_PERFECT)
        self.assertFalse(row.winlive.verifica_winlive_eseguita)

    def test_unified_certification_called_once_on_original_when_not_normalized(self) -> None:
        self._write_input("single_cert_original.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(engine, "_certify_mp3_candidate", wraps=engine._certify_mp3_candidate) as cert_spy:
            result = self._run(engine, verify_winlive=True, repair_mode=False)

        self.assertEqual(cert_spy.call_count, 1)
        called_path = cert_spy.call_args.kwargs["file_path"]
        called_temp_dir = cert_spy.call_args.kwargs.get("temp_dir")
        self.assertEqual(Path(called_path).name, "single_cert_original.mp3")
        self.assertIsNotNone(called_temp_dir)
        self.assertIn(OUTPUT_FOLDER_TEMP, str(called_temp_dir))
        self.assertEqual(Path(called_temp_dir).parent, Path(result["summary"]["output_folder"]))

    def test_unified_certification_called_once_on_normalized_when_validated(self) -> None:
        self._write_input("single_cert_normalized.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()

        with mock.patch.object(engine, "_certify_mp3_candidate", wraps=engine._certify_mp3_candidate) as cert_spy:
            result = self._run(engine, verify_winlive=True, repair_mode=True)

        self.assertEqual(cert_spy.call_count, 1)
        called_path = Path(cert_spy.call_args.kwargs["file_path"])
        called_temp_dir = cert_spy.call_args.kwargs.get("temp_dir")
        self.assertNotEqual(called_path.name, "single_cert_normalized.mp3")
        self.assertEqual(called_path.suffix.lower(), ".tmp")
        self.assertIsNotNone(called_temp_dir)
        self.assertIn(OUTPUT_FOLDER_TEMP, str(called_temp_dir))
        self.assertEqual(Path(called_temp_dir).parent, Path(result["summary"]["output_folder"]))

    def test_verify_mp3_integrity_false_runs_winlive_only(self) -> None:
        self._write_input("wl_only.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        with mock.patch.object(engine, "_certify_mp3_candidate", side_effect=AssertionError("_certify_mp3_candidate should not run")):
            result = self._run(engine, verify_winlive=True, verify_mp3_integrity=False)
        row = self._first_result(result)
        self.assertTrue(row.winlive.verifica_winlive_eseguita)
        self.assertEqual(row.repair_outcome, "")

        session_output = Path(result["summary"]["output_folder"])
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).exists())

    def test_output_mode_integrity_only_creates_single_main_folder(self) -> None:
        self._write_input("integrity_only.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=False, verify_mp3_integrity=True)

        session_output = Path(result["summary"]["output_folder"])
        self.assertTrue((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).exists())
        self.assertFalse((session_output / OUTPUT_FOLDER_WINLIVE).exists())
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_WINLIVE).exists())

        all_hits = list((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).rglob("integrity_only.mp3"))
        self.assertEqual(len(all_hits), 1)

    def test_output_mode_winlive_only_creates_single_main_folder(self) -> None:
        self._write_input("winlive_only_mode.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, verify_mp3_integrity=False)

        session_output = Path(result["summary"]["output_folder"])
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).exists())
        self.assertTrue((session_output / OUTPUT_FOLDER_WINLIVE).exists())
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_WINLIVE).exists())

        all_hits = list((session_output / OUTPUT_FOLDER_WINLIVE).rglob("winlive_only_mode.mp3"))
        self.assertEqual(len(all_hits), 1)

    def test_empty_winlive_output_directories_are_removed_after_run(self) -> None:
        self._write_input("cleanup_dirs.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, verify_mp3_integrity=False)

        session_output = Path(result["summary"]["output_folder"])
        winlive_root = session_output / OUTPUT_FOLDER_WINLIVE
        self.assertTrue((winlive_root / "Conformi").exists())
        self.assertFalse((winlive_root / "Normalizzati").exists())
        self.assertFalse((winlive_root / "MP3 corrotti").exists())
        self.assertFalse((winlive_root / "Errori struttura").exists())

    def test_empty_originals_folder_is_removed_when_unused(self) -> None:
        self._write_input("integrity_ok.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=False, verify_mp3_integrity=True)

        session_output = Path(result["summary"]["output_folder"])
        self.assertFalse((session_output / OUTPUT_FOLDER_PROCESSED_ORIGINALS).exists())

    def test_output_mode_combined_creates_single_main_folder_and_single_copy(self) -> None:
        self._write_input("combo_a.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        self._write_input("combo_b.mp3", _mp3_blob(None, b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, verify_mp3_integrity=True, repair_mode=True)

        session_output = Path(result["summary"]["output_folder"])
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).exists())
        self.assertFalse((session_output / OUTPUT_FOLDER_WINLIVE).exists())
        combined_root = session_output / OUTPUT_FOLDER_INTEGRITY_WINLIVE
        self.assertTrue(combined_root.exists())

        for row in result["diagnostic_results"]:
            self.assertEqual(row.placed_file_path, row.winlive.esito_percorso_winlive)

        hits_a = list(combined_root.rglob("combo_a.mp3"))
        hits_b = list(combined_root.rglob("combo_b.mp3"))
        self.assertEqual(len(hits_a), 1)
        self.assertEqual(len(hits_b), 1)

    def test_both_flags_false_are_rejected(self) -> None:
        self._write_input("invalid.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        with self.assertRaisesRegex(RuntimeError, "Almeno un controllo diagnostico deve essere attivo"):
            engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                verify_mp3_integrity=False,
                verify_winlive=False,
            )

    def test_each_file_is_copied_in_single_winlive_folder(self) -> None:
        self._write_input("one.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=False, verify_mp3_integrity=False)
        row = self._first_result(result)
        self.assertTrue(row.winlive.esito_cartella_winlive)

        winlive_root = Path(result["summary"]["output_folder"]) / "Esito WinLive"
        hits = list(winlive_root.rglob("one.mp3"))
        self.assertEqual(len(hits), 1)

    def test_removed_generic_categories_not_created(self) -> None:
        self._write_input("x.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        result = self._run(_WinLiveScenarioEngine(), verify_winlive=True, repair_mode=False, verify_mp3_integrity=False)
        winlive_root = Path(result["summary"]["output_folder"]) / "Esito WinLive"
        self.assertFalse((winlive_root / "Senza TAG").exists())
        self.assertFalse((winlive_root / "Normalizzabile").exists())
        self.assertFalse((winlive_root / "Non correggibile").exists())


if __name__ == "__main__":
    unittest.main()