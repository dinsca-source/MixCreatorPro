# -*- coding: utf-8 -*-

from __future__ import annotations

import array
import os
import tempfile
import unittest
from pathlib import Path

from waveform_analyzer import (
    _finalize_minmax,
    _reduce_chunk_into_buckets,
    analysis_params,
    get_or_build_waveform,
    normalize_bucket_count,
)
from waveform_cache import save_cached_waveform
from waveform_widget import is_waveform_result_obsolete


class WaveformAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_localappdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.temp.name

    def tearDown(self) -> None:
        if self.old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.old_localappdata
        self.temp.cleanup()

    def test_bucket_limits(self) -> None:
        self.assertEqual(normalize_bucket_count(10_000, 100), 1200)
        self.assertEqual(normalize_bucket_count(10_000, 50_000), 10000)

    def test_minmax_normalization_range(self) -> None:
        mins = [1.0, 1.0]
        maxs = [-1.0, -1.0]
        touched = [False, False]
        samples = array.array("h", [-32768, -2000, 0, 2000, 32767])

        _reduce_chunk_into_buckets(samples, 0, 5, mins, maxs, touched)
        peaks = _finalize_minmax(mins, maxs, touched)

        for low, high in peaks:
            self.assertGreaterEqual(low, -1.0)
            self.assertLessEqual(high, 1.0)

    def test_file_missing_returns_error(self) -> None:
        result = get_or_build_waveform(Path(self.temp.name) / "none.mp3", duration_ms=10_000)
        self.assertFalse(result["ok"])

    def test_cache_hit_path(self) -> None:
        mp3 = Path(self.temp.name) / "song.mp3"
        mp3.write_bytes(b"abc")

        params = analysis_params(duration_ms=10_000)
        levels = {1: [(-0.1, 0.1)], 2: [(-0.2, 0.2)]}
        save_cached_waveform(mp3, params, 10_000, levels)

        result = get_or_build_waveform(mp3, duration_ms=10_000)
        self.assertTrue(result["ok"])
        self.assertTrue(result["cache_hit"])

    def test_obsolete_result_guard(self) -> None:
        file_a = Path(self.temp.name) / "a.mp3"
        file_b = Path(self.temp.name) / "b.mp3"
        file_a.write_bytes(b"a")
        file_b.write_bytes(b"b")

        self.assertTrue(
            is_waveform_result_obsolete(
                request_id=1,
                active_request_id=2,
                current_file=file_a,
                result_file=file_a,
                cancelled=False,
                widget_exists=True,
            )
        )

        self.assertTrue(
            is_waveform_result_obsolete(
                request_id=2,
                active_request_id=2,
                current_file=file_a,
                result_file=file_b,
                cancelled=False,
                widget_exists=True,
            )
        )

        self.assertFalse(
            is_waveform_result_obsolete(
                request_id=2,
                active_request_id=2,
                current_file=file_a,
                result_file=file_a,
                cancelled=False,
                widget_exists=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
