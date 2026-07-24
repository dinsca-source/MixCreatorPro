# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest

from winlive_safe_write import WinLiveWriteErrorCode, cleanup_temporary_copy, decode_text_lossless, write_normalized_winlive_copy
from winlive_normalizer import normalize_synct_content
from winlive_tags import WinLiveStructureState, parse_winlive_blocks_strict
from winlive_validation import compute_mpeg_audio_hash


REAL_SAMPLE_PATH = r"C:\BASI Organizzate\Andante\24 mila baci (Adriano Celentano).mp3"


@unittest.skipUnless(os.path.exists(REAL_SAMPLE_PATH), "Real WinLive sample not available")
class WinLiveRealSampleE2ETests(unittest.TestCase):
    def test_real_sample_controlled_e2e_read_only(self) -> None:
        with open(REAL_SAMPLE_PATH, "rb") as source_file:
            original_bytes = source_file.read()
        original_sha_before = hashlib.sha256(original_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            # One temporary copy only, created outside source folder.
            copy_path = os.path.join(temp_dir, "real_sample_copy.mp3")
            shutil.copy2(REAL_SAMPLE_PATH, copy_path)
            self.assertTrue(os.path.exists(copy_path))

            with open(copy_path, "rb") as copied_file:
                copied_bytes = copied_file.read()

            parsed = parse_winlive_blocks_strict(copied_bytes)
            self.assertEqual(parsed.synct.state, WinLiveStructureState.VALID)
            self.assertEqual(parsed.chord.state, WinLiveStructureState.VALID)

            synct_text = None
            if parsed.synct.content_bytes is not None:
                decoded = decode_text_lossless(parsed.synct.content_bytes)
                synct_text = decoded.text
                if synct_text is not None:
                    _ = normalize_synct_content(synct_text)

            # Safe write is attempted only on the temporary copy and writes to temp_dir.
            write_result = write_normalized_winlive_copy(copy_path, temp_dir, keep_temporary_on_failure=True)

            if write_result.write_succeeded and write_result.readback_succeeded and write_result.temporary_path:
                self.assertTrue(os.path.exists(write_result.temporary_path))
                self.assertTrue(write_result.audio_identical)
                self.assertTrue(write_result.metadata_preserved)
                self.assertTrue(write_result.prefix_preserved)
                self.assertTrue(write_result.postfix_preserved)

                with open(write_result.temporary_path, "rb") as temp_written:
                    written_bytes = temp_written.read()

                original_hash = compute_mpeg_audio_hash(original_bytes)
                written_hash = compute_mpeg_audio_hash(written_bytes)
                self.assertEqual(original_hash.status, written_hash.status)
                self.assertEqual(original_hash.frames_count, written_hash.frames_count)
                self.assertEqual(original_hash.audio_bytes_hashed, written_hash.audio_bytes_hashed)
                self.assertEqual(original_hash.audio_hash_sha256, written_hash.audio_hash_sha256)

                self.assertTrue(cleanup_temporary_copy(write_result.temporary_path))
            else:
                # Real sample currently hits this branch when SYNCT normalization is not semantically valid.
                self.assertIn(
                    write_result.error_code,
                    {
                        WinLiveWriteErrorCode.NORMALIZATION_INVALID,
                        WinLiveWriteErrorCode.DECODING_FAILED,
                        WinLiveWriteErrorCode.NON_IDEMPOTENT_NORMALIZATION,
                    },
                )

            with open(REAL_SAMPLE_PATH, "rb") as source_file:
                original_after = source_file.read()
            original_sha_after = hashlib.sha256(original_after).hexdigest()
            self.assertEqual(original_sha_before, original_sha_after)


if __name__ == "__main__":
    unittest.main()
