# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mp3_recovery import (
    MP3RecoveryStatus,
    RecoveryMode,
    recover_mp3_from_original,
)
from winlive_tags import parse_winlive_blocks_strict
from winlive_validation import compute_mpeg_audio_hash


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


class MP3RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_dir = self.root / "original"
        self.problematic_dir = self.root / "problematic"
        self.output_dir = self.root / "output"
        self.original_dir.mkdir(parents=True, exist_ok=True)
        self.problematic_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, folder: Path, name: str, data: bytes) -> Path:
        path = folder / name
        path.write_bytes(data)
        return path

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_problematic_with_tags_original_without_tags(self) -> None:
        original_bytes = _build_mp3(tail=b"ORIGINAL_TAIL")
        problematic_bytes = _build_mp3(
            synct_text=b"|100|CIAO|200|",
            chord_text=b"|100|C|200|",
            tail=b"PROBLEM_TAIL",
        )
        original = self._write(self.original_dir, "Love Collection 1.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "Love Collection 1_corrupt.mp3", problematic_bytes)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS)
        self.assertTrue(result.tags_transferred)
        self.assertTrue(result.verification_ok)
        self.assertTrue(result.compatibility_ok)
        self.assertTrue(Path(result.output_path).is_file())
        self.assertEqual(self._sha256(original), result.original_sha256_before)
        self.assertEqual(self._sha256(original), result.original_sha256_after)
        self.assertEqual(self._sha256(original), self._sha256(original))

        recovered_bytes = Path(result.output_path).read_bytes()
        parsed = parse_winlive_blocks_strict(recovered_bytes)
        self.assertEqual(parsed.synct.content_bytes, b"|100|CIAO|200|")
        self.assertEqual(parsed.chord.content_bytes, b"|100|C|200|")
        self.assertEqual(self._sha256(original), self._sha256(original))
        self.assertEqual(self._sha256(original), hashlib.sha256(original.read_bytes()).hexdigest())
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_problematic_with_tags_original_with_different_tags(self) -> None:
        original_bytes = _build_mp3(
            synct_text=b"|10|VECCHIO|20|",
            chord_text=b"|10|Am|20|",
            tail=b"ORIGINAL_TAIL",
        )
        problematic_bytes = _build_mp3(
            synct_text=b"|100|NUOVO|200|",
            chord_text=b"|100|C|200|",
            tail=b"PROBLEM_TAIL",
        )
        original = self._write(self.original_dir, "track.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "track_corrupt.mp3", problematic_bytes)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS)
        recovered_bytes = Path(result.output_path).read_bytes()
        self.assertIn(b"|100|NUOVO|200|", recovered_bytes)
        self.assertIn(b"|100|C|200|", recovered_bytes)
        self.assertNotIn(b"|10|VECCHIO|20|", recovered_bytes)
        self.assertNotIn(b"|10|Am|20|", recovered_bytes)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_problematic_without_tags_original_with_tags(self) -> None:
        original_bytes = _build_mp3(
            synct_text=b"|100|ORIG|200|",
            chord_text=b"|100|C|200|",
            tail=b"ORIGINAL_TAIL",
        )
        problematic_bytes = _build_mp3(tail=b"PROBLEM_TAIL")
        original = self._write(self.original_dir, "with_tags.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "no_tags.mp3", problematic_bytes)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.UNCHANGED_ORIGINAL_COPY)
        self.assertFalse(result.tags_transferred)
        self.assertEqual(Path(result.output_path).read_bytes(), original_bytes)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_problematic_without_tags_original_without_tags(self) -> None:
        original_bytes = _build_mp3(tail=b"ORIGINAL_TAIL")
        problematic_bytes = _build_mp3(tail=b"PROBLEM_TAIL")
        original = self._write(self.original_dir, "plain.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "plain_bad.mp3", problematic_bytes)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.UNCHANGED_ORIGINAL_COPY)
        self.assertEqual(Path(result.output_path).read_bytes(), original_bytes)

    def test_non_compatible_files_fail_without_partial_output(self) -> None:
        original = self._write(self.original_dir, "original.mp3", _build_mp3())
        problematic = self._write(
            self.problematic_dir,
            "different.mp3",
            _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)),
        )

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.ORIGINAL_FILE_NOT_COMPATIBLE)
        self.assertIsNone(result.output_path)
        self.assertEqual(list(self.output_dir.iterdir()), [])
        self.assertEqual(original.read_bytes(), _build_mp3())
        self.assertEqual(problematic.read_bytes(), _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

    def test_write_error_removes_temporary_file(self) -> None:
        original = self._write(self.original_dir, "original.mp3", _build_mp3())
        problematic = self._write(
            self.problematic_dir,
            "problematic.mp3",
            _build_mp3(synct_text=b"|100|CIAO|200|", chord_text=b"|100|C|200|"),
        )

        with mock.patch("mp3_recovery._write_temp_candidate", side_effect=OSError("write failed")):
            result = recover_mp3_from_original(
                problematic_path=problematic,
                original_path=original,
                output_dir=self.output_dir,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.WRITE_ERROR)
        self.assertEqual(list(self.output_dir.iterdir()), [])
        self.assertEqual(original.read_bytes(), _build_mp3())

    def test_final_verification_failure_cleans_temp(self) -> None:
        original = self._write(self.original_dir, "original.mp3", _build_mp3())
        problematic = self._write(
            self.problematic_dir,
            "problematic.mp3",
            _build_mp3(synct_text=b"|100|CIAO|200|", chord_text=b"|100|C|200|"),
        )

        with mock.patch("mp3_recovery._verify_recovered_candidate", return_value=(False, ["forced failure"], None)):
            result = recover_mp3_from_original(
                problematic_path=problematic,
                original_path=original,
                output_dir=self.output_dir,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.FINAL_VERIFICATION_FAILED)
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_destination_exists_is_renamed_automatically(self) -> None:
        original = self._write(self.original_dir, "original.mp3", _build_mp3())
        problematic = self._write(
            self.problematic_dir,
            "problematic.mp3",
            _build_mp3(synct_text=b"|100|CIAO|200|", chord_text=b"|100|C|200|"),
        )
        existing = self._write(self.output_dir, "original.mp3", b"already here")

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.destination_renamed)
        self.assertTrue(Path(result.output_path).name.startswith("original_"))
        self.assertTrue(existing.is_file())

    def test_original_immutability_hash_size_and_mtime(self) -> None:
        original = self._write(self.original_dir, "original.mp3", _build_mp3())
        problematic = self._write(
            self.problematic_dir,
            "problematic.mp3",
            _build_mp3(synct_text=b"|100|CIAO|200|", chord_text=b"|100|C|200|"),
        )

        before_stat = original.stat()
        before_hash = self._sha256(original)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        after_stat = original.stat()
        after_hash = self._sha256(original)

        self.assertTrue(result.success)
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(int(before_stat.st_mtime), int(after_stat.st_mtime))

    def test_non_winlive_metadata_preserved_in_transfer_case(self) -> None:
        original_bytes = _build_mp3(prefix=b"PREFIX", synct_text=b"|10|OLD|20|", chord_text=b"|10|C|20|", tail=b"SUFFIX")
        problematic_bytes = _build_mp3(prefix=b"PREFIX", synct_text=b"|10|NEW|20|", chord_text=b"|10|C|20|", tail=b"SUFFIX")
        original = self._write(self.original_dir, "meta.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "meta_bad.mp3", problematic_bytes)

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
        )

        self.assertTrue(result.success)
        recovered_bytes = Path(result.output_path).read_bytes()
        self.assertTrue(recovered_bytes.startswith(b"PREFIX"))
        self.assertTrue(recovered_bytes.endswith(b"SUFFIX"))
        self.assertIn(b"|10|NEW|20|", recovered_bytes)
        self.assertNotIn(b"|10|OLD|20|", recovered_bytes)

    def test_forced_recovery_with_different_hashes_skips_comparison(self) -> None:
        original = self._write(self.original_dir, "forced.mp3", _build_mp3(tail=b"ORIGINAL_TAIL"))
        problematic = self._write(self.problematic_dir, "forced.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC), synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
            recovery_mode=RecoveryMode.FORCED,
        )

        self.assertTrue(result.success)
        self.assertTrue(result.forced_recovery)
        self.assertFalse(result.audio_comparison_executed)
        self.assertEqual(result.recovery_mode, RecoveryMode.FORCED)
        self.assertEqual(result.status, MP3RecoveryStatus.FORCED_COPY_WITH_REPLACED_WINLIVE_TAGS)
        self.assertTrue(Path(result.output_path).is_file())

    def test_forced_recovery_without_tags_returns_byte_identical_original_copy(self) -> None:
        original_bytes = _build_mp3(prefix=b"PRE", tail=b"TAIL")
        original = self._write(self.original_dir, "plain.mp3", original_bytes)
        problematic = self._write(self.problematic_dir, "plain.mp3", _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

        result = recover_mp3_from_original(
            problematic_path=problematic,
            original_path=original,
            output_dir=self.output_dir,
            recovery_mode=RecoveryMode.FORCED,
        )

        self.assertTrue(result.success)
        self.assertEqual(Path(result.output_path).read_bytes(), original_bytes)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(problematic.read_bytes(), _build_mp3(audio_payloads=(0xAA, 0xBB, 0xCC)))

    def test_final_verification_failure_in_forced_mode(self) -> None:
        original = self._write(self.original_dir, "verif.mp3", _build_mp3())
        problematic = self._write(self.problematic_dir, "verif.mp3", _build_mp3(synct_text=b"|100|NEW|200|", chord_text=b"|100|C|200|"))

        with mock.patch("mp3_recovery._verify_recovered_candidate", return_value=(False, ["forced failure"], None)):
            result = recover_mp3_from_original(
                problematic_path=problematic,
                original_path=original,
                output_dir=self.output_dir,
                recovery_mode=RecoveryMode.FORCED,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, MP3RecoveryStatus.FINAL_VERIFICATION_FAILED)
        self.assertEqual(list(self.output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()