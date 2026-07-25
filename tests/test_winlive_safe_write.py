# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

import winlive_safe_write as ws
from winlive_safe_write import (
    WinLiveWriteErrorCode,
    cleanup_temporary_copy,
    detect_text_encoding,
    encode_text_strict,
    write_normalized_winlive_copy,
)
from winlive_tags import parse_winlive_blocks_strict


def _synchsafe(size: int) -> bytes:
    return bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )


def _frame(length: int = 417, payload_byte: int = 0x41) -> bytes:
    return b"\xFF\xFB\x90\x64" + bytes([payload_byte]) * (length - 4)


def _build_valid_source(
    *,
    synct_text: str,
    chord_text: str,
    encoding: str = "utf-8",
    postfix: bytes | None = None,
) -> bytes:
    id3 = b"ID3\x04\x00\x00" + _synchsafe(8) + b"METADATA"
    audio = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33)
    synct = synct_text.encode(encoding)
    chord = chord_text.encode(encoding)
    tail = postfix if postfix is not None else b"\x00\x01" + (b"TAG" + b"Y" * 125)
    return id3 + audio + b"<WL5SYNCT>" + synct + b"/<WL5SYNCT>" + b"<WL5CHORD>" + chord + b"/<WL5CHORD>" + tail


class WinLiveSafeWriteTests(unittest.TestCase):
    def test_safe_write_success_and_original_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            original_bytes = _build_valid_source(
                synct_text="|100||150|CIAO|500|\n|300|MONDO|700|",
                chord_text="|100|C|500|",
            )
            with open(source_path, "wb") as file_obj:
                file_obj.write(original_bytes)

            result = write_normalized_winlive_copy(source_path, temp_dir)
            self.assertTrue(result.write_succeeded)
            self.assertTrue(result.readback_succeeded)
            self.assertTrue(result.winlive_structure_valid)
            self.assertTrue(result.text_matches_expected)
            self.assertTrue(result.chords_match_expected)
            self.assertTrue(result.normalization_idempotent)
            self.assertTrue(result.audio_identical)
            self.assertTrue(result.metadata_preserved)
            self.assertTrue(result.prefix_preserved)
            self.assertTrue(result.postfix_preserved)
            self.assertTrue(bool(result.rewrite_metrics.get("length_equation_ok", False)))
            self.assertTrue(bool(result.rewrite_metrics.get("chord_equal", False)))
            self.assertTrue(bool(result.rewrite_metrics.get("prefix_equal", False)))
            self.assertTrue(bool(result.rewrite_metrics.get("suffix_equal", False)))
            self.assertIsNotNone(result.temporary_path)

            with open(source_path, "rb") as file_obj:
                self.assertEqual(file_obj.read(), original_bytes)

            with open(result.temporary_path, "rb") as file_obj:
                copy_bytes = file_obj.read()
            parsed = parse_winlive_blocks_strict(copy_bytes)
            self.assertEqual(len(parsed.synct.open_offsets), 1)
            self.assertEqual(len(parsed.chord.open_offsets), 1)
            self.assertNotIn(b"|100||150|CIAO|500|", copy_bytes)

            removed = cleanup_temporary_copy(result.temporary_path)
            self.assertTrue(removed)
            self.assertFalse(os.path.exists(result.temporary_path))

    def test_cleanup_on_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|A|200|", chord_text="|100|C|200|"))

            def _fake_validate(**kwargs):
                return ws._result_error(
                    code=WinLiveWriteErrorCode.AUDIO_MISMATCH,
                    message="forced failure",
                    notes=[],
                    temporary_path=kwargs["temporary_path"],
                    write_succeeded=True,
                    readback_succeeded=True,
                    winlive_structure_valid=True,
                    text_matches_expected=True,
                    chords_match_expected=True,
                    normalization_idempotent=True,
                )

            with mock.patch.object(ws, "_validate_written_copy", side_effect=_fake_validate):
                result = write_normalized_winlive_copy(source_path, temp_dir, keep_temporary_on_failure=False)

            self.assertEqual(result.error_code, WinLiveWriteErrorCode.AUDIO_MISMATCH)
            self.assertIsNone(result.temporary_path)
            leftovers = [name for name in os.listdir(temp_dir) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_utf8_and_cp1252_encoding_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            utf8_source = os.path.join(temp_dir, "utf8.mp3")
            cp_source = os.path.join(temp_dir, "cp1252.mp3")

            with open(utf8_source, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|Citta\u00e0|200|", chord_text="|100|C|200|", encoding="utf-8"))

            with open(cp_source, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|Citt\xe0|200|", chord_text="|100|C|200|", encoding="cp1252"))

            utf8_result = write_normalized_winlive_copy(utf8_source, temp_dir)
            cp_result = write_normalized_winlive_copy(cp_source, temp_dir)

            self.assertEqual(utf8_result.encoding_used, "utf-8")
            self.assertEqual(cp_result.encoding_used, "cp1252")

            cleanup_temporary_copy(utf8_result.temporary_path)
            cleanup_temporary_copy(cp_result.temporary_path)

    def test_decoding_error_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "bad-encoding.mp3")
            prefix = b"ID3\x04\x00\x00" + _synchsafe(0)
            audio = _frame() + _frame() + _frame()
            synct = b"|100|\x81|200|"
            chord = b"|100|C|200|"
            content = prefix + audio + b"<WL5SYNCT>" + synct + b"/<WL5SYNCT>" + b"<WL5CHORD>" + chord + b"/<WL5CHORD>"
            with open(source_path, "wb") as file_obj:
                file_obj.write(content)

            result = write_normalized_winlive_copy(source_path, temp_dir)
            self.assertEqual(result.error_code, WinLiveWriteErrorCode.DECODING_FAILED)
            self.assertFalse(result.encoding_lossless)

    def test_non_representable_character_is_detected(self) -> None:
        encoded, error = encode_text_strict("CIAO🙂", "cp1252")
        self.assertIsNone(encoded)
        self.assertIsNotNone(error)

    def test_detect_text_encoding_direct_api(self) -> None:
        report_utf8 = detect_text_encoding("Città".encode("utf-8"))
        self.assertEqual(report_utf8.used_encoding, "utf-8")
        self.assertTrue(report_utf8.lossless)

        report_cp = detect_text_encoding("Città".encode("cp1252"))
        self.assertEqual(report_cp.used_encoding, "cp1252")
        self.assertTrue(report_cp.lossless)

    def test_invalid_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = write_normalized_winlive_copy(os.path.join(temp_dir, "missing.mp3"), temp_dir)
            self.assertEqual(result.error_code, WinLiveWriteErrorCode.READ_FAILED)

    def test_filesystem_error_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|A|200|", chord_text="|100|C|200|"))

            bad_output_dir = os.path.join(temp_dir, "does-not-exist")
            result = write_normalized_winlive_copy(source_path, bad_output_dir)
            self.assertEqual(result.error_code, WinLiveWriteErrorCode.WRITE_FAILED)

    def test_incomplete_write_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|A|200|", chord_text="|100|C|200|"))

            def _partial_writer(content: bytes, output_dir: str) -> str:
                path = os.path.join(output_dir, "partial.tmp")
                with open(path, "wb") as file_obj:
                    file_obj.write(content[: len(content) // 2])
                    file_obj.flush()
                    os.fsync(file_obj.fileno())
                return path

            with mock.patch.object(ws, "_write_temp_file", side_effect=_partial_writer):
                result = write_normalized_winlive_copy(source_path, temp_dir, keep_temporary_on_failure=False)

            self.assertEqual(result.error_code, WinLiveWriteErrorCode.READBACK_INVALID_STRUCTURE)
            leftovers = [name for name in os.listdir(temp_dir) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_duplicate_tag_in_copy_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|A|200|", chord_text="|100|C|200|"))

            original_writer = ws._write_temp_file

            def _writer_with_duplicate(content: bytes, output_dir: str) -> str:
                path = original_writer(content, output_dir)
                with open(path, "ab") as file_obj:
                    file_obj.write(b"<WL5CHORD>|1|X|2|/<WL5CHORD>")
                    file_obj.flush()
                    os.fsync(file_obj.fileno())
                return path

            with mock.patch.object(ws, "_write_temp_file", side_effect=_writer_with_duplicate):
                result = write_normalized_winlive_copy(source_path, temp_dir, keep_temporary_on_failure=False)

            self.assertEqual(result.error_code, WinLiveWriteErrorCode.READBACK_INVALID_STRUCTURE)

    def test_classification_ok_and_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100||200|A|300|", chord_text="|100|C|200|"))

            success = write_normalized_winlive_copy(source_path, temp_dir)
            self.assertIn(
                str(success.suggested_outcome),
                {"WinLiveOutcome.FILE_NORMALIZED", "WinLiveOutcome.FILE_ALREADY_OK"},
            )
            cleanup_temporary_copy(success.temporary_path)

            def _fake_failed_validate(**kwargs):
                return ws._result_error(
                    code=WinLiveWriteErrorCode.METADATA_MISMATCH,
                    message="forced metadata mismatch",
                    notes=[],
                    temporary_path=kwargs["temporary_path"],
                    write_succeeded=True,
                    readback_succeeded=True,
                    winlive_structure_valid=True,
                    text_matches_expected=True,
                    chords_match_expected=True,
                    normalization_idempotent=True,
                )

            with mock.patch.object(ws, "_validate_written_copy", side_effect=_fake_failed_validate):
                failed = write_normalized_winlive_copy(source_path, temp_dir, keep_temporary_on_failure=False)

            self.assertEqual(str(failed.suggested_outcome), "WinLiveOutcome.MODIFICATION_NOT_INTEGRAL")

    def test_sentinel_regions_and_chord_preserved_for_shorter_and_longer_synct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = b"PFX\x00\x01"
            sentinel = b"DATA_SENTINEL_ABC123"
            suffix = b"SFX\x00\x02\x03"

            audio = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33)
            chord = b"|100|C|200|"

            # Shorter rewrite: collapse adjacent chain and remove empty-timed line.
            synct_shorter = b"0|\n|100||150|CIAO|500|\n|1790|\n|500|MONDO|700|\n|0||"
            source_short = os.path.join(temp_dir, "shorter.mp3")
            with open(source_short, "wb") as file_obj:
                file_obj.write(
                    prefix
                    + audio
                    + b"<WL5SYNCT>"
                    + synct_shorter
                    + b"/<WL5SYNCT>"
                    + sentinel
                    + b"<WL5CHORD>"
                    + chord
                    + b"/<WL5CHORD>"
                    + suffix
                )

            short_result = write_normalized_winlive_copy(source_short, temp_dir)
            self.assertTrue(short_result.write_succeeded)
            self.assertTrue(short_result.readback_succeeded)
            self.assertTrue(short_result.prefix_preserved)
            self.assertTrue(short_result.postfix_preserved)
            self.assertTrue(short_result.metadata_preserved)
            self.assertTrue(short_result.rewrite_metrics.get("chord_equal", False))
            self.assertTrue(short_result.rewrite_metrics.get("prefix_equal", False))
            self.assertTrue(short_result.rewrite_metrics.get("suffix_equal", False))
            self.assertTrue(short_result.rewrite_metrics.get("length_equation_ok", False))
            self.assertLess(int(short_result.rewrite_metrics.get("new_synct_block_len", 0)), int(short_result.rewrite_metrics.get("original_synct_block_len", 0)))

            with open(short_result.temporary_path, "rb") as file_obj:
                short_copy = file_obj.read()
            self.assertIn(sentinel, short_copy)
            self.assertIn(b"<WL5CHORD>" + chord + b"/<WL5CHORD>", short_copy)
            cleanup_temporary_copy(short_result.temporary_path)

            # Longer rewrite: row-link alignment increases size due to expanded timestamp.
            synct_longer = b"0|\n|100|A|999|\n|12345|BBBB|14000|\n|0||"
            source_long = os.path.join(temp_dir, "longer.mp3")
            with open(source_long, "wb") as file_obj:
                file_obj.write(
                    prefix
                    + audio
                    + b"<WL5SYNCT>"
                    + synct_longer
                    + b"/<WL5SYNCT>"
                    + sentinel
                    + b"<WL5CHORD>"
                    + chord
                    + b"/<WL5CHORD>"
                    + suffix
                )

            long_result = write_normalized_winlive_copy(source_long, temp_dir)
            self.assertTrue(long_result.write_succeeded)
            self.assertTrue(long_result.readback_succeeded)
            self.assertTrue(long_result.prefix_preserved)
            self.assertTrue(long_result.postfix_preserved)
            self.assertTrue(long_result.metadata_preserved)
            self.assertTrue(long_result.rewrite_metrics.get("chord_equal", False))
            self.assertTrue(long_result.rewrite_metrics.get("prefix_equal", False))
            self.assertTrue(long_result.rewrite_metrics.get("suffix_equal", False))
            self.assertTrue(long_result.rewrite_metrics.get("length_equation_ok", False))
            self.assertGreater(int(long_result.rewrite_metrics.get("new_synct_block_len", 0)), int(long_result.rewrite_metrics.get("original_synct_block_len", 0)))

            with open(long_result.temporary_path, "rb") as file_obj:
                long_copy = file_obj.read()
            self.assertIn(sentinel, long_copy)
            self.assertIn(b"<WL5CHORD>" + chord + b"/<WL5CHORD>", long_copy)
            cleanup_temporary_copy(long_result.temporary_path)

    def test_safe_write_counters_and_retry_absence_on_normal_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "counters.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="0|\n|100||150|CIAO|500|\n|500|MONDO|700|\n|0||", chord_text="|100|C|200|"))

            result = write_normalized_winlive_copy(source_path, temp_dir)
            self.assertIsNone(result.error_code)
            self.assertGreater(result.phase_times_ms.get("safe_write_totale_ms", 0.0), 0.0)
            self.assertGreaterEqual(result.phase_times_ms.get("validazione_totale_ms", 0.0), 0.0)
            self.assertGreaterEqual(result.phase_times_ms.get("tempo_non_attribuito_ms", 0.0), 0.0)

            counters = result.diagnostic_counters
            self.assertEqual(counters.get("read_original", 0), 1)
            self.assertEqual(counters.get("parse_original", 0), 1)
            self.assertGreaterEqual(counters.get("normalize", 0), 1)
            self.assertEqual(counters.get("write_temp", 0), 1)
            self.assertEqual(counters.get("read_temp", 0), 1)
            self.assertLessEqual(counters.get("idempotence_normalize", 0), 2)
            self.assertEqual(counters.get("retry_write", 0), 0)
            self.assertEqual(counters.get("retry_read", 0), 0)

            cleanup_temporary_copy(result.temporary_path)

    def test_permission_error_on_temp_write_fails_fast_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "perm.mp3")
            with open(source_path, "wb") as file_obj:
                file_obj.write(_build_valid_source(synct_text="|100|A|200|", chord_text="|100|C|200|"))

            with mock.patch.object(ws.tempfile, "NamedTemporaryFile", side_effect=PermissionError("WinError 32 mocked")):
                result = write_normalized_winlive_copy(source_path, temp_dir)

            self.assertEqual(result.error_code, WinLiveWriteErrorCode.WRITE_FAILED)
            self.assertIn("PermissionError", " ".join(result.notes))
            self.assertEqual(result.diagnostic_counters.get("retry_write", 0), 0)

    def test_complexity_growth_is_bounded_across_row_scales(self) -> None:
        sizes = [10, 100, 1000, 5000]
        elapsed_ms: list[float] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            for count in sizes:
                lines = [f"|{i * 100}|LINE{i}|{i * 100 + 50}|" for i in range(1, count + 1)]
                synct = "\n".join(lines)
                payload = _build_valid_source(synct_text=synct, chord_text="|100|C|200|")
                source_path = os.path.join(temp_dir, f"scale_{count}.mp3")
                with open(source_path, "wb") as file_obj:
                    file_obj.write(payload)

                start = time.perf_counter()
                result = write_normalized_winlive_copy(source_path, temp_dir)
                elapsed = (time.perf_counter() - start) * 1000.0
                elapsed_ms.append(elapsed)

                self.assertIsNone(result.error_code)
                self.assertEqual(result.diagnostic_counters.get("parse_original", 0), 1)
                self.assertEqual(result.diagnostic_counters.get("write_temp", 0), 1)
                cleanup_temporary_copy(result.temporary_path)

        ratio_100_over_10 = elapsed_ms[1] / max(elapsed_ms[0], 0.001)
        ratio_1000_over_100 = elapsed_ms[2] / max(elapsed_ms[1], 0.001)
        ratio_5000_over_1000 = elapsed_ms[3] / max(elapsed_ms[2], 0.001)

        self.assertLess(ratio_100_over_10, 25.0)
        self.assertLess(ratio_1000_over_100, 15.0)
        self.assertLess(ratio_5000_over_1000, 10.0)

if __name__ == "__main__":
    unittest.main()
