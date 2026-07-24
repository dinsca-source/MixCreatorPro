# -*- coding: utf-8 -*-

from __future__ import annotations

import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path

import waveform_cache
from waveform_cache import (
    cleanup_cache_if_needed,
    load_cached_waveform,
    make_cache_key,
    save_cached_waveform,
)


class WaveformCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_localappdata = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.temp.name
        waveform_cache._CLEANUP_DONE = False

        self.dir_a = Path(self.temp.name) / "A"
        self.dir_b = Path(self.temp.name) / "B"
        self.dir_a.mkdir(parents=True, exist_ok=True)
        self.dir_b.mkdir(parents=True, exist_ok=True)
        self.file_a = self.dir_a / "song.mp3"
        self.file_b = self.dir_b / "song.mp3"
        self.file_a.write_bytes(b"abc")
        self.file_b.write_bytes(b"abc")

        self.params = {
            "sample_rate": 8000,
            "channels": 1,
            "bucket_count": 1200,
            "algorithm": "stream_minmax_v1",
        }
        self.levels = {1: [(-0.5, 0.5), (-0.2, 0.2)]}

    def tearDown(self) -> None:
        if self.old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.old_localappdata
        self.temp.cleanup()

    def test_cache_key_differs_for_same_name_different_folder(self) -> None:
        key_a, _ = make_cache_key(self.file_a, self.params)
        key_b, _ = make_cache_key(self.file_b, self.params)
        self.assertNotEqual(key_a, key_b)

    def test_cache_hit_for_unchanged_file(self) -> None:
        ok = save_cached_waveform(self.file_a, self.params, 10_000, self.levels)
        self.assertTrue(ok)

        cached = load_cached_waveform(self.file_a, self.params)
        self.assertIsNotNone(cached)
        self.assertIn("levels", cached)

    def test_cache_miss_after_size_change(self) -> None:
        save_cached_waveform(self.file_a, self.params, 10_000, self.levels)
        self.file_a.write_bytes(b"abcdef")
        self.assertIsNone(load_cached_waveform(self.file_a, self.params))

    def test_cache_miss_after_mtime_change(self) -> None:
        save_cached_waveform(self.file_a, self.params, 10_000, self.levels)
        time.sleep(0.01)
        os.utime(self.file_a, None)
        self.assertIsNone(load_cached_waveform(self.file_a, self.params))

    def test_corrupted_cache_is_ignored(self) -> None:
        key, _ = make_cache_key(self.file_a, self.params)
        cache_file = waveform_cache._cache_root() / f"{key}.json.gz"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"not-gzip")

        self.assertIsNone(load_cached_waveform(self.file_a, self.params))
        self.assertFalse(cache_file.exists())

    def test_atomic_save_leaves_no_tmp(self) -> None:
        save_cached_waveform(self.file_a, self.params, 10_000, self.levels)
        tmp_files = list(waveform_cache._cache_root().glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_cleanup_policy(self) -> None:
        old_limit = waveform_cache.MAX_CACHE_FILES
        waveform_cache.MAX_CACHE_FILES = 3
        waveform_cache._CLEANUP_DONE = False
        try:
            for i in range(6):
                file_i = self.dir_a / f"f{i}.mp3"
                file_i.write_bytes(f"{i}".encode("utf-8"))
                save_cached_waveform(file_i, self.params, 1_000, self.levels)
                waveform_cache._CLEANUP_DONE = False

            cleanup_cache_if_needed(force=True)
            files = list(waveform_cache._cache_root().glob("*.json.gz"))
            self.assertLessEqual(len(files), 3)
        finally:
            waveform_cache.MAX_CACHE_FILES = old_limit


if __name__ == "__main__":
    unittest.main()
