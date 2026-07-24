# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
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
                synct_text="|100|  CIAO|500|\n|300|MONDO |700|",
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
            self.assertIsNotNone(result.temporary_path)

            with open(source_path, "rb") as file_obj:
                self.assertEqual(file_obj.read(), original_bytes)

            with open(result.temporary_path, "rb") as file_obj:
                copy_bytes = file_obj.read()
            parsed = parse_winlive_blocks_strict(copy_bytes)
            self.assertEqual(len(parsed.synct.open_offsets), 1)
            self.assertEqual(len(parsed.chord.open_offsets), 1)
            self.assertNotIn(b"|100|  CIAO|500|", copy_bytes)

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
                file_obj.write(_build_valid_source(synct_text="|100| A |200|", chord_text="|100|C|200|"))

            success = write_normalized_winlive_copy(source_path, temp_dir)
            self.assertEqual(str(success.suggested_outcome), "WinLiveOutcome.FILE_NORMALIZED")
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

            self.assertEqual(str(failed.suggested_outcome), "WinLiveOutcome.NORMALIZATION_NOT_VALIDATED")


if __name__ == "__main__":
    unittest.main()
