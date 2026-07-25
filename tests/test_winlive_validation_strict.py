# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_validation import validate_normalized_winlive_file


def _frame(length: int = 417, payload_byte: int = 0x21) -> bytes:
    return b"\xFF\xFB\x90\x64" + bytes([payload_byte]) * (length - 4)


def _blob(synct: bytes, chord: bytes) -> bytes:
    audio = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33)
    return audio + b"<WL5SYNCT>" + synct + b"/<WL5SYNCT>" + b"<WL5CHORD>" + chord + b"/<WL5CHORD>"


class WinLiveStrictValidationTests(unittest.TestCase):
    def test_valid_file_passes(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _blob(b"0||200|CIAO|200||0||", b"|100|C|200|")
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertTrue(result.valid)
        self.assertEqual(result.reason_code, "OK")

    def test_meaningful_text_lost_fails(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _blob(b"0||100||200||0||", b"|100|C|200|")
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, "MEANINGFUL_TEXT_LOST")

    def test_missing_synct_after_fails(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33) + b"<WL5CHORD>|100|C|200|/<WL5CHORD>"
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, "SYNCT_MISSING_OR_INVALID_AFTER")

    def test_missing_terminator_fails(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _blob(b"0||200|CIAO|200|", b"|100|C|200|")
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, "SYNCT_TERMINATOR_MISSING")

    def test_chord_changed_fails(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _blob(b"0||200|CIAO|200||0||", b"|100|Dm|200|")
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, "CHORD_CHANGED")

    def test_initial_prefix_changed_fails(self) -> None:
        original = _blob(b"0||100|CIAO|200||0||", b"|100|C|200|")
        candidate = _blob(b"1||200|CIAO|200||0||", b"|100|C|200|")
        result = validate_normalized_winlive_file(original_data=original, candidate_data=candidate)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason_code, "SYNCT_INITIAL_VALUE_CHANGED")


if __name__ == "__main__":
    unittest.main()
