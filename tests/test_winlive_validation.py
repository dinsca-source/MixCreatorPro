# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_validation import parse_audio_hash_plan


def _synchsafe(size: int) -> bytes:
    return bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )


def _fake_mpeg_frame(length: int = 417) -> bytes:
    # Header: FF FB 90 64 -> MPEG1 Layer III, 128 kbps, 44100 Hz, no padding
    if length < 4:
        raise ValueError("length must be >= 4")
    return b"\xFF\xFB\x90\x64" + (b"\x00" * (length - 4))


class WinLiveValidationPlanTests(unittest.TestCase):
    def test_detects_id3v2_id3v1_and_mpeg_region(self) -> None:
        id3v2_payload = b"X" * 20
        id3v2 = b"ID3\x04\x00\x00" + _synchsafe(len(id3v2_payload)) + id3v2_payload
        audio = _fake_mpeg_frame() + _fake_mpeg_frame()
        id3v1 = b"TAG" + (b"Y" * 125)
        data = id3v2 + audio + id3v1

        plan = parse_audio_hash_plan(data)

        self.assertIsNotNone(plan.id3v2_region)
        self.assertIsNotNone(plan.id3v1_region)
        self.assertIsNotNone(plan.mpeg_frames_region)
        self.assertEqual(plan.id3v2_region.start, 0)
        self.assertEqual(plan.id3v1_region.end, len(data))

    def test_detects_ape_footer_region(self) -> None:
        audio = _fake_mpeg_frame()
        ape_body = b"Z" * 32
        # Minimal synthetic APE footer with declared size 64 bytes
        ape_footer = b"APETAGEX" + b"\xD0\x07\x00\x00" + (64).to_bytes(4, "little") + b"\x00" * 16
        data = audio + ape_body + ape_footer

        plan = parse_audio_hash_plan(data)

        self.assertIsNotNone(plan.ape_region)
        self.assertIsNotNone(plan.mpeg_frames_region)
        self.assertLessEqual(plan.mpeg_frames_region.end, plan.ape_region.start)

    def test_no_mpeg_frames_adds_note(self) -> None:
        data = b"ID3\x04\x00\x00" + _synchsafe(0) + b"NOT_AUDIO"
        plan = parse_audio_hash_plan(data)
        self.assertIsNone(plan.mpeg_frames_region)
        self.assertTrue(plan.notes)


if __name__ == "__main__":
    unittest.main()
