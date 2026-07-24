# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_validation import AudioHashStatus, compute_mpeg_audio_hash


def _synchsafe(size: int) -> bytes:
    return bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )


def _sample_rate(version_bits: int, sample_index: int) -> int:
    table = {
        0x03: [44100, 48000, 32000],
        0x02: [22050, 24000, 16000],
        0x00: [11025, 12000, 8000],
    }
    return table[version_bits][sample_index]


def _bitrate(version_bits: int, bitrate_index: int) -> int:
    mpeg1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    mpeg2_l23 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    if version_bits == 0x03:
        return mpeg1_l3[bitrate_index]
    return mpeg2_l23[bitrate_index]


def _frame_length(version_bits: int, bitrate_kbps: int, sample_rate_hz: int, padding: int) -> int:
    if version_bits == 0x03:
        return int((144 * bitrate_kbps * 1000) // sample_rate_hz + padding)
    return int((72 * bitrate_kbps * 1000) // sample_rate_hz + padding)


def _make_frame(
    *,
    version_bits: int,
    bitrate_index: int,
    sample_index: int,
    padding: int = 0,
    crc: bool = False,
    payload_byte: int = 0,
    include_xing: bool = False,
) -> bytes:
    protection_bit = 0 if crc else 1
    b1 = 0xFF
    b2 = 0xE0 | (version_bits << 3) | (0x01 << 1) | protection_bit
    b3 = (bitrate_index << 4) | (sample_index << 2) | (padding << 1)
    b4 = 0x00

    bitrate_kbps = _bitrate(version_bits, bitrate_index)
    sample_rate_hz = _sample_rate(version_bits, sample_index)
    length = _frame_length(version_bits, bitrate_kbps, sample_rate_hz, padding)

    frame = bytearray([b1, b2, b3, b4])
    frame.extend(bytes([payload_byte]) * (length - 4))
    if include_xing and len(frame) > 16:
        frame[8:12] = b"Xing"
    return bytes(frame)


def _id3v2(payload: bytes) -> bytes:
    return b"ID3\x04\x00\x00" + _synchsafe(len(payload)) + payload


def _id3v1() -> bytes:
    return b"TAG" + (b"Y" * 125)


def _ape_footer(size: int = 64) -> bytes:
    return b"APETAGEX" + b"\xD0\x07\x00\x00" + size.to_bytes(4, "little") + b"\x00" * 16


class WinLiveAudioHashTests(unittest.TestCase):
    def test_cbr_mpeg1_layer3_stream(self) -> None:
        audio = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(4))
        result = compute_mpeg_audio_hash(audio)
        self.assertEqual(result.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(result.frames_count, 4)
        self.assertIsNotNone(result.audio_hash_sha256)

    def test_vbr_stream_with_xing(self) -> None:
        frames = [
            _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, include_xing=True),
            _make_frame(version_bits=0x03, bitrate_index=10, sample_index=0),
            _make_frame(version_bits=0x03, bitrate_index=11, sample_index=0),
        ]
        result = compute_mpeg_audio_hash(b"".join(frames))
        self.assertEqual(result.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(result.frames_count, 3)

    def test_mpeg2_layer3_and_mpeg25_layer3_supported(self) -> None:
        stream2 = b"".join(_make_frame(version_bits=0x02, bitrate_index=8, sample_index=0) for _ in range(3))
        stream25 = b"".join(_make_frame(version_bits=0x00, bitrate_index=8, sample_index=0) for _ in range(3))

        result2 = compute_mpeg_audio_hash(stream2)
        result25 = compute_mpeg_audio_hash(stream25)

        self.assertEqual(result2.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(result25.status, AudioHashStatus.VALID_AUDIO_STREAM)

    def test_padding_and_crc_are_handled(self) -> None:
        audio = b"".join(
            [
                _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, padding=0, crc=False),
                _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, padding=1, crc=True),
                _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, padding=0, crc=True),
            ]
        )
        result = compute_mpeg_audio_hash(audio)
        self.assertEqual(result.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(result.frames_count, 3)
        self.assertTrue(any(frame.has_crc for frame in result.frame_sequence))

    def test_id3_id3v1_ape_winlive_and_false_sync_in_metadata(self) -> None:
        fake_sync = b"\xFF\xFB\x90\x64"
        id3 = _id3v2(b"META" + fake_sync + b"NOISE")
        audio = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(3))
        winlive = b"<WL5SYNCT>|100|CIAO|200|/<WL5SYNCT><WL5CHORD>|100|C|200|/<WL5CHORD>"
        ape = b"Z" * 16 + _ape_footer()
        data = id3 + audio + winlive + ape + _id3v1()

        result = compute_mpeg_audio_hash(data)

        self.assertEqual(result.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(result.frames_count, 3)

    def test_truncated_frame_reports_partial(self) -> None:
        good = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(2))
        truncated = _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0)[:40]
        result = compute_mpeg_audio_hash(good + truncated)
        self.assertEqual(result.status, AudioHashStatus.PARTIAL_AUDIO_STREAM)
        self.assertTrue(any(item.startswith("TRUNCATED_FRAME_AT_") for item in result.anomalies))

    def test_no_frame_is_no_audio_stream(self) -> None:
        result = compute_mpeg_audio_hash(b"ID3\x04\x00\x00" + _synchsafe(0) + b"NOT_AUDIO")
        self.assertEqual(result.status, AudioHashStatus.NO_AUDIO_STREAM)

    def test_ambiguous_stream_detection(self) -> None:
        chain_a = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x11) for _ in range(3))
        chain_b = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x22) for _ in range(3))
        data = chain_a + (b"X" * 97) + chain_b
        result = compute_mpeg_audio_hash(data)
        self.assertEqual(result.status, AudioHashStatus.AMBIGUOUS_AUDIO_STREAM)

    def test_hash_identical_when_only_winlive_changes(self) -> None:
        audio = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(3))
        data_a = audio + b"<WL5SYNCT>|100|A|200|/<WL5SYNCT><WL5CHORD>|100|C|200|/<WL5CHORD>"
        data_b = audio + b"<WL5SYNCT>|100|AA|200|/<WL5SYNCT><WL5CHORD>|100|C|200|/<WL5CHORD>"

        hash_a = compute_mpeg_audio_hash(data_a)
        hash_b = compute_mpeg_audio_hash(data_b)

        self.assertEqual(hash_a.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(hash_b.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(hash_a.audio_hash_sha256, hash_b.audio_hash_sha256)

    def test_hash_identical_with_winlive_before_audio(self) -> None:
        audio = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(3))
        prefix_a = b"<WL5SYNCT>|10|TESTO|20|/<WL5SYNCT><WL5CHORD>|10|C?|20|/<WL5CHORD>"
        prefix_b = b"<WL5SYNCT>|10|TESTO MOD|20|/<WL5SYNCT><WL5CHORD>|10|Dm|20|/<WL5CHORD>"

        hash_a = compute_mpeg_audio_hash(prefix_a + audio)
        hash_b = compute_mpeg_audio_hash(prefix_b + audio)

        self.assertEqual(hash_a.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(hash_b.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(hash_a.audio_hash_sha256, hash_b.audio_hash_sha256)

    def test_hash_changes_if_one_audio_byte_changes(self) -> None:
        audio = bytearray(b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0) for _ in range(3)))
        altered = bytearray(audio)
        altered[100] ^= 0x01

        hash_original = compute_mpeg_audio_hash(bytes(audio))
        hash_altered = compute_mpeg_audio_hash(bytes(altered))

        self.assertEqual(hash_original.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertEqual(hash_altered.status, AudioHashStatus.VALID_AUDIO_STREAM)
        self.assertNotEqual(hash_original.audio_hash_sha256, hash_altered.audio_hash_sha256)


if __name__ == "__main__":
    unittest.main()
