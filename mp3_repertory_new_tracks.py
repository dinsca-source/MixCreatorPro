# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

STATUS_DA_GESTIRE = "Da gestire"
STATUS_GESTITO = "Gestito"
ROOT_FOLDER_LABEL = "ROOT"
ROOT_RELATIVE_PATH = ""


@dataclass(slots=True)
class NewTrackItem:
    source_path: str
    file_name: str
    status: str = STATUS_DA_GESTIRE
    destinations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class RepertoryFolderItem:
    relative_path: str
    full_path: str
    folder_name: str
    direct_mp3_count: int
    direct_mp3_size_bytes: int


class NewTracksAssignmentModel:
    def __init__(self) -> None:
        self._tracks_by_path: dict[str, NewTrackItem] = {}
        self._track_order: list[str] = []
        self._folders_by_relative: dict[str, RepertoryFolderItem] = {}

    def reset(self) -> None:
        self._tracks_by_path.clear()
        self._track_order.clear()
        self._folders_by_relative.clear()

    def load_tracks(self, tracks: list[NewTrackItem]) -> None:
        self._tracks_by_path = {item.source_path: item for item in tracks}
        self._track_order = [item.source_path for item in tracks]

    def load_folders(self, folders: list[RepertoryFolderItem]) -> None:
        self._folders_by_relative = {item.relative_path: item for item in folders}

    @property
    def tracks(self) -> list[NewTrackItem]:
        return [self._tracks_by_path[path] for path in self._track_order if path in self._tracks_by_path]

    @property
    def folders(self) -> list[RepertoryFolderItem]:
        return sorted(self._folders_by_relative.values(), key=lambda item: (_folder_depth(item.relative_path), item.relative_path.casefold()))

    @property
    def all_managed(self) -> bool:
        items = self.tracks
        return bool(items) and all(item.status == STATUS_GESTITO for item in items)

    def get_track(self, source_path: str) -> NewTrackItem | None:
        return self._tracks_by_path.get(source_path)

    def get_visible_tracks(self, *, show_managed: bool) -> list[NewTrackItem]:
        if show_managed:
            return list(self.tracks)
        return [item for item in self.tracks if item.status != STATUS_GESTITO]

    def sort_tracks(self, items: list[NewTrackItem], key: str, reverse: bool = False) -> list[NewTrackItem]:
        if key == "status":
            return sorted(items, key=lambda item: (item.status.casefold(), item.file_name.casefold()), reverse=reverse)
        return sorted(items, key=lambda item: (item.file_name.casefold(), item.status.casefold()), reverse=reverse)

    def can_assign_multiple(self, source_paths: list[str]) -> tuple[bool, str]:
        if len(source_paths) <= 1:
            return True, ""
        for source_path in source_paths:
            item = self._tracks_by_path.get(source_path)
            if item is not None and item.status == STATUS_GESTITO:
                return False, "La multiselezione e consentita solo per brani non ancora gestiti."
        return True, ""

    def assign_tracks(self, source_paths: list[str], destination_rel_paths: list[str]) -> None:
        if not source_paths:
            return
        can_assign, reason = self.can_assign_multiple(source_paths)
        if not can_assign:
            raise ValueError(reason)

        normalized_destinations = tuple(sorted({str(path or "").strip().replace("\\", "/").strip("/") for path in destination_rel_paths if str(path or "").strip()}))
        if not normalized_destinations:
            raise ValueError("Selezionare almeno una cartella del repertorio.")

        for source_path in source_paths:
            item = self._tracks_by_path.get(source_path)
            if item is None:
                continue
            item.destinations = normalized_destinations
            item.status = STATUS_GESTITO

    def remove_assignments(self, source_paths: list[str]) -> None:
        for source_path in source_paths:
            item = self._tracks_by_path.get(source_path)
            if item is None:
                continue
            item.destinations = ()
            item.status = STATUS_DA_GESTIRE

    def destination_labels_for_track(self, source_path: str) -> str:
        item = self._tracks_by_path.get(source_path)
        if item is None or not item.destinations:
            return ""
        labels: list[str] = []
        for destination in item.destinations:
            labels.append(self._display_label_for_destination(destination))
        return ", ".join(labels)

    def _display_label_for_destination(self, relative_path: str) -> str:
        normalized = str(relative_path or "").strip().replace("\\", "/").strip("/")
        if normalized in {"", "."}:
            return ROOT_FOLDER_LABEL

        leaf = normalized.split("/")[-1]
        colliding_relatives = self._folder_relatives_with_same_leaf(leaf)
        if len(colliding_relatives) <= 1:
            return leaf

        # Use the shortest relative suffix that uniquely identifies this folder among same-leaf siblings.
        minimal = self._minimal_unique_suffix(normalized, colliding_relatives)
        return minimal.replace("/", "\\")

    def _folder_relatives_with_same_leaf(self, leaf_name: str) -> list[str]:
        target = str(leaf_name or "").casefold()
        relatives: list[str] = []
        for relative in self._folders_by_relative.keys():
            normalized = str(relative or "").strip().replace("\\", "/").strip("/")
            if not normalized:
                continue
            if normalized.split("/")[-1].casefold() == target:
                relatives.append(normalized)
        if not relatives:
            relatives.append(str(leaf_name or ""))
        return sorted(set(relatives), key=lambda item: item.casefold())

    @staticmethod
    def _minimal_unique_suffix(target: str, candidates: list[str]) -> str:
        target_parts = [part for part in target.split("/") if part]
        if not target_parts:
            return target
        normalized_candidates = [
            [part for part in candidate.split("/") if part]
            for candidate in candidates
        ]
        for suffix_len in range(1, len(target_parts) + 1):
            suffix = tuple(part.casefold() for part in target_parts[-suffix_len:])
            matches = 0
            for parts in normalized_candidates:
                if len(parts) < suffix_len:
                    continue
                if tuple(part.casefold() for part in parts[-suffix_len:]) == suffix:
                    matches += 1
            if matches <= 1:
                return "/".join(target_parts[-suffix_len:])
        return "/".join(target_parts)

    def assignments_snapshot(self) -> dict[str, dict[str, object]]:
        snapshot: dict[str, dict[str, object]] = {}
        for item in self.tracks:
            snapshot[item.source_path] = {
                "destinations": list(item.destinations),
                "status": item.status,
            }
        return snapshot


def list_new_tracks_non_recursive(folder: str | Path) -> list[NewTrackItem]:
    root = Path(folder).expanduser().resolve()
    items: list[NewTrackItem] = []
    for child in root.iterdir():
        if not child.is_file() or child.suffix.lower() != ".mp3":
            continue
        items.append(
            NewTrackItem(
                source_path=str(child),
                file_name=child.name,
            )
        )
    items.sort(key=lambda item: item.file_name.casefold())
    return items


def scan_repertory_folders_non_recursive_stats(
    root_folder: str | Path,
    *,
    excluded_relative_roots: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[RepertoryFolderItem]:
    root = Path(root_folder).expanduser().resolve()
    folders: list[RepertoryFolderItem] = []
    excluded = {
        str(item or "").strip().replace("\\", "/").strip("/").casefold()
        for item in (excluded_relative_roots or [])
        if str(item or "").strip()
    }

    for current_root, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root)
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]

        relative_path = _relative_posix(current_path, root)
        folded_relative = relative_path.casefold()
        if folded_relative and any(folded_relative == candidate or folded_relative.startswith(candidate + "/") for candidate in excluded):
            dirs[:] = []
            continue

        filtered_dirs: list[str] = []
        for name in dirs:
            candidate_relative = f"{relative_path}/{name}".strip("/")
            folded_candidate = candidate_relative.casefold()
            if any(folded_candidate == candidate or folded_candidate.startswith(candidate + "/") for candidate in excluded):
                continue
            filtered_dirs.append(name)
        dirs[:] = filtered_dirs

        folder_name = ROOT_FOLDER_LABEL if not relative_path else current_path.name
        mp3_count = 0
        mp3_size = 0
        for file_name in files:
            candidate = current_path / file_name
            if candidate.suffix.lower() != ".mp3":
                continue
            if candidate.is_symlink():
                continue
            mp3_count += 1
            try:
                mp3_size += int(candidate.stat().st_size)
            except OSError:
                continue

        folders.append(
            RepertoryFolderItem(
                relative_path=relative_path,
                full_path=str(current_path),
                folder_name=folder_name,
                direct_mp3_count=mp3_count,
                direct_mp3_size_bytes=mp3_size,
            )
        )

    folders.sort(key=lambda item: (_folder_depth(item.relative_path), item.relative_path.casefold()))
    return folders


def ensure_folder_available(folder: str | Path) -> Path:
    target = Path(folder).expanduser().resolve()
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    if not target.exists() or not target.is_dir():
        raise OSError(f"Cartella non disponibile: {target}")
    return target


def format_size_megabytes(size_bytes: int) -> str:
    mb_value = max(0, int(size_bytes)) / (1024.0 * 1024.0)
    return f"{mb_value:.2f} MB"


def _relative_posix(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except Exception:
        try:
            relative = path.relative_to(root)
        except Exception:
            return ""
    text = relative.as_posix()
    if text == ".":
        return ""
    return text


def _folder_depth(relative_path: str) -> int:
    if not relative_path:
        return 0
    return relative_path.count("/") + 1
