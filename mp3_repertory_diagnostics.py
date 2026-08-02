# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from zipfile import ZIP_DEFLATED, ZipFile


DiagnosticsProgressCallback = Callable[[int, int, str], None]
DiagnosticsLogCallback = Callable[[str], None]

DIAGNOSTICS_SESSION_PREFIX = "Diagnosi_Repertorio_"
DIAGNOSTICS_FOLDER_NAME = "Diagnosi"
DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL = "File non esistenti in repertorio suddiviso"
DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT = "File non esistenti in cartella repertorio generale"
ROOT_FILES_TOKEN = "__ROOT_FILES__"
ROOT_FILES_REPORT_LABEL = "[FILE NELLA CARTELLA PRINCIPALE]"

AUTO_EXCLUDED_FOLDER_NAMES = {
    "file non trovati in repertorio",
    "file non trovati nel repertorio",
    "diagnosi",
}
DIAGNOSI_SESSION_RE = re.compile(r"^diagnosi_repertorio_\d{8}_\d{6}$", re.IGNORECASE)

FOLDER_STATUS_SELECTED = "Selezionata per il controllo"
FOLDER_STATUS_NOT_SELECTED = "Non selezionata per il controllo"
FOLDER_STATUS_AUTO_EXCLUDED = "Esclusa automaticamente"

FOLDER_REPORT_HEADERS = [
    "relative_path",
    "full_path",
    "level",
    "status",
    "reason",
    "mp3_detected",
    "mp3_processed",
]

FOLDER_REPORT_HEADER_LABELS = {
    "relative_path": "Percorso relativo",
    "full_path": "Percorso completo",
    "level": "Livello",
    "status": "Stato",
    "reason": "Motivo",
    "mp3_detected": "Numero MP3 rilevati",
    "mp3_processed": "Numero MP3 elaborati",
}

REPORT_HEADERS = [
    "stato",
    "file",
    "nome_normalizzato",
    "presente_in_cartella_generale",
    "percorso_cartella_generale",
    "presente_in_repertorio_suddiviso",
    "percorso_repertorio_suddiviso",
    "numero_occorrenze_nel_repertorio_suddiviso",
    "percorsi_duplicati",
    "dimensione_file_cartella_generale",
    "dimensione_file_repertorio",
    "data_ora_file_cartella_generale",
    "data_ora_file_repertorio",
    "durata_file_cartella_generale",
    "durata_file_repertorio",
    "confronto_contenuto",
    "percorso_copia_diagnosi",
    "esito_copia_diagnosi",
    "motivo_note",
    "dettaglio_errore",
]

REPORT_HEADER_LABELS = {
    "stato": "Stato",
    "file": "File",
    "nome_normalizzato": "Nome normalizzato",
    "presente_in_cartella_generale": "Presente in Cartella Repertorio Generale",
    "percorso_cartella_generale": "Percorso Cartella Repertorio Generale",
    "presente_in_repertorio_suddiviso": "Presente in Repertorio suddiviso",
    "percorso_repertorio_suddiviso": "Percorso Repertorio suddiviso",
    "numero_occorrenze_nel_repertorio_suddiviso": "Numero occorrenze nel Repertorio suddiviso",
    "percorsi_duplicati": "Percorsi duplicati",
    "dimensione_file_cartella_generale": "Dimensione file Cartella Generale",
    "dimensione_file_repertorio": "Dimensione file Repertorio",
    "data_ora_file_cartella_generale": "Data/Ora file Cartella Generale",
    "data_ora_file_repertorio": "Data/Ora file Repertorio",
    "durata_file_cartella_generale": "Durata file Cartella Generale",
    "durata_file_repertorio": "Durata file Repertorio",
    "confronto_contenuto": "Confronto contenuto",
    "percorso_copia_diagnosi": "Percorso copia Diagnosi",
    "esito_copia_diagnosi": "Esito copia Diagnosi",
    "motivo_note": "Motivo/Note",
    "dettaglio_errore": "Dettaglio errore",
}


class DiagnosticsStatus(str, Enum):
    PRESENTE_ENTRAMBI = "PRESENTE_ENTRAMBI"
    SOLO_GENERALE = "SOLO_GENERALE"
    SOLO_SUDDIVISO = "SOLO_SUDDIVISO"
    DUPLICATO_SUDDIVISO = "DUPLICATO_SUDDIVISO"
    ERRORE_LETTURA = "ERRORE_LETTURA"
    INTERROTTO = "INTERROTTO"


class DiagnosticsError(RuntimeError):
    pass


class DiagnosticsCancelled(DiagnosticsError):
    pass


@dataclass(slots=True)
class DiagnosticsConfig:
    split_repertory_dir: str | Path
    general_repertory_dir: str | Path
    results_dir: str | Path | None = None
    copy_only_missing: bool = True
    selected_relative_roots: tuple[str, ...] = ()
    excluded_relative_roots: tuple[str, ...] = ()
    include_root_files: bool = True


@dataclass(slots=True)
class SplitFolderNode:
    relative_path: str
    full_path: str
    level: int
    auto_excluded: bool
    auto_exclusion_reason: str
    selectable: bool
    is_virtual_root_files: bool = False
    mp3_detected: int = 0


@dataclass(slots=True)
class FolderReportRecord:
    relative_path: str
    full_path: str
    level: int
    status: str
    reason: str
    mp3_detected: int
    mp3_processed: int

    def to_row(self) -> dict[str, str]:
        relative_label = self.relative_path
        if relative_label == ROOT_FILES_TOKEN:
            relative_label = ROOT_FILES_REPORT_LABEL
        return {
            "relative_path": relative_label,
            "full_path": self.full_path,
            "level": str(self.level),
            "status": self.status,
            "reason": self.reason,
            "mp3_detected": str(max(0, int(self.mp3_detected))),
            "mp3_processed": str(max(0, int(self.mp3_processed))),
        }


@dataclass(slots=True)
class DiagnosticsRecord:
    stato: str
    file: str
    nome_normalizzato: str
    presente_in_cartella_generale: str
    percorso_cartella_generale: str
    presente_in_repertorio_suddiviso: str
    percorso_repertorio_suddiviso: str
    numero_occorrenze_nel_repertorio_suddiviso: str
    percorsi_duplicati: str
    dimensione_file_cartella_generale: str
    dimensione_file_repertorio: str
    data_ora_file_cartella_generale: str
    data_ora_file_repertorio: str
    durata_file_cartella_generale: str
    durata_file_repertorio: str
    confronto_contenuto: str
    percorso_copia_diagnosi: str
    esito_copia_diagnosi: str
    motivo_note: str
    dettaglio_errore: str

    def to_row(self) -> dict[str, str]:
        return {key: str(getattr(self, key, "") or "") for key in REPORT_HEADERS}


@dataclass(slots=True)
class DiagnosticsResult:
    success: bool
    interrupted: bool
    error: str | None
    analyzed_general_files: int
    analyzed_split_files: int
    general_unique_titles: int
    split_unique_titles: int
    matched_both: int
    only_general: int
    only_split: int
    split_duplicates: int
    split_duplicate_extra_occurrences: int
    read_errors: int
    copied_files: int
    copy_errors: int
    is_perfect_alignment: bool
    duration_seconds: float
    session_folder: str
    diagnosis_root: str
    report_paths: dict[str, str]
    folder_report_paths: dict[str, str]
    log_path: str
    records: list[DiagnosticsRecord] = field(default_factory=list)
    folder_records: list[FolderReportRecord] = field(default_factory=list)


def run_repertory_diagnostics(
    config: DiagnosticsConfig,
    *,
    progress_callback: DiagnosticsProgressCallback | None = None,
    log_callback: DiagnosticsLogCallback | None = None,
    cancel_event: object | None = None,
) -> DiagnosticsResult:
    started_at = time.monotonic()

    split_root = Path(config.split_repertory_dir).expanduser().resolve()
    general_root = Path(config.general_repertory_dir).expanduser().resolve()
    diagnosis_base = _resolve_diagnosis_root(general_root, config.results_dir)
    session_root = diagnosis_base / f"{DIAGNOSTICS_SESSION_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    missing_in_general_flat_output_root = session_root / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
    missing_in_split_flat_output_root = session_root / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL
    report_root = session_root / "Report"
    log_root = session_root / "Log"

    for folder in (missing_in_general_flat_output_root, missing_in_split_flat_output_root, report_root, log_root):
        folder.mkdir(parents=True, exist_ok=True)

    logs: list[str] = []

    def _log(message: str) -> None:
        stamped = f"{datetime.now().strftime('%H:%M:%S')}  {message}"
        logs.append(stamped)
        if log_callback is not None:
            log_callback(message)

    _log(f"[DIAGNOSI REPERTORIO] Avvio scansione Repertorio suddiviso | path={split_root}")
    _log(f"[DIAGNOSI REPERTORIO] Avvio scansione Cartella Repertorio Generale | path={general_root}")

    folder_nodes = enumerate_split_repertory_nodes(
        split_root,
        general_root,
        diagnosis_base.parent,
    )
    split_files = _scan_split_repertory(
        split_root,
        exclude_roots=[general_root, diagnosis_base],
        selected_relative_roots=config.selected_relative_roots,
        excluded_relative_roots=config.excluded_relative_roots,
        include_root_files=bool(config.include_root_files),
    )
    general_files = _scan_general_repertory(general_root)

    # Protective coherence check: if selected folders contain detectable MP3 but
    # split scan yields zero files, abort before producing unreliable mass mismatch output.
    precheck_folder_records = _build_folder_records(
        root=split_root,
        nodes=folder_nodes,
        split_files=[],
        selected_relative_roots=config.selected_relative_roots,
        excluded_relative_roots=config.excluded_relative_roots,
        include_root_files=bool(config.include_root_files),
    )
    selected_detected_mp3 = sum(
        int(record.mp3_detected)
        for record in precheck_folder_records
        if record.status == FOLDER_STATUS_SELECTED
    )
    if selected_detected_mp3 > 0 and not split_files:
        raise DiagnosticsError(
            "Non e stato elaborato alcun file del Repertorio suddiviso, nonostante le cartelle "
            "selezionate contengano file MP3. Verificare la selezione delle cartelle o la scansione."
        )

    split_index = _index_by_normalized_name(split_files)
    general_index = _index_by_normalized_name(general_files)
    all_normalized_names = sorted(set(split_index) | set(general_index))

    total_groups = len(all_normalized_names)
    processed = 0
    records: list[DiagnosticsRecord] = []
    counters = {
        "matched_both": 0,
        "only_general": 0,
        "only_split": 0,
        "split_duplicates": 0,
        "split_duplicate_extra_occurrences": 0,
        "read_errors": 0,
        "copied_files": 0,
        "copy_errors": 0,
    }

    interrupted = False
    for normalized_name in all_normalized_names:
        if _is_cancelled(cancel_event):
            interrupted = True
            records.append(
                _record_for_interrupt(normalized_name, len(split_index.get(normalized_name, [])), len(general_index.get(normalized_name, [])))
            )
            break

        split_group = split_index.get(normalized_name, [])
        general_group = general_index.get(normalized_name, [])

        try:
            record, summary = _classify_group(
                normalized_name=normalized_name,
                split_group=split_group,
                general_group=general_group,
                missing_in_general_flat_output_root=missing_in_general_flat_output_root,
                missing_in_split_flat_output_root=missing_in_split_flat_output_root,
                log=_log,
            )
        except DiagnosticsError as error:
            counters["read_errors"] += 1
            records.append(
                DiagnosticsRecord(
                    stato=DiagnosticsStatus.ERRORE_LETTURA.value,
                    file=normalized_name,
                    nome_normalizzato=normalized_name,
                    presente_in_cartella_generale="NO",
                    percorso_cartella_generale="",
                    presente_in_repertorio_suddiviso="NO",
                    percorso_repertorio_suddiviso="",
                    numero_occorrenze_nel_repertorio_suddiviso=str(len(split_group)),
                    percorsi_duplicati="",
                    dimensione_file_cartella_generale="",
                    dimensione_file_repertorio="",
                    data_ora_file_cartella_generale="",
                    data_ora_file_repertorio="",
                    durata_file_cartella_generale="",
                    durata_file_repertorio="",
                    confronto_contenuto="",
                    percorso_copia_diagnosi="",
                    esito_copia_diagnosi="Errore",
                    motivo_note="Errore di lettura o confronto",
                    dettaglio_errore=str(error),
                )
            )
            _log(f"[DIAGNOSI REPERTORIO] Errore lettura: {normalized_name} | {error}")
        else:
            records.append(record)
            for key, value in summary.items():
                counters[key] += int(value)

        processed += 1
        _emit_progress(progress_callback, processed, total_groups, normalized_name)

    is_perfect_alignment = (
        not interrupted
        and counters["only_general"] == 0
        and counters["only_split"] == 0
        and counters["split_duplicates"] == 0
        and counters["read_errors"] == 0
        and counters["copy_errors"] == 0
    )

    folder_records = _build_folder_records(
        root=split_root,
        nodes=folder_nodes,
        split_files=split_files,
        selected_relative_roots=config.selected_relative_roots,
        excluded_relative_roots=config.excluded_relative_roots,
        include_root_files=bool(config.include_root_files),
    )
    report_paths, folder_report_paths = _write_reports(report_root, session_root.name, records, folder_records)
    log_path = log_root / f"{session_root.name}.log"
    _write_log(log_path, logs)

    return DiagnosticsResult(
        success=not interrupted and counters["read_errors"] == 0,
        interrupted=interrupted,
        error=None,
        analyzed_general_files=len(general_files),
        analyzed_split_files=len(split_files),
        general_unique_titles=len(general_index),
        split_unique_titles=len(split_index),
        matched_both=counters["matched_both"],
        only_general=counters["only_general"],
        only_split=counters["only_split"],
        split_duplicates=counters["split_duplicates"],
        split_duplicate_extra_occurrences=counters["split_duplicate_extra_occurrences"],
        read_errors=counters["read_errors"],
        copied_files=counters["copied_files"],
        copy_errors=counters["copy_errors"],
        is_perfect_alignment=is_perfect_alignment,
        duration_seconds=max(0.0, time.monotonic() - started_at),
        session_folder=str(session_root),
        diagnosis_root=str(diagnosis_base),
        report_paths=report_paths,
        folder_report_paths=folder_report_paths,
        log_path=str(log_path),
        records=records,
        folder_records=folder_records,
    )


def _classify_group(
    *,
    normalized_name: str,
    split_group: list[Path],
    general_group: list[Path],
    missing_in_general_flat_output_root: Path,
    missing_in_split_flat_output_root: Path,
    log: DiagnosticsLogCallback,
) -> tuple[DiagnosticsRecord, dict[str, int]]:
    split_count = len(split_group)
    general_count = len(general_group)

    if split_count and general_count:
        if split_count > 1:
            record = _duplicate_record(normalized_name, split_group, general_group)
            # Duplicates that are also present in the general repertory are tracked in reports only.
            # They must not be routed into "missing" diagnostic folders.
            return record, {
                "matched_both": 1,
                "split_duplicates": 1,
                "split_duplicate_extra_occurrences": max(0, split_count - 1),
            }

        split_file = split_group[0]
        general_file = general_group[0]
        split_info = _file_info(split_file)
        general_info = _file_info(general_file)
        same_content = _same_content(split_info, general_info)
        record = DiagnosticsRecord(
            stato=DiagnosticsStatus.PRESENTE_ENTRAMBI.value,
            file=split_file.name,
            nome_normalizzato=normalized_name,
            presente_in_cartella_generale="SI",
            percorso_cartella_generale=str(general_file),
            presente_in_repertorio_suddiviso="SI",
            percorso_repertorio_suddiviso=str(split_file),
            numero_occorrenze_nel_repertorio_suddiviso=str(split_count),
            percorsi_duplicati="",
            dimensione_file_cartella_generale=str(general_info[0]),
            dimensione_file_repertorio=str(split_info[0]),
            data_ora_file_cartella_generale=_format_timestamp(general_info[2]),
            data_ora_file_repertorio=_format_timestamp(split_info[2]),
            durata_file_cartella_generale="",
            durata_file_repertorio="",
            confronto_contenuto="Identico" if same_content else "Differente",
            percorso_copia_diagnosi="",
            esito_copia_diagnosi="Non applicabile",
            motivo_note="Presente in entrambi",
            dettaglio_errore="",
        )
        if same_content:
            log(f"[DIAGNOSI REPERTORIO] Presente in entrambi: {split_file}")
        else:
            log(f"[DIAGNOSI REPERTORIO] Presente in entrambi ma contenuto differente: {split_file}")
        return record, {"matched_both": 1}

    if general_count and not split_count:
        selected_general_file = _choose_deterministic_source(general_group)
        copied, copy_errors = _copy_many(
            general_group,
            missing_in_split_flat_output_root,
            log,
            normalized_name,
            preserve_relative=False,
        )
        copy_outcome = _copy_outcome(total_requested=len(general_group), copied_count=len(copied), error_count=copy_errors)
        first_info = _file_info(selected_general_file)
        record = DiagnosticsRecord(
            stato=DiagnosticsStatus.SOLO_GENERALE.value,
            file=selected_general_file.name,
            nome_normalizzato=normalized_name,
            presente_in_cartella_generale="SI",
            percorso_cartella_generale=str(selected_general_file),
            presente_in_repertorio_suddiviso="NO",
            percorso_repertorio_suddiviso="",
            numero_occorrenze_nel_repertorio_suddiviso="0",
            percorsi_duplicati="",
            dimensione_file_cartella_generale=str(first_info[0]),
            dimensione_file_repertorio="",
            data_ora_file_cartella_generale=_format_timestamp(first_info[2]),
            data_ora_file_repertorio="",
            durata_file_cartella_generale="",
            durata_file_repertorio="",
            confronto_contenuto="Non confrontabile",
            percorso_copia_diagnosi=" | ".join(str(path) for path in copied),
            esito_copia_diagnosi=copy_outcome,
            motivo_note="Presente solo nella Cartella Repertorio Generale",
            dettaglio_errore=(f"Errori copia: {copy_errors}" if copy_errors > 0 else ""),
        )
        log(f"[DIAGNOSI REPERTORIO] Presente solo nella Cartella Generale: {selected_general_file}")
        return record, {"only_general": 1, "copied_files": len(copied), "copy_errors": copy_errors}

    if split_count and not general_count:
        selected_split_file = _choose_deterministic_source(split_group)
        differing_homonyms = split_count > 1 and _group_has_different_content(split_group)
        copied, copy_errors = _copy_many(
            [selected_split_file],
            missing_in_general_flat_output_root,
            log,
            normalized_name,
            preserve_relative=False,
        )
        copy_outcome = _copy_outcome(total_requested=1, copied_count=len(copied), error_count=copy_errors)
        first_info = _file_info(selected_split_file)
        state = DiagnosticsStatus.SOLO_SUDDIVISO.value if split_count == 1 else DiagnosticsStatus.DUPLICATO_SUDDIVISO.value
        if split_count > 1:
            note = "Duplicato nel Repertorio suddiviso"
            if differing_homonyms:
                note += " - omonimi con contenuto differente"
        else:
            note = "Presente solo nel Repertorio suddiviso"
        record = DiagnosticsRecord(
            stato=state,
            file=selected_split_file.name,
            nome_normalizzato=normalized_name,
            presente_in_cartella_generale="NO",
            percorso_cartella_generale="",
            presente_in_repertorio_suddiviso="SI",
            percorso_repertorio_suddiviso=str(selected_split_file),
            numero_occorrenze_nel_repertorio_suddiviso=str(split_count),
            percorsi_duplicati=" | ".join(str(path) for path in split_group),
            dimensione_file_cartella_generale="",
            dimensione_file_repertorio=str(first_info[0]),
            data_ora_file_cartella_generale="",
            data_ora_file_repertorio=_format_timestamp(first_info[2]),
            durata_file_cartella_generale="",
            durata_file_repertorio="",
            confronto_contenuto="Duplicato" if split_count > 1 else "Non confrontabile",
            percorso_copia_diagnosi=" | ".join(str(path) for path in copied),
            esito_copia_diagnosi=copy_outcome,
            motivo_note=note,
            dettaglio_errore=(f"Errori copia: {copy_errors}" if copy_errors > 0 else ""),
        )
        log(
            "[DIAGNOSI REPERTORIO] Presente solo nel Repertorio suddiviso: "
            f"{selected_split_file} | scelta_deterministica=true"
        )
        if split_count > 1:
            log(f"[DIAGNOSI REPERTORIO] Duplicato nel Repertorio: {normalized_name} | occorrenze={split_count}")
            if differing_homonyms:
                log(
                    "[DIAGNOSI REPERTORIO] Anomalia omonimi con contenuto differente: "
                    f"{normalized_name}"
                )
            return record, {
                "split_duplicates": 1,
                "split_duplicate_extra_occurrences": max(0, split_count - 1),
                "only_split": 1,
                "copied_files": len(copied),
                "copy_errors": copy_errors,
            }
        return record, {"only_split": 1, "copied_files": len(copied), "copy_errors": copy_errors}

    raise DiagnosticsError(f"Gruppo non classificabile: {normalized_name}")


def _duplicate_record(normalized_name: str, split_group: list[Path], general_group: list[Path]) -> DiagnosticsRecord:
    first = split_group[0]
    first_info = _file_info(first)
    general_info = _file_info(general_group[0]) if general_group else None
    content = "Identici" if general_info is not None and _same_content(first_info, general_info) else "Differenti o non confrontabili"
    return DiagnosticsRecord(
        stato=DiagnosticsStatus.DUPLICATO_SUDDIVISO.value,
        file=first.name,
        nome_normalizzato=normalized_name,
        presente_in_cartella_generale="SI" if general_group else "NO",
        percorso_cartella_generale=str(general_group[0]) if general_group else "",
        presente_in_repertorio_suddiviso="SI",
        percorso_repertorio_suddiviso=str(first),
        numero_occorrenze_nel_repertorio_suddiviso=str(len(split_group)),
        percorsi_duplicati=" | ".join(str(path) for path in split_group),
        dimensione_file_cartella_generale=str(general_info[0]) if general_info else "",
        dimensione_file_repertorio=str(first_info[0]),
        data_ora_file_cartella_generale=_format_timestamp(general_info[2]) if general_info else "",
        data_ora_file_repertorio=_format_timestamp(first_info[2]),
        durata_file_cartella_generale="",
        durata_file_repertorio="",
        confronto_contenuto=content,
        percorso_copia_diagnosi="",
        esito_copia_diagnosi="Non applicabile",
        motivo_note=(
            "Presente in entrambi e duplicato nel Repertorio suddiviso"
            if general_group else
            "Duplicato nel Repertorio suddiviso"
        ),
        dettaglio_errore="",
    )


def _copy_many(
    files: list[Path],
    destination_root: Path,
    log: DiagnosticsLogCallback,
    normalized_name: str,
    *,
    preserve_relative: bool,
) -> tuple[list[Path], int]:
    copied: list[Path] = []
    errors = 0
    for file_path in files:
        try:
            if preserve_relative:
                relative = _best_relative_name(file_path)
                destination = destination_root / relative
            else:
                destination = destination_root / file_path.name
            copied_path = _copy_with_suffix(file_path, destination)
            copied.append(copied_path)
            log(f"[DIAGNOSI REPERTORIO] Copiato in diagnosi: {copied_path}")
        except Exception as error:
            errors += 1
            log(f"[DIAGNOSI REPERTORIO] Errore copia: {normalized_name} | {error}")
    return copied, errors


def _copy_outcome(*, total_requested: int, copied_count: int, error_count: int) -> str:
    if total_requested <= 0:
        return "Non applicabile"
    if error_count <= 0 and copied_count == total_requested:
        return "Copiato"
    if copied_count > 0 and error_count > 0:
        return "Copiato parziale"
    if copied_count <= 0 and error_count > 0:
        return "Errore copia"
    return "Copiato"


def _copy_with_suffix(source_path: Path, destination_path: Path) -> Path:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    base_name = destination_path.name
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix
    candidate = destination_path
    index = 1
    while candidate.exists():
        candidate = destination_path.with_name(f"{stem} ({index}){suffix}")
        index += 1
    shutil.copy2(source_path, candidate)
    return candidate


def _choose_deterministic_source(paths: list[Path]) -> Path:
    if not paths:
        raise DiagnosticsError("Nessun file disponibile per la selezione deterministica.")
    ordered = sorted(paths, key=lambda item: str(item).casefold())
    return ordered[0]


def _group_has_different_content(paths: list[Path]) -> bool:
    if len(paths) <= 1:
        return False
    signatures: set[tuple[int, str]] = set()
    for file_path in paths:
        size, digest, _mtime = _file_info(file_path)
        signatures.add((size, digest))
        if len(signatures) > 1:
            return True
    return False


def _file_info(path: Path) -> tuple[int, str, float]:
    stat_result = path.stat()
    return int(stat_result.st_size), _sha256(path), float(stat_result.st_mtime)


def _same_content(left: tuple[int, str, float], right: tuple[int, str, float]) -> bool:
    return left[0] == right[0] and left[1] == right[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _best_relative_name(path: Path) -> Path:
    parts = path.parts
    if len(parts) <= 1:
        return Path(path.name)
    return Path(*parts[-3:])


def _normalize_name(file_name: str) -> str:
    import unicodedata

    name = unicodedata.normalize("NFC", str(file_name or ""))
    name = name.strip()
    name = name.casefold()
    return name


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return ""


def _scan_general_repertory(root: Path) -> list[Path]:
    return sorted([path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".mp3"], key=lambda item: item.name.casefold())


def _scan_split_repertory(
    root: Path,
    *,
    exclude_roots: list[Path],
    selected_relative_roots: tuple[str, ...] = (),
    excluded_relative_roots: tuple[str, ...] = (),
    include_root_files: bool = True,
) -> list[Path]:
    excluded = [_resolve_path(path) for path in exclude_roots]
    selected_roots = _sanitize_relative_roots(selected_relative_roots)
    excluded_roots_rel = _sanitize_relative_roots(excluded_relative_roots)
    found: list[Path] = []
    for current_root, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root)
        resolved_current = _resolve_path(current_path)
        relative_current = _relative_posix(current_path, root)
        if _is_relative_path_excluded(relative_current, excluded_roots_rel):
            if selected_roots and _has_selected_relative_descendant(relative_current, selected_roots):
                pass
            else:
                dirs[:] = []
                continue
        if _is_path_in_excluded_roots(resolved_current, excluded):
            dirs[:] = []
            continue

        pruned: list[str] = []
        for directory in dirs:
            candidate = current_path / directory
            resolved_candidate = _resolve_path(candidate)
            candidate_relative = _relative_posix(candidate, root)
            if _is_relative_path_excluded(candidate_relative, excluded_roots_rel):
                if selected_roots and _has_selected_relative_descendant(candidate_relative, selected_roots):
                    pruned.append(directory)
                    continue
                continue
            if _is_path_in_excluded_roots(resolved_candidate, excluded):
                continue
            if _is_reparse_point(candidate):
                continue
            pruned.append(directory)
        dirs[:] = pruned

        for file_name in files:
            candidate = current_path / file_name
            if candidate.suffix.lower() != ".mp3":
                continue
            if _is_reparse_point(candidate):
                continue
            if not include_root_files and relative_current == "":
                continue
            if not _is_relative_path_selected(relative_current, selected_roots):
                continue
            found.append(candidate)
    found.sort(key=lambda item: item.relative_to(root).as_posix().casefold())
    return found


def _index_by_normalized_name(files: list[Path]) -> dict[str, list[Path]]:
    indexed: dict[str, list[Path]] = {}
    for file_path in files:
        indexed.setdefault(_normalize_name(file_path.name), []).append(file_path)
    for key in indexed:
        indexed[key].sort(key=lambda item: item.as_posix().casefold())
    return indexed


def _resolve_diagnosis_root(general_root: Path, results_dir: str | Path | None) -> Path:
    if results_dir is not None and str(results_dir).strip():
        resolved = Path(results_dir).expanduser().resolve()
        if not _is_same_or_child(resolved, general_root):
            raise DiagnosticsError("La cartella Diagnosi deve risiedere all'interno della Cartella Repertorio Generale o di una sua sottocartella.")
        return resolved / DIAGNOSTICS_FOLDER_NAME
    return general_root / DIAGNOSTICS_FOLDER_NAME


def enumerate_split_repertory_nodes(
    split_root: str | Path,
    general_root: str | Path,
    results_dir: str | Path | None = None,
) -> list[SplitFolderNode]:
    split_root_path = Path(split_root).expanduser().resolve()
    general_root_path = Path(general_root).expanduser().resolve()
    diagnosis_root = _resolve_diagnosis_root(general_root_path, results_dir)

    nodes: list[SplitFolderNode] = []
    root_mp3_detected = 0
    try:
        for direct_file in split_root_path.iterdir():
            if direct_file.is_file() and direct_file.suffix.lower() == ".mp3":
                root_mp3_detected += 1
    except OSError:
        root_mp3_detected = 0

    nodes.append(
        SplitFolderNode(
            relative_path=ROOT_FILES_TOKEN,
            full_path=str(split_root_path),
            level=0,
            auto_excluded=False,
            auto_exclusion_reason="",
            selectable=True,
            is_virtual_root_files=True,
            mp3_detected=int(root_mp3_detected),
        )
    )

    for current_root, dirs, _files in os.walk(split_root_path, topdown=True, followlinks=False):
        current_path = Path(current_root)
        relative = _relative_posix(current_path, split_root_path)

        pruned_dirs: list[str] = []
        for directory in dirs:
            candidate = current_path / directory
            if _is_reparse_point(candidate):
                continue

            auto_excluded, reason = _compute_auto_exclusion(candidate, general_root_path, diagnosis_root)
            candidate_relative = _relative_posix(candidate, split_root_path)
            level = 0 if not candidate_relative else candidate_relative.count("/") + 1
            nodes.append(
                SplitFolderNode(
                    relative_path=candidate_relative,
                    full_path=str(candidate),
                    level=level,
                    auto_excluded=auto_excluded,
                    auto_exclusion_reason=reason,
                    selectable=not auto_excluded,
                    is_virtual_root_files=False,
                    mp3_detected=0,
                )
            )
            if auto_excluded:
                continue
            pruned_dirs.append(directory)

        dirs[:] = pruned_dirs

    nodes.sort(key=lambda node: (node.level, node.relative_path.casefold()))
    return nodes


def _compute_auto_exclusion(candidate: Path, general_root: Path, diagnosis_root: Path) -> tuple[bool, str]:
    name = candidate.name.strip().casefold()
    if name in AUTO_EXCLUDED_FOLDER_NAMES:
        return True, "Cartella esclusa automaticamente"
    if DIAGNOSI_SESSION_RE.match(candidate.name.strip()):
        return True, "Sessione diagnosi esclusa automaticamente"

    resolved_candidate = _resolve_path(candidate)
    resolved_general = _resolve_path(general_root)
    resolved_diagnosis = _resolve_path(diagnosis_root)
    if _is_same_or_child(resolved_candidate, resolved_general) or _is_same_or_child(resolved_general, resolved_candidate):
        return True, "Percorso repertorio generale escluso automaticamente"
    if _is_same_or_child(resolved_candidate, resolved_diagnosis) or _is_same_or_child(resolved_diagnosis, resolved_candidate):
        return True, "Percorso diagnosi escluso automaticamente"
    return False, ""


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


def _sanitize_relative_roots(items: tuple[str, ...] | list[str]) -> set[str]:
    normalized: set[str] = set()
    for item in items:
        value = str(item or "").strip().replace("\\", "/").strip("/")
        if not value or value == ROOT_FILES_TOKEN:
            continue
        normalized.add(value.casefold())
    return normalized


def _is_relative_path_excluded(relative_path: str, excluded_roots: set[str]) -> bool:
    if not excluded_roots:
        return False
    value = str(relative_path or "").strip().replace("\\", "/").strip("/").casefold()
    for root in excluded_roots:
        if value == root or value.startswith(root + "/"):
            return True
    return False


def _is_relative_path_selected(relative_path: str, selected_roots: set[str]) -> bool:
    if not selected_roots:
        return True
    value = str(relative_path or "").strip().replace("\\", "/").strip("/").casefold()
    for root in selected_roots:
        if value == root or value.startswith(root + "/"):
            return True
    return False


def _has_selected_relative_descendant(relative_path: str, selected_roots: set[str]) -> bool:
    if not selected_roots:
        return False
    value = str(relative_path or "").strip().replace("\\", "/").strip("/").casefold()
    if not value:
        return True
    for root in selected_roots:
        if root == value or root.startswith(value + "/"):
            return True
    return False


def _build_folder_records(
    *,
    root: Path,
    nodes: list[SplitFolderNode],
    split_files: list[Path],
    selected_relative_roots: tuple[str, ...],
    excluded_relative_roots: tuple[str, ...],
    include_root_files: bool,
) -> list[FolderReportRecord]:
    selected_roots = _sanitize_relative_roots(selected_relative_roots)
    excluded_roots = _sanitize_relative_roots(excluded_relative_roots)

    detected_counts: dict[str, int] = {}
    processed_counts: dict[str, int] = {}

    for current_root, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root)
        relative = _relative_posix(current_path, root)
        pruned_dirs: list[str] = []
        for directory in dirs:
            candidate = current_path / directory
            if _is_reparse_point(candidate):
                continue
            pruned_dirs.append(directory)
        dirs[:] = pruned_dirs

        mp3_count = 0
        for file_name in files:
            candidate = current_path / file_name
            if candidate.suffix.lower() == ".mp3" and not _is_reparse_point(candidate):
                mp3_count += 1
        detected_counts[relative] = mp3_count

    for split_file in split_files:
        relative = _relative_posix(split_file.parent, root)
        processed_counts[relative] = processed_counts.get(relative, 0) + 1

    records: list[FolderReportRecord] = []
    root_detected = int(detected_counts.get("", 0))
    root_processed = int(processed_counts.get("", 0))
    root_selected = bool(include_root_files)
    root_status = FOLDER_STATUS_SELECTED if root_selected else FOLDER_STATUS_NOT_SELECTED
    root_reason = "Selezione utente" if root_selected else "Deselezionata dall'utente"
    records.append(
        FolderReportRecord(
            relative_path=ROOT_FILES_TOKEN,
            full_path=str(root),
            level=0,
            status=root_status,
            reason=root_reason,
            mp3_detected=root_detected,
            mp3_processed=root_processed,
        )
    )

    for node in nodes:
        if node.is_virtual_root_files:
            continue

        rel_key = str(node.relative_path or "")
        rel_key_folded = rel_key.casefold()
        if node.auto_excluded:
            status = FOLDER_STATUS_AUTO_EXCLUDED
            reason = node.auto_exclusion_reason or "Cartella esclusa automaticamente"
        else:
            selected = True
            if rel_key_folded in excluded_roots:
                selected = False
            elif selected_roots:
                selected = _is_relative_path_selected(rel_key, selected_roots)
            status = FOLDER_STATUS_SELECTED if selected else FOLDER_STATUS_NOT_SELECTED
            reason = "Selezione utente" if selected else "Deselezionata dall'utente"

        records.append(
            FolderReportRecord(
                relative_path=rel_key,
                full_path=node.full_path,
                level=int(node.level),
                status=status,
                reason=reason,
                mp3_detected=int(detected_counts.get(rel_key, 0)),
                mp3_processed=int(processed_counts.get(rel_key, 0)),
            )
        )

    records.sort(key=lambda item: (item.level, item.relative_path.casefold()))
    return records


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser()


def _is_path_in_excluded_roots(path: Path, excluded_roots: list[Path]) -> bool:
    # Exclude only the target folder itself or its descendants.
    # Do not exclude ancestors of the target (e.g. split root containing general root).
    return any(_is_same_or_child(path, excluded_root) for excluded_root in excluded_roots)


def _is_same_or_child(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_point and (attributes & reparse_point) == reparse_point)


def _is_cancelled(cancel_event: object | None) -> bool:
    return bool(cancel_event is not None and hasattr(cancel_event, "is_set") and callable(getattr(cancel_event, "is_set")) and cancel_event.is_set())


def _emit_progress(callback: DiagnosticsProgressCallback | None, current: int, total: int, message: str) -> None:
    if callback is not None:
        callback(current, total, message)


def _write_reports(
    report_root: Path,
    session_name: str,
    records: list[DiagnosticsRecord],
    folder_records: list[FolderReportRecord],
) -> tuple[dict[str, str], dict[str, str]]:
    report_root.mkdir(parents=True, exist_ok=True)
    csv_path = report_root / f"{session_name}.csv"
    folder_csv_path = report_root / f"{session_name}_cartelle_controllate.csv"
    xlsx_path = report_root / f"{session_name}.xlsx"
    html_path = report_root / f"{session_name}.html"
    rows = [record.to_row() for record in records]
    folder_rows = [record.to_row() for record in folder_records]
    _write_csv(csv_path, rows)
    _write_folder_csv(folder_csv_path, folder_rows)
    _write_xlsx(xlsx_path, rows, folder_rows)
    _write_html(html_path, rows, folder_rows)
    return (
        {"csv": str(csv_path), "xlsx": str(xlsx_path), "html": str(html_path)},
        {"csv": str(folder_csv_path), "xlsx": str(xlsx_path), "html": str(html_path)},
    )


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([REPORT_HEADER_LABELS.get(key, key) for key in REPORT_HEADERS])
        for row in rows:
            writer.writerow([row.get(key, "") for key in REPORT_HEADERS])


def _write_folder_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([FOLDER_REPORT_HEADER_LABELS.get(key, key) for key in FOLDER_REPORT_HEADERS])
        for row in rows:
            writer.writerow([row.get(key, "") for key in FOLDER_REPORT_HEADERS])


def _write_html(path: Path, rows: list[dict[str, str]], folder_rows: list[dict[str, str]]) -> None:
    headers = [REPORT_HEADER_LABELS.get(key, key) for key in REPORT_HEADERS]
    lines = [
        "<!doctype html>",
        "<html lang='it'>",
        "<head>",
        "<meta charset='utf-8'>",
        f"<title>{DIAGNOSTICS_FOLDER_NAME}</title>",
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:20px;color:#1f2937}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d1d5db;padding:6px;vertical-align:top;white-space:pre-wrap}th{background:#f3f4f6}.ok{background:#ecfdf5}.warn{background:#fffbeb}.err{background:#fef2f2}.dup{background:#eff6ff}</style>",
        "</head><body>",
        f"<h1>{DIAGNOSTICS_FOLDER_NAME}</h1>",
        f"<p>Generato: {datetime.now().isoformat(timespec='seconds')}</p>",
        "<table><thead><tr>",
    ]
    for header in headers:
        lines.append(f"<th>{html.escape(header)}</th>")
    lines.append("</tr></thead><tbody>")
    for row in rows:
        cls = _html_row_class(row.get("stato", ""))
        lines.append(f"<tr class='{cls}'>")
        for key in REPORT_HEADERS:
            lines.append(f"<td>{html.escape(str(row.get(key, '')))}</td>")
        lines.append("</tr>")

    lines.extend([
        "</tbody></table>",
        "<h2>Alberatura repertorio e selezione</h2>",
        "<table><thead><tr>",
    ])
    for key in FOLDER_REPORT_HEADERS:
        header = FOLDER_REPORT_HEADER_LABELS.get(key, key)
        lines.append(f"<th>{html.escape(header)}</th>")
    lines.append("</tr></thead><tbody>")
    for row in folder_rows:
        lines.append("<tr>")
        for key in FOLDER_REPORT_HEADERS:
            lines.append(f"<td>{html.escape(str(row.get(key, '')))}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table></body></html>")
    path.write_text("".join(lines), encoding="utf-8")


def _html_row_class(status: str) -> str:
    if status == DiagnosticsStatus.ERRORE_LETTURA.value:
        return "err"
    if status == DiagnosticsStatus.DUPLICATO_SUDDIVISO.value:
        return "dup"
    if status in {DiagnosticsStatus.SOLO_GENERALE.value, DiagnosticsStatus.SOLO_SUDDIVISO.value}:
        return "warn"
    return "ok"


def _write_xlsx(path: Path, rows: list[dict[str, str]], folder_rows: list[dict[str, str]]) -> None:
    headers = [REPORT_HEADER_LABELS.get(key, key) for key in REPORT_HEADERS]
    content_types = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        "<Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
        "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
        "<Override PartName='/xl/worksheets/sheet2.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
        "<Override PartName='/xl/styles.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'/>"
        "</Types>"
    )
    rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument' Target='xl/workbook.xml'/>"
        "</Relationships>"
    )
    workbook = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
        "<sheets>"
        "<sheet name='Diagnosi' sheetId='1' r:id='rId1'/>"
        "<sheet name='Cartelle controllate' sheetId='2' r:id='rId2'/>"
        "</sheets></workbook>"
    )
    workbook_rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/>"
        "<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet2.xml'/>"
        "<Relationship Id='rId3' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles' Target='styles.xml'/>"
        "</Relationships>"
    )
    styles = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<styleSheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<fonts count='2'><font><sz val='11'/><name val='Calibri'/></font><font><b/><sz val='11'/><name val='Calibri'/></font></fonts>"
        "<fills count='2'><fill><patternFill patternType='none'/></fill><fill><patternFill patternType='gray125'/></fill></fills>"
        "<borders count='1'><border><left/><right/><top/><bottom/><diagonal/></border></borders>"
        "<cellStyleXfs count='1'><xf numFmtId='0' fontId='0' fillId='0' borderId='0'/></cellStyleXfs>"
        "<cellXfs count='3'>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='0' xfId='0'/>"
        "<xf numFmtId='0' fontId='1' fillId='0' borderId='0' xfId='0' applyFont='1' applyAlignment='1'><alignment wrapText='1' vertical='top'/></xf>"
        "<xf numFmtId='0' fontId='0' fillId='0' borderId='0' xfId='0' applyAlignment='1'><alignment wrapText='1' vertical='top'/></xf>"
        "</cellXfs>"
        "<cellStyles count='1'><cellStyle name='Normal' xfId='0' builtinId='0'/></cellStyles>"
        "</styleSheet>"
    )
    sheet_xml = _build_sheet_xml(headers, rows)
    folder_sheet_xml = _build_sheet_xml(
        [FOLDER_REPORT_HEADER_LABELS.get(key, key) for key in FOLDER_REPORT_HEADERS],
        folder_rows,
        FOLDER_REPORT_HEADERS,
    )

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/worksheets/sheet2.xml", folder_sheet_xml)


def _build_sheet_xml(headers: list[str], rows: list[dict[str, str]], keys: list[str] | None = None) -> str:
    ordered_keys = list(keys or REPORT_HEADERS)
    lines = [
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>",
        "<sheetViews><sheetView workbookViewId='0'><pane ySplit='1' topLeftCell='A2' activePane='bottomLeft' state='frozen'/></sheetView></sheetViews>",
        "<sheetData>",
        "<row r='1'>",
    ]
    for index, header in enumerate(headers, start=1):
        lines.append(f"<c r='{_xlsx_col(index)}1' s='1' t='inlineStr'><is><t>{html.escape(header)}</t></is></c>")
    lines.append("</row>")
    for row_index, row in enumerate(rows, start=2):
        lines.append(f"<row r='{row_index}'>")
        for col_index, key in enumerate(ordered_keys, start=1):
            lines.append(f"<c r='{_xlsx_col(col_index)}{row_index}' s='2' t='inlineStr'><is><t>{html.escape(str(row.get(key, '')))}</t></is></c>")
        lines.append("</row>")
    lines.append("</sheetData>")
    lines.append(f"<autoFilter ref='A1:{_xlsx_col(len(headers))}{max(1, len(rows) + 1)}'/>")
    lines.append("</worksheet>")
    return "".join(lines)


def _xlsx_col(index: int) -> str:
    col = ""
    value = index
    while value > 0:
        value, rem = divmod(value - 1, 26)
        col = chr(65 + rem) + col
    return col


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _record_for_interrupt(normalized_name: str, split_count: int, general_count: int) -> DiagnosticsRecord:
    return DiagnosticsRecord(
        stato=DiagnosticsStatus.INTERROTTO.value,
        file=normalized_name,
        nome_normalizzato=normalized_name,
        presente_in_cartella_generale="SI" if general_count else "NO",
        percorso_cartella_generale="",
        presente_in_repertorio_suddiviso="SI" if split_count else "NO",
        percorso_repertorio_suddiviso="",
        numero_occorrenze_nel_repertorio_suddiviso=str(split_count),
        percorsi_duplicati="",
        dimensione_file_cartella_generale="",
        dimensione_file_repertorio="",
        data_ora_file_cartella_generale="",
        data_ora_file_repertorio="",
        durata_file_cartella_generale="",
        durata_file_repertorio="",
        confronto_contenuto="",
        percorso_copia_diagnosi="",
        esito_copia_diagnosi="Interrotto",
        motivo_note="Diagnosi interrotta",
        dettaglio_errore="",
    )