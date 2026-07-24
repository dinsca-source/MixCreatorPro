# -*- coding: utf-8 -*-
"""
Persistent cache for reduced waveform data.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

CACHE_VERSION = 1
MAX_CACHE_FILES = 300
MAX_CACHE_SIZE_BYTES = 350 * 1024 * 1024

_CLEANUP_DONE = False


def _cache_root() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata)
    else:
        base = Path.home() / "AppData" / "Local"

    return base / "MixCreatorPro" / "waveform_cache"


def _normalize_path(path: Path) -> str:
    normalized = path.resolve().as_posix()
    if os.name == "nt":
        return normalized.lower()
    return normalized


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stats = path.stat()
    mtime_ns = getattr(stats, "st_mtime_ns", int(stats.st_mtime * 1_000_000_000))
    return {
        "path": _normalize_path(path),
        "size": int(stats.st_size),
        "mtime_ns": int(mtime_ns),
    }


def make_cache_key(path: str | Path, analysis_params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    file_path = Path(path)
    fingerprint = _file_fingerprint(file_path)

    payload = {
        "cache_version": CACHE_VERSION,
        "fingerprint": fingerprint,
        "analysis": analysis_params,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return key, fingerprint


def _cache_file_path(cache_key: str) -> Path:
    return _cache_root() / f"{cache_key}.json.gz"


def load_cached_waveform(path: str | Path, analysis_params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        cache_key, fingerprint = make_cache_key(path, analysis_params)
    except OSError:
        return None

    cache_path = _cache_file_path(cache_key)
    if not cache_path.is_file():
        return None

    try:
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        _safe_unlink(cache_path)
        return None

    if not isinstance(data, dict):
        _safe_unlink(cache_path)
        return None

    if data.get("cache_version") != CACHE_VERSION:
        _safe_unlink(cache_path)
        return None

    if data.get("cache_key") != cache_key:
        _safe_unlink(cache_path)
        return None

    if data.get("fingerprint") != fingerprint:
        _safe_unlink(cache_path)
        return None

    levels = data.get("levels")
    if not isinstance(levels, dict) or not levels:
        _safe_unlink(cache_path)
        return None

    try:
        os.utime(cache_path, None)
    except OSError:
        pass

    return data


def save_cached_waveform(
    path: str | Path,
    analysis_params: dict[str, Any],
    duration_ms: int,
    levels: dict[int, list[tuple[float, float]]],
) -> bool:
    try:
        cache_key, fingerprint = make_cache_key(path, analysis_params)
    except OSError:
        return False

    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)

    serialized_levels = {
        str(int(zoom)): [[float(item[0]), float(item[1])] for item in peaks]
        for zoom, peaks in levels.items()
    }

    payload = {
        "cache_version": CACHE_VERSION,
        "cache_key": cache_key,
        "fingerprint": fingerprint,
        "analysis": analysis_params,
        "duration_ms": int(duration_ms),
        "levels": serialized_levels,
    }

    temp_path: Path | None = None
    final_path = _cache_file_path(cache_key)

    try:
        fd, temp_name = tempfile.mkstemp(prefix=f"{cache_key[:12]}.", suffix=".tmp", dir=str(root))
        temp_path = Path(temp_name)

        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb") as zipped:
                content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                zipped.write(content)
            raw.flush()
            os.fsync(raw.fileno())

        os.replace(temp_path, final_path)
    except OSError:
        if temp_path is not None:
            _safe_unlink(temp_path)
        return False

    cleanup_cache_if_needed()
    return True


def decode_cached_levels(data: dict[str, Any]) -> dict[int, list[tuple[float, float]]]:
    raw_levels = data.get("levels", {})
    decoded: dict[int, list[tuple[float, float]]] = {}

    if not isinstance(raw_levels, dict):
        return decoded

    for zoom_key, values in raw_levels.items():
        try:
            zoom = int(zoom_key)
        except (TypeError, ValueError):
            continue

        peaks: list[tuple[float, float]] = []
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, list) or len(item) != 2:
                    continue
                try:
                    low = float(item[0])
                    high = float(item[1])
                except (TypeError, ValueError):
                    continue
                peaks.append((max(-1.0, min(1.0, low)), max(-1.0, min(1.0, high))))
        if peaks:
            decoded[zoom] = peaks

    return decoded


def cleanup_cache_if_needed(force: bool = False) -> None:
    global _CLEANUP_DONE

    if _CLEANUP_DONE and not force:
        return

    root = _cache_root()
    if not root.is_dir():
        _CLEANUP_DONE = True
        return

    files = [item for item in root.glob("*.json.gz") if item.is_file()]
    if not files:
        _CLEANUP_DONE = True
        return

    files.sort(key=lambda item: item.stat().st_mtime)

    total_size = 0
    for item in files:
        try:
            total_size += item.stat().st_size
        except OSError:
            pass

    while len(files) > MAX_CACHE_FILES or total_size > MAX_CACHE_SIZE_BYTES:
        victim = files.pop(0)
        try:
            total_size -= victim.stat().st_size
        except OSError:
            pass
        _safe_unlink(victim)

    _CLEANUP_DONE = True


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
