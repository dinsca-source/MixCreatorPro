# -*- coding: utf-8 -*-
"""
Gestione progetti MixCreatorPro (.mixproject).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clip_info import ClipInfo
from utils import scan_mp3_files

PROJECT_EXTENSION = ".mixproject"
PROJECT_FORMAT = "MixCreatorPro Project"
SUPPORTED_PROJECT_VERSION = 1
DURATION_MATCH_TOLERANCE_MS = 2000
AUTO_APPEND_NEW_TRACKS = True


class ProjectManagerError(RuntimeError):
    """Errore base del project manager."""


class ProjectValidationError(ProjectManagerError):
    """Formato progetto non valido."""


class ProjectVersionError(ProjectValidationError):
    """Versione progetto non supportata."""


class ProjectResolutionError(ProjectManagerError):
    """Impossibile risolvere file/cartella del progetto."""


@dataclass
class ProjectLoadResult:
    source_folder: str
    tracks: list[dict[str, Any]]
    settings: dict[str, Any]
    missing_files: list[str]
    modified_files: list[str]
    new_files: list[str]
    warnings: list[str]
    project_version: int


@dataclass
class _ResolvedFile:
    path: Path
    relative_path: str
    file_name: str
    size_bytes: int
    modified_ts: float


def save_project(
    project_path: str | Path,
    source_folder: str | Path,
    tracks: list[dict[str, Any]],
    project_settings: dict[str, Any],
    last_generated_mix: dict[str, Any] | None = None,
) -> Path:
    target = _ensure_project_extension(project_path)
    source = Path(source_folder).expanduser()

    target.parent.mkdir(parents=True, exist_ok=True)

    created_at = _read_existing_created_at(target)
    now = _iso_now()

    data = {
        "format": PROJECT_FORMAT,
        "version": SUPPORTED_PROJECT_VERSION,
        "created_at": created_at or now,
        "modified_at": now,
        "application_version": str(project_settings.get("application_version", "unknown")),
        "source_folder": str(source),
        "settings": dict(project_settings),
        "tracks": list(tracks),
    }

    if last_generated_mix is not None:
        data["last_generated_mix"] = last_generated_mix

    _atomic_write_json(target, data)
    return target


def load_project(project_path: str | Path) -> dict[str, Any]:
    path = Path(project_path).expanduser()

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as error:
        raise ProjectManagerError(f"File progetto non trovato:\n{path}") from error
    except json.JSONDecodeError as error:
        raise ProjectValidationError(
            f"Il file progetto non contiene JSON valido:\n{path}\n{error}"
        ) from error
    except OSError as error:
        raise ProjectManagerError(f"Impossibile leggere il progetto:\n{error}") from error

    validate_project(data)
    return data


def validate_project(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ProjectValidationError("Struttura progetto non valida: oggetto JSON atteso.")

    if data.get("format") != PROJECT_FORMAT:
        raise ProjectValidationError("Formato progetto non riconosciuto.")

    version = data.get("version")
    if not isinstance(version, int):
        raise ProjectValidationError("Campo 'version' non valido nel progetto.")

    if version > SUPPORTED_PROJECT_VERSION:
        raise ProjectVersionError(
            "Questo progetto è stato creato con una versione più recente di MixCreatorPro."
        )

    if version < 1:
        raise ProjectValidationError("Versione progetto non valida.")

    if not isinstance(data.get("tracks"), list):
        raise ProjectValidationError("Campo 'tracks' mancante o non valido.")

    if not isinstance(data.get("settings"), dict):
        raise ProjectValidationError("Campo 'settings' mancante o non valido.")

    source_folder = data.get("source_folder")
    if source_folder is None or not str(source_folder).strip():
        raise ProjectValidationError("Campo 'source_folder' mancante o vuoto.")


def resolve_project_files(
    data: dict[str, Any],
    selected_folder: str | Path | None = None,
    *,
    include_subfolders: bool = False,
    auto_append_new: bool = AUTO_APPEND_NEW_TRACKS,
) -> ProjectLoadResult:
    validate_project(data)

    warnings: list[str] = []
    missing_files: list[str] = []
    modified_files: list[str] = []

    source_folder = Path(selected_folder or data["source_folder"]).expanduser()
    if not source_folder.is_dir():
        raise ProjectResolutionError(f"Cartella sorgente non valida:\n{source_folder}")

    source_folder = source_folder.resolve()
    scanned = _scan_mp3_files(source_folder, include_subfolders=include_subfolders)

    by_relative = {
        item.relative_path.lower(): item
        for item in scanned
    }

    by_name: dict[str, list[_ResolvedFile]] = {}
    by_name_size: dict[tuple[str, int], list[_ResolvedFile]] = {}
    for item in scanned:
        key_name = item.file_name.lower()
        by_name.setdefault(key_name, []).append(item)
        by_name_size.setdefault((key_name, item.size_bytes), []).append(item)

    duration_cache: dict[str, int | None] = {}
    consumed_relative: set[str] = set()
    restored_tracks: list[dict[str, Any]] = []

    for saved in data["tracks"]:
        if not isinstance(saved, dict):
            warnings.append("Elemento track non valido ignorato nel progetto.")
            continue

        display_name = str(saved.get("relative_path") or saved.get("file_name") or "<sconosciuto>")
        matched: _ResolvedFile | None = None

        normalized_relative = _normalize_relative_path(saved.get("relative_path"))
        if normalized_relative is not None:
            candidate = by_relative.get(normalized_relative.lower())
            if candidate is not None:
                matched = candidate

        if matched is None:
            file_name = str(saved.get("file_name") or Path(display_name).name)
            size_bytes = _safe_int(saved.get("size_bytes"))
            if file_name and size_bytes is not None:
                candidates = by_name_size.get((file_name.lower(), size_bytes), [])
                matched = _pick_unique_match(candidates, display_name, warnings, "nome+dimensione")

        if matched is None:
            file_name = str(saved.get("file_name") or Path(display_name).name)
            saved_duration = _safe_int(saved.get("duration_ms"))
            if file_name and saved_duration is not None:
                candidates = by_name.get(file_name.lower(), [])
                duration_matches = []
                for candidate in candidates:
                    current_duration = _read_duration_ms(candidate.path, duration_cache)
                    if current_duration is None:
                        continue
                    if abs(current_duration - saved_duration) <= DURATION_MATCH_TOLERANCE_MS:
                        duration_matches.append(candidate)

                matched = _pick_unique_match(duration_matches, display_name, warnings, "nome+durata")

        if matched is None:
            absolute_original = saved.get("absolute_path_original")
            if absolute_original:
                candidate = Path(str(absolute_original)).expanduser()
                if candidate.is_file() and candidate.suffix.lower() == ".mp3":
                    candidate = candidate.resolve()
                    rel = _safe_relative_to(candidate, source_folder)
                    if rel is not None:
                        match_from_scan = by_relative.get(rel.lower())
                        if match_from_scan is not None:
                            matched = match_from_scan
                    else:
                        stat = candidate.stat()
                        matched = _ResolvedFile(
                            path=candidate,
                            relative_path=candidate.name,
                            file_name=candidate.name,
                            size_bytes=int(stat.st_size),
                            modified_ts=float(stat.st_mtime)
                        )

        if matched is None:
            missing_files.append(display_name)
            continue

        consumed_relative.add(matched.relative_path.lower())

        clip_info = ClipInfo.from_dict(saved.get("clip_info"), resolved_path=matched.path)
        saved_duration = _safe_int(saved.get("duration_ms"))
        current_duration = _read_duration_ms(matched.path, duration_cache)
        effective_duration = current_duration if current_duration is not None else saved_duration

        is_modified = _is_modified(saved, matched)
        if is_modified:
            clip_info = _clamp_clip_info(clip_info, effective_duration)
            modified_files.append(matched.relative_path)

        restored_tracks.append(
            {
                "file_name": matched.relative_path,
                "relative_path": matched.relative_path,
                "absolute_path": str(matched.path),
                "clip_info": clip_info,
                "size_bytes": matched.size_bytes,
                "modified_ts": matched.modified_ts,
                "duration_ms": effective_duration
            }
        )

    new_files: list[str] = []
    if auto_append_new:
        for candidate in sorted(scanned, key=lambda item: item.relative_path.lower()):
            if candidate.relative_path.lower() in consumed_relative:
                continue
            restored_tracks.append(
                {
                    "file_name": candidate.relative_path,
                    "relative_path": candidate.relative_path,
                    "absolute_path": str(candidate.path),
                    "clip_info": ClipInfo(),
                    "size_bytes": candidate.size_bytes,
                    "modified_ts": candidate.modified_ts,
                    "duration_ms": _read_duration_ms(candidate.path, duration_cache)
                }
            )
            new_files.append(candidate.relative_path)

    return ProjectLoadResult(
        source_folder=str(source_folder),
        tracks=restored_tracks,
        settings=dict(data.get("settings", {})),
        missing_files=missing_files,
        modified_files=modified_files,
        new_files=new_files,
        warnings=warnings,
        project_version=int(data.get("version", 0))
    )


def _scan_mp3_files(folder: Path, *, include_subfolders: bool = False) -> list[_ResolvedFile]:
    resolved: list[_ResolvedFile] = []

    for file_path in scan_mp3_files(
        folder,
        include_subfolders=include_subfolders,
        exclude_diagnostics_sessions=True,
    ):
        try:
            relative = file_path.relative_to(folder).as_posix()
            stat = file_path.stat()
        except OSError:
            continue

        resolved.append(
            _ResolvedFile(
                path=file_path.resolve(),
                relative_path=relative,
                file_name=file_path.name,
                size_bytes=int(stat.st_size),
                modified_ts=float(stat.st_mtime)
            )
        )

    return resolved


def _pick_unique_match(
    candidates: list[_ResolvedFile],
    display_name: str,
    warnings: list[str],
    strategy: str
) -> _ResolvedFile | None:
    if not candidates:
        return None

    if len(candidates) > 1:
        warnings.append(
            "Corrispondenza ambigua per "
            f"'{display_name}' con strategia {strategy}: "
            f"{len(candidates)} candidati."
        )
        return None

    return candidates[0]


def _is_modified(saved_track: dict[str, Any], resolved: _ResolvedFile) -> bool:
    saved_size = _safe_int(saved_track.get("size_bytes"))
    saved_mtime = _safe_float(saved_track.get("modified_timestamp"))

    size_changed = saved_size is not None and saved_size != resolved.size_bytes

    # Finestra piccola per evitare mismatch dovuti ad arrotondamenti.
    mtime_changed = (
        saved_mtime is not None
        and abs(saved_mtime - resolved.modified_ts) > 1e-3
    )

    return size_changed or mtime_changed


def _clamp_clip_info(clip_info: ClipInfo, full_duration_ms: int | None) -> ClipInfo:
    fixed = clip_info.copy()

    if not fixed.use_custom_clip:
        return fixed

    if full_duration_ms is None or full_duration_ms <= 0:
        return fixed

    if fixed.clip_end_ms > full_duration_ms:
        fixed.clip_end_ms = full_duration_ms

    if fixed.clip_start_ms < 0:
        fixed.clip_start_ms = 0

    if fixed.clip_start_ms >= fixed.clip_end_ms:
        fixed.clip_start_ms = max(0, fixed.clip_end_ms - 1000)

    if fixed.clip_end_ms <= fixed.clip_start_ms:
        fixed.use_custom_clip = False
        fixed.clip_start_ms = 0
        fixed.clip_end_ms = 0

    return fixed


def _read_duration_ms(path: Path, cache: dict[str, int | None]) -> int | None:
    cache_key = str(path)
    if cache_key in cache:
        return cache[cache_key]

    duration_ms: int | None = None

    try:
        from ffmpeg_manager import FFmpegManager

        manager = FFmpegManager()
        if manager.ffprobe_path.is_file():
            duration_seconds = manager.get_duration(path)
            duration_ms = int(round(duration_seconds * 1000))
    except Exception:
        duration_ms = None

    cache[cache_key] = duration_ms
    return duration_ms


def _read_existing_created_at(path: Path) -> str | None:
    if not path.is_file():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    created_at = data.get("created_at")
    if isinstance(created_at, str) and created_at.strip():
        return created_at
    return None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temp_path: Path | None = None

    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f"{path.stem}.",
            suffix=".tmp",
            dir=str(path.parent)
        )
        temp_path = Path(temp_name)

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)

    except OSError as error:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

        raise ProjectManagerError(
            f"Impossibile salvare il progetto in modo sicuro:\n{error}"
        ) from error


def _ensure_project_extension(path: str | Path) -> Path:
    value = Path(path).expanduser()
    if value.suffix.lower() == PROJECT_EXTENSION:
        return value
    return value.with_suffix(PROJECT_EXTENSION)


def _normalize_relative_path(value: Any) -> str | None:
    if value is None:
        return None

    raw = str(value).strip().replace("\\", "/")
    if not raw:
        return None

    rel = Path(raw)
    if rel.is_absolute():
        return None

    normalized_parts: list[str] = []
    for part in rel.parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        normalized_parts.append(part)

    if not normalized_parts:
        return None

    return Path(*normalized_parts).as_posix()


def _safe_relative_to(path: Path, base: Path) -> str | None:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
