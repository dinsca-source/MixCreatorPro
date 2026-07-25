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
    OUTPUT_FOLDER_INTEGRITY_ROOT,
    STATUS_PERFECT,
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
            phase_times_ms={},
            diagnostic_counters={},
        )

        with mock.patch("mp3_diagnostics.write_normalized_winlive_copy", return_value=failed_write):
            row = self._first_result(self._run(engine, verify_winlive=True, repair_mode=True))

        self.assertEqual(row.winlive.stato_winlive_finale, WinLiveOutcome.MODIFICATION_NOT_INTEGRAL)
        self.assertEqual(row.winlive.errore_winlive_code, WinLiveWriteErrorCode.METADATA_MISMATCH.value)
        self.assertEqual(row.winlive.esito_cartella_winlive, "Non integro dopo modifica")

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

    def test_verify_mp3_integrity_false_runs_winlive_only(self) -> None:
        self._write_input("wl_only.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _WinLiveScenarioEngine()
        with mock.patch.object(engine, "_analyze_mp3", side_effect=AssertionError("_analyze_mp3 should not run")):
            with mock.patch.object(engine, "_safe_duration_seconds", side_effect=AssertionError("_safe_duration_seconds should not run")):
                with mock.patch.object(
                    engine,
                    "_place_file_for_category",
                    side_effect=AssertionError("_place_file_for_category should not run"),
                ):
                    result = self._run(engine, verify_winlive=True, verify_mp3_integrity=False)
        row = self._first_result(result)
        self.assertTrue(row.winlive.verifica_winlive_eseguita)
        self.assertEqual(row.repair_outcome, "")

        session_output = Path(result["summary"]["output_folder"])
        self.assertFalse((session_output / OUTPUT_FOLDER_INTEGRITY_ROOT).exists())

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