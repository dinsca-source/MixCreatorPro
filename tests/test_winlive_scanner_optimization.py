# -*- coding: utf-8 -*-

from __future__ import annotations

import threading
import unittest
from unittest import mock

import winlive_validation as wv

from winlive_validation import (
    AudioHashStatus,
    MpegFrame,
    MpegScanStats,
    _check_cancel,
    _is_chain_compatible,
    _scan_mpeg_frames,
    compute_mpeg_audio_hash,
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
    payload_byte: int = 0,
) -> bytes:
    b1 = 0xFF
    b2 = 0xE0 | (version_bits << 3) | (0x01 << 1) | 1
    b3 = (bitrate_index << 4) | (sample_index << 2) | (padding << 1)
    b4 = 0x00

    bitrate_kbps = _bitrate(version_bits, bitrate_index)
    sample_rate_hz = _sample_rate(version_bits, sample_index)
    length = _frame_length(version_bits, bitrate_kbps, sample_rate_hz, padding)

    frame = bytearray([b1, b2, b3, b4])
    frame.extend(bytes([payload_byte]) * (length - 4))
    return bytes(frame)


def _synchsafe(size: int) -> bytes:
    return bytes([
        (size >> 21) & 0x7F,
        (size >> 14) & 0x7F,
        (size >> 7) & 0x7F,
        size & 0x7F,
    ])


def _legacy_scan_mpeg_frames(
    data: bytes,
    start: int,
    end: int,
    *,
    cancel_event: object | None = None,
    debug_callback=None,
    source_label: str = "",
) -> tuple[list[list[MpegFrame]], list[str], MpegScanStats]:
    del debug_callback, source_label
    chains: dict[tuple[int, int, int], list[MpegFrame]] = {}
    anomalies: list[str] = []
    offset = max(0, start)
    limit = min(len(data), end)
    outer_iterations = 0
    inner_iterations_total = 0
    frames_found = 0
    frames_valid = 0
    frames_discarded = 0

    while offset + 4 <= limit:
        _check_cancel(cancel_event)
        outer_iterations += 1

        frame, parse_anomaly = wv._parse_frame_at(data, offset)
        if parse_anomaly is not None:
            anomalies.append(parse_anomaly)
            frames_discarded += 1
        if frame is None:
            offset += 1
            continue

        frames_found += 1
        if frame.length <= 0:
            anomalies.append(f"NON_PROGRESSIVE_FRAME_LEN_{offset}")
            frames_discarded += 1
            offset += 1
            continue

        chain = [frame]
        next_offset = frame.offset + frame.length
        if next_offset <= offset:
            anomalies.append(f"NON_PROGRESSIVE_NEXT_OFFSET_{offset}_{next_offset}")
            frames_discarded += 1
            offset += 1
            continue

        while next_offset + 4 <= limit:
            _check_cancel(cancel_event)
            inner_iterations_total += 1
            next_frame, next_anomaly = wv._parse_frame_at(data, next_offset)
            if next_anomaly is not None:
                anomalies.append(next_anomaly)
                frames_discarded += 1
            if next_frame is None:
                break
            if next_frame.length <= 0:
                anomalies.append(f"NON_PROGRESSIVE_CHAIN_FRAME_LEN_{next_offset}")
                frames_discarded += 1
                break
            if not _is_chain_compatible(chain[-1], next_frame):
                break
            chain.append(next_frame)
            candidate_next_offset = next_frame.offset + next_frame.length
            if candidate_next_offset <= next_offset:
                anomalies.append(f"NON_PROGRESSIVE_CHAIN_OFFSET_{next_offset}_{candidate_next_offset}")
                frames_discarded += 1
                break
            next_offset = candidate_next_offset

        key = (chain[0].offset, chain[-1].offset + chain[-1].length, len(chain))
        chains[key] = chain
        frames_valid += len(chain)

        new_offset = frame.offset + frame.length
        if new_offset <= offset:
            anomalies.append(f"OUTER_OFFSET_NOT_ADVANCING_{offset}_{new_offset}")
            frames_discarded += 1
            offset += 1
        else:
            offset = new_offset

    stats = MpegScanStats(
        parse_calls=0,
        unique_offsets=0,
        cache_hits=0,
        cache_misses=0,
        outer_iterations=outer_iterations,
        inner_iterations=inner_iterations_total,
        frames_found=frames_found,
        frames_valid=frames_valid,
        frames_rejected=frames_discarded,
        scanner_elapsed_seconds=0.0,
        average_speed_mb_s=0.0,
    )
    return list(chains.values()), anomalies, stats


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object], bool]] = []

    def __call__(self, _message: str) -> None:
        return

    def telemetry_event(self, event_type: str, payload: dict[str, object], *, critical: bool = False) -> None:
        self.events.append((event_type, dict(payload), critical))


class WinLiveScannerOptimizationTests(unittest.TestCase):
    def _compute_legacy(self, data: bytes, cancel_event: object | None = None):
        with mock.patch("winlive_validation._scan_mpeg_frames", side_effect=_legacy_scan_mpeg_frames):
            return compute_mpeg_audio_hash(data, cancel_event=cancel_event)

    def test_equivalence_on_reference_scenarios(self) -> None:
        base = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x34 + i) for i in range(4))
        scenarios = [
            base,
            (b"\x00" * 73) + base,
            base + _make_frame(version_bits=0x03, bitrate_index=9, sample_index=0)[:80],
            b"ID3\x04\x00\x00" + _synchsafe(12) + (b"META" * 3) + base,
            base + b"<WL5SYNCT>|100|A|200|/<WL5SYNCT><WL5CHORD>|100|C|200|/<WL5CHORD>",
            base + (b"Z" * 37),
            base + (b"X" * 91) + base,
            b"\x00" * 55 + base + b"<WL5CHORD>|40|Dm|60|/<WL5CHORD>" + (b"Q" * 20),
        ]

        for scenario in scenarios:
            new_result = compute_mpeg_audio_hash(scenario)
            old_result = self._compute_legacy(scenario)
            self.assertEqual(new_result.status, old_result.status)
            self.assertEqual(new_result.frames_count, old_result.frames_count)
            self.assertEqual(new_result.audio_bytes_hashed, old_result.audio_bytes_hashed)
            self.assertEqual(new_result.first_frame_offset, old_result.first_frame_offset)
            self.assertEqual(new_result.last_frame_end_offset, old_result.last_frame_end_offset)
            self.assertEqual(new_result.audio_hash_sha256, old_result.audio_hash_sha256)

    def test_equivalence_with_cancellation(self) -> None:
        data = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x20) for _ in range(200))
        event = threading.Event()
        event.set()

        new_result = compute_mpeg_audio_hash(data, cancel_event=event)
        old_result = self._compute_legacy(data, cancel_event=event)

        self.assertEqual(new_result.status, AudioHashStatus.CANCELLED)
        self.assertEqual(old_result.status, AudioHashStatus.CANCELLED)

    def test_parse_calls_are_near_linear_and_lower_than_legacy(self) -> None:
        data = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x44) for _ in range(900))

        with mock.patch("winlive_validation._scan_mpeg_frames", wraps=_scan_mpeg_frames):
            chains_new, _anomalies_new, stats_new = _scan_mpeg_frames(data, 0, len(data))

        legacy_parse_calls = {"count": 0}
        new_parse_calls = {"count": 0}

        original_parse = wv._parse_frame_at

        def _count_legacy(d: bytes, off: int):
            legacy_parse_calls["count"] += 1
            return original_parse(d, off)

        def _count_new(d: bytes, off: int):
            new_parse_calls["count"] += 1
            return original_parse(d, off)

        with mock.patch("winlive_validation._parse_frame_at", side_effect=_count_legacy):
            _legacy_scan_mpeg_frames(data, 0, len(data))

        with mock.patch("winlive_validation._parse_frame_at", side_effect=_count_new):
            _scan_mpeg_frames(data, 0, len(data))

        self.assertGreater(len(chains_new), 0)
        self.assertGreater(stats_new.frames_valid, 0)
        self.assertLessEqual(new_parse_calls["count"], stats_new.frames_valid * 4)
        self.assertLess(new_parse_calls["count"], legacy_parse_calls["count"])
        self.assertGreater(legacy_parse_calls["count"], new_parse_calls["count"] * 10)

    def test_scanner_end_telemetry_contains_performance_metrics(self) -> None:
        sink = _CollectingSink()
        data = b"".join(_make_frame(version_bits=0x03, bitrate_index=9, sample_index=0, payload_byte=0x41) for _ in range(800))

        compute_mpeg_audio_hash(data, debug_callback=sink, source_label="perf")

        end_events = [payload for event, payload, _ in sink.events if event == "MPEG_SCAN_END"]
        self.assertTrue(end_events)
        payload = end_events[-1]
        self.assertIn("parse_calls_total", payload)
        self.assertIn("unique_offsets_total", payload)
        self.assertIn("parse_cache_hits", payload)
        self.assertIn("parse_cache_misses", payload)
        self.assertGreaterEqual(int(payload.get("parse_calls_total") or 0), 1)


if __name__ == "__main__":
    unittest.main()
