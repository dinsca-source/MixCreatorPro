# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


REVERIFY_STATUS_REPAIRED = "Riparato"
REVERIFY_STATUS_UNRECOVERABLE = "Non recuperabile"

_TARGET_STATUSES = {
    "riparato": REVERIFY_STATUS_REPAIRED,
    "non recuperabile": REVERIFY_STATUS_UNRECOVERABLE,
}

_FORBIDDEN_GENERATED_FOLDERS = {
    "file riparati",
    "non recuperabili",
    "file già rilevati ok",
    "file gia rilevati ok",
    "originali dei file elaborati",
}

_REQUIRED_COLUMNS = (
    "Stato finale file",
    "Percorso originale",
    "File",
)

_OPTIONAL_COLUMNS = (
    "Categoria finale",
    "Fine audio significativo",
    "Silenzio finale (ms)",
)


class SelectiveReverifyError(ValueError):
    """Raised when a selective reverify report cannot be parsed."""


@dataclass(slots=True)
class PreviousReportRow:
    file_name: str
    original_path: str
    previous_status: str
    previous_category: str
    previous_significant_end: str
    previous_trailing_silence_ms: str


@dataclass(slots=True)
class MissingOriginalRow:
    row: PreviousReportRow
    reason: str


@dataclass(slots=True)
class SelectiveReverifySelection:
    report_csv_path: Path
    total_rows: int
    repaired_rows: int
    unrecoverable_rows: int
    duplicates_excluded: int
    valid_original_files: list[Path]
    missing_originals: list[MissingOriginalRow]
    selected_rows: list[PreviousReportRow]

    @property
    def final_reverify_count(self) -> int:
        return len(self.valid_original_files)


def prepare_selective_reverify_selection(report_csv_path: str | Path) -> SelectiveReverifySelection:
    csv_path = Path(report_csv_path).expanduser().resolve()
    if not csv_path.is_file():
        raise SelectiveReverifyError(f"Report CSV non trovato: {csv_path}")

    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header_map = _resolve_header_map(reader.fieldnames or [])

            missing_required = [name for name in _REQUIRED_COLUMNS if name not in header_map]
            if missing_required:
                raise SelectiveReverifyError(
                    "Colonne obbligatorie mancanti nel report selezionato: "
                    + ", ".join(missing_required)
                )

            total_rows = 0
            repaired_rows = 0
            unrecoverable_rows = 0
            duplicates_excluded = 0

            valid_original_files: list[Path] = []
            missing_originals: list[MissingOriginalRow] = []
            selected_rows: list[PreviousReportRow] = []
            seen_paths: set[str] = set()

            for row in reader:
                total_rows += 1

                raw_status = _read_col(row, header_map, "Stato finale file")
                normalized_status = _normalize_status(raw_status)
                canonical_status = _TARGET_STATUSES.get(normalized_status)
                if canonical_status is None:
                    continue

                if canonical_status == REVERIFY_STATUS_REPAIRED:
                    repaired_rows += 1
                elif canonical_status == REVERIFY_STATUS_UNRECOVERABLE:
                    unrecoverable_rows += 1

                previous_row = PreviousReportRow(
                    file_name=_read_col(row, header_map, "File"),
                    original_path=_read_col(row, header_map, "Percorso originale"),
                    previous_status=canonical_status,
                    previous_category=_read_col(row, header_map, "Categoria finale"),
                    previous_significant_end=_read_col(row, header_map, "Fine audio significativo"),
                    previous_trailing_silence_ms=_read_col(row, header_map, "Silenzio finale (ms)"),
                )

                normalized_path_key = _normalize_path_key(previous_row.original_path)
                if normalized_path_key:
                    if normalized_path_key in seen_paths:
                        duplicates_excluded += 1
                        continue
                    seen_paths.add(normalized_path_key)

                selected_rows.append(previous_row)

                if not previous_row.original_path:
                    missing_originals.append(
                        MissingOriginalRow(row=previous_row, reason="Originale non trovato")
                    )
                    continue

                if _is_in_generated_folder(previous_row.original_path):
                    missing_originals.append(
                        MissingOriginalRow(
                            row=previous_row,
                            reason="Percorso originale in cartella generata precedente",
                        )
                    )
                    continue

                original_path = Path(previous_row.original_path).expanduser().resolve()
                if not original_path.is_file():
                    missing_originals.append(
                        MissingOriginalRow(row=previous_row, reason="Originale non trovato")
                    )
                    continue

                valid_original_files.append(original_path)

    except SelectiveReverifyError:
        raise
    except Exception as error:
        raise SelectiveReverifyError(f"Errore lettura report CSV: {error}") from error

    return SelectiveReverifySelection(
        report_csv_path=csv_path,
        total_rows=total_rows,
        repaired_rows=repaired_rows,
        unrecoverable_rows=unrecoverable_rows,
        duplicates_excluded=duplicates_excluded,
        valid_original_files=sorted(valid_original_files, key=lambda p: p.as_posix().lower()),
        missing_originals=missing_originals,
        selected_rows=selected_rows,
    )


def _resolve_header_map(fieldnames: list[str]) -> dict[str, str]:
    by_normalized = {_normalize_header(name): name for name in fieldnames}
    resolved: dict[str, str] = {}
    for required_name in (*_REQUIRED_COLUMNS, *_OPTIONAL_COLUMNS):
        normalized = _normalize_header(required_name)
        if normalized in by_normalized:
            resolved[required_name] = by_normalized[normalized]
    return resolved


def _read_col(row: dict[str, str], header_map: dict[str, str], canonical_name: str) -> str:
    actual = header_map.get(canonical_name)
    if not actual:
        return ""
    return str(row.get(actual) or "").strip()


def _normalize_header(name: str) -> str:
    return " ".join(str(name or "").replace("\ufeff", "").strip().casefold().split())


def _normalize_status(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _normalize_path_key(path_text: str) -> str:
    value = str(path_text or "").strip()
    if not value:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _is_in_generated_folder(path_text: str) -> bool:
    value = str(path_text or "").strip()
    if not value:
        return False
    parts = [segment.strip().casefold() for segment in PureWindowsPath(value).parts if segment.strip()]
    return any(part in _FORBIDDEN_GENERATED_FOLDERS for part in parts)
