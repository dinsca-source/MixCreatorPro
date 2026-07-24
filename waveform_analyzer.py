# -*- coding: utf-8 -*-
"""
Waveform analysis utilities with persistent cache support.
"""

from __future__ import annotations

import array
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ffmpeg_manager import FFmpegManager
from waveform_cache import decode_cached_levels, load_cached_waveform, save_cached_waveform

ENABLE_PERFORMANCE_LOG = True
DEFAULT_SAMPLE_RATE = 8000
DEFAULT_CHANNELS = 1
DEFAULT_BUCKET_COUNT = 4096
MIN_BUCKETS = 1200
MAX_BUCKETS = 10000
ZOOM_LEVELS = [1, 2, 4, 8, 16]


def log_perf(label: str, seconds: float) -> None:
    if not ENABLE_PERFORMANCE_LOG:
        return
    print(f"[ClipEditorPerf] {label}: {seconds:.3f} s")


def normalize_bucket_count(duration_ms: int, requested: int | None = None) -> int:
    if requested is not None:
        return max(MIN_BUCKETS, min(MAX_BUCKETS, int(requested)))

    duration_seconds = max(1.0, duration_ms / 1000.0)
    dynamic = int(duration_seconds * 40)
    return max(MIN_BUCKETS, min(MAX_BUCKETS, max(dynamic, DEFAULT_BUCKET_COUNT)))


def analysis_params(duration_ms: int, bucket_count: int | None = None) -> dict[str, Any]:
    buckets = normalize_bucket_count(duration_ms, bucket_count)
    return {
        "sample_rate": DEFAULT_SAMPLE_RATE,
        "channels": DEFAULT_CHANNELS,
        "pcm_format": "s16le",
        "bucket_count": buckets,
        "zoom_levels": ZOOM_LEVELS,
        "algorithm": "stream_minmax_v1",
    }


def get_or_build_waveform(
    file_path: str | Path,
    duration_ms: int,
    bucket_count: int | None = None,
) -> dict[str, Any]:
    source = Path(file_path)
    t_start = time.perf_counter()

    if not source.is_file():
        return {
            "ok": False,
            "error": "File non trovato",
            "cache_hit": False,
            "levels": {},
            "timings": {"waveform_ready_total": time.perf_counter() - t_start},
        }

    params = analysis_params(duration_ms, bucket_count=bucket_count)
    t_cache = time.perf_counter()
    cached = load_cached_waveform(source, params)
    cache_time = time.perf_counter() - t_cache

    if cached is not None:
        levels = decode_cached_levels(cached)
        total = time.perf_counter() - t_start
        return {
            "ok": bool(levels),
            "error": None if levels else "Cache waveform non valida",
            "cache_hit": bool(levels),
            "levels": levels,
            "timings": {
                "waveform_cache_hit": cache_time,
                "waveform_ready_total": total,
            },
            "duration_ms": int(cached.get("duration_ms", duration_ms) or duration_ms),
            "analysis_params": params,
        }

    try:
        t_decode_start = time.perf_counter()
        base = _stream_pcm_to_minmax(source, duration_ms, params["sample_rate"], params["bucket_count"])
        decode_time = time.perf_counter() - t_decode_start

        t_reduce_start = time.perf_counter()
        levels = _build_levels(base, params["bucket_count"])
        reduce_time = time.perf_counter() - t_reduce_start

        save_cached_waveform(source, params, duration_ms, levels)

        total = time.perf_counter() - t_start
        return {
            "ok": True,
            "error": None,
            "cache_hit": False,
            "levels": levels,
            "timings": {
                "waveform_decode": decode_time,
                "waveform_reduce": reduce_time,
                "waveform_ready_total": total,
            },
            "duration_ms": int(duration_ms),
            "analysis_params": params,
        }
    except Exception as error:
        total = time.perf_counter() - t_start
        return {
            "ok": False,
            "error": str(error),
            "cache_hit": False,
            "levels": {},
            "timings": {"waveform_ready_total": total},
            "duration_ms": int(duration_ms),
            "analysis_params": params,
        }


def _stream_pcm_to_minmax(source: Path, duration_ms: int, sample_rate: int, bucket_count: int) -> list[tuple[float, float]]:
    manager = FFmpegManager()
    ffmpeg_path = manager.ffmpeg_path

    if not ffmpeg_path.is_file():
        raise RuntimeError(f"FFmpeg non trovato: {ffmpeg_path}")

    command = [
        str(ffmpeg_path),
        "-v", "error",
        "-i", str(source),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-",
    ]

    total_samples_est = max(1, int((duration_ms / 1000.0) * sample_rate))
    mins = [1.0] * bucket_count
    maxs = [-1.0] * bucket_count
    touched = [False] * bucket_count

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )

    remainder = b""
    sample_index = 0
    chunk_size = 64 * 1024

    try:
        while True:
            if process.stdout is None:
                break
            chunk = process.stdout.read(chunk_size)
            if not chunk:
                break

            data = remainder + chunk
            if len(data) % 2 != 0:
                remainder = data[-1:]
                data = data[:-1]
            else:
                remainder = b""

            if not data:
                continue

            samples = array.array("h")
            samples.frombytes(data)
            _reduce_chunk_into_buckets(samples, sample_index, total_samples_est, mins, maxs, touched)
            sample_index += len(samples)

        stderr_output = b""
        if process.stderr is not None:
            stderr_output = process.stderr.read()

        return_code = process.wait(timeout=10)
        if return_code != 0:
            message = stderr_output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg waveform failed: {message or return_code}")
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    return _finalize_minmax(mins, maxs, touched)


def _reduce_chunk_into_buckets(
    samples: array.array,
    sample_start_index: int,
    total_samples_est: int,
    mins: list[float],
    maxs: list[float],
    touched: list[bool],
) -> None:
    bucket_count = len(mins)
    pos = 0
    sample_count = len(samples)

    while pos < sample_count:
        global_index = sample_start_index + pos
        bucket = int((global_index * bucket_count) / max(1, total_samples_est))
        if bucket >= bucket_count:
            bucket = bucket_count - 1

        bucket_end_global = ((bucket + 1) * total_samples_est + bucket_count - 1) // bucket_count
        take = max(1, min(sample_count - pos, int(bucket_end_global - global_index)))

        segment = samples[pos:pos + take]
        seg_min = min(segment)
        seg_max = max(segment)

        low = max(-1.0, min(1.0, seg_min / 32768.0))
        high = max(-1.0, min(1.0, seg_max / 32768.0))

        if not touched[bucket]:
            mins[bucket] = low
            maxs[bucket] = high
            touched[bucket] = True
        else:
            if low < mins[bucket]:
                mins[bucket] = low
            if high > maxs[bucket]:
                maxs[bucket] = high

        pos += take


def _finalize_minmax(mins: list[float], maxs: list[float], touched: list[bool]) -> list[tuple[float, float]]:
    last_min = 0.0
    last_max = 0.0
    peaks: list[tuple[float, float]] = []

    for index in range(len(mins)):
        if not touched[index]:
            peaks.append((last_min, last_max))
            continue

        last_min = mins[index]
        last_max = maxs[index]
        peaks.append((last_min, last_max))

    return peaks


def _build_levels(finest_peaks: list[tuple[float, float]], finest_bucket_count: int) -> dict[int, list[tuple[float, float]]]:
    levels: dict[int, list[tuple[float, float]]] = {}

    for zoom in ZOOM_LEVELS:
        target = max(MIN_BUCKETS, min(finest_bucket_count, int((finest_bucket_count / max(ZOOM_LEVELS)) * zoom)))
        levels[zoom] = _resample_minmax(finest_peaks, target)

    return levels


def _resample_minmax(peaks: list[tuple[float, float]], target_count: int) -> list[tuple[float, float]]:
    if not peaks:
        return []
    if target_count <= 0 or target_count == len(peaks):
        return peaks[:]

    src_count = len(peaks)
    result: list[tuple[float, float]] = []

    for index in range(target_count):
        start = int(index * src_count / target_count)
        end = int((index + 1) * src_count / target_count)
        if end <= start:
            end = min(src_count, start + 1)

        block = peaks[start:end]
        if not block:
            continue

        low = min(item[0] for item in block)
        high = max(item[1] for item in block)
        result.append((low, high))

    return result
