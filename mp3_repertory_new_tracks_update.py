# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import os
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zipfile import ZIP_DEFLATED, ZipFile

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]
DecisionCallback = Callable[[dict[str, Any]], str | None]

DECISION_UPDATE_CURRENT = "UPDATE_CURRENT"
DECISION_SKIP_CURRENT = "SKIP_CURRENT"
DECISION_UPDATE_AND_BYPASS_SESSION = "UPDATE_AND_BYPASS_SESSION"
DECISION_SKIP_AND_BYPASS_SESSION = "SKIP_AND_BYPASS_SESSION"

SESSION_POLICY_ASK = "ASK"
SESSION_POLICY_UPDATE_ALL = "UPDATE_ALL"
SESSION_POLICY_SKIP_ALL = "SKIP_ALL"

DEST_SPLIT = "Repertorio Suddiviso"
DEST_GENERAL = "Repertorio Generale"
DEST_ANDROID = "Android"
REPERTORY_GENERAL_TECH_FOLDER_NAME = "REPERTORIO_GENERALE_DA_MIXCREATOR"

SESSION_FOLDER_PREFIX = "Inserimento_Nuovi_Brani_"
REPORT_SUBFOLDER_NAME = "Report"
BACKUP_SUBFOLDER_NAME = "Backup"
LOG_SUBFOLDER_NAME = "Log"
BACKUP_SPLIT_FOLDER_NAME = "BACKUP_REPERTORIO_SUDDIVISO"
BACKUP_GENERAL_FOLDER_NAME = "BACKUP_REPERTORIO_GENERALE_DA_MIXCREATOR"
BACKUP_ANDROID_FOLDER_NAME = "BACKUP_ANDROID"
REPORT_FILE_STEM = "Inserimento_Nuovi_Brani"

COUNTER_TRACKS_PROCESSED = "tracks_processed"
COUNTER_TRACKS_INSERTED = "tracks_inserted"
COUNTER_TRACKS_UPDATED = "tracks_updated"
COUNTER_TRACKS_KEPT = "tracks_kept"
COUNTER_TRACKS_SKIPPED = "tracks_skipped"
COUNTER_TRACKS_ERRORS = "tracks_errors"
COUNTER_SPLIT_COPIED = "split_copied"
COUNTER_SPLIT_UPDATED = "split_updated"
COUNTER_SPLIT_SKIPPED = "split_skipped"
COUNTER_GENERAL_COPIED = "general_copied"
COUNTER_GENERAL_UPDATED = "general_updated"
COUNTER_GENERAL_SKIPPED = "general_skipped"
COUNTER_ANDROID_COPIED = "android_copied"
COUNTER_ANDROID_UPDATED = "android_updated"
COUNTER_ANDROID_SKIPPED = "android_skipped"
COUNTER_BACKUPS_CREATED = "backups_created"
COUNTER_ERRORS = "errors"
COUNTER_DECISION_ASKED = "decision_asked"
COUNTER_DECISION_UPDATE = "decision_update"
COUNTER_DECISION_SKIP = "decision_skip"

OUTCOME_COPIED = "Copiato"
OUTCOME_UPDATED = "Aggiornato"
OUTCOME_KEPT = "Mantenuto"
OUTCOME_SKIPPED = "Saltato"
OUTCOME_ERROR = "Errore"
OUTCOME_INTERRUPTED = "Interrotto"

REPORT_HEADERS = [
    "nome_file",
    "percorso_sorgente",
    "cartelle_assegnate",
    "copiato_nel_repertorio",
    "aggiornato_nel_repertorio",
    "saltato_nel_repertorio",
    "copiato_nel_generale",
    "aggiornato_nel_generale",
    "saltato_nel_generale",
    "copiato_android",
    "aggiornato_android",
    "saltato_android",
    "backup_effettuato",
    "errori",
    "decisione_utente",
    "tempo",
    "messaggio_errore",
    "data_ora",
]


@dataclass(slots=True)
class Rep003OperationRecord:
    nome_file: str
    percorso_sorgente: str
    cartelle_assegnate: str
    copiato_nel_repertorio: str
    aggiornato_nel_repertorio: str
    saltato_nel_repertorio: str
    copiato_nel_generale: str
    aggiornato_nel_generale: str
    saltato_nel_generale: str
    copiato_android: str
    aggiornato_android: str
    saltato_android: str
    backup_effettuato: str
    errori: str
    decisione_utente: str
    tempo: str
    messaggio_errore: str
    data_ora: str

    def to_row(self) -> dict[str, str]:
        return {
            "nome_file": self.nome_file,
            "percorso_sorgente": self.percorso_sorgente,
            "cartelle_assegnate": self.cartelle_assegnate,
            "copiato_nel_repertorio": self.copiato_nel_repertorio,
            "aggiornato_nel_repertorio": self.aggiornato_nel_repertorio,
            "saltato_nel_repertorio": self.saltato_nel_repertorio,
            "copiato_nel_generale": self.copiato_nel_generale,
            "aggiornato_nel_generale": self.aggiornato_nel_generale,
            "saltato_nel_generale": self.saltato_nel_generale,
            "copiato_android": self.copiato_android,
            "aggiornato_android": self.aggiornato_android,
            "saltato_android": self.saltato_android,
            "backup_effettuato": self.backup_effettuato,
            "errori": self.errori,
            "decisione_utente": self.decisione_utente,
            "tempo": self.tempo,
            "messaggio_errore": self.messaggio_errore,
            "data_ora": self.data_ora,
        }


@dataclass(slots=True)
class Rep003UpdateResult:
    success: bool
    interrupted: bool
    error: str | None
    total_tracks: int
    processed_tracks: int
    copied_tracks: int
    updated_tracks: int
    kept_tracks: int
    skipped_tracks: int
    error_tracks: int
    elapsed_seconds: float
    session_folder: str
    report_paths: dict[str, str]
    log_path: str
    records: list[Rep003OperationRecord] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)


def run_repertory_new_tracks_update(
    *,
    new_tracks_dir: str | Path,
    split_repertory_dir: str | Path,
    general_repertory_dir: str | Path,
    smartphone_tablet_dir: str | Path,
    assignments_snapshot: dict[str, dict[str, object]],
    progress_callback: ProgressCallback | None = None,
    log_callback: LogCallback | None = None,
    decision_callback: DecisionCallback | None = None,
    cancel_event: object | None = None,
) -> Rep003UpdateResult:
    started_at = time.monotonic()

    new_tracks_root = Path(new_tracks_dir).expanduser().resolve()
    split_root = Path(split_repertory_dir).expanduser().resolve()
    general_root = Path(general_repertory_dir).expanduser().resolve()
    smartphone_root = Path(smartphone_tablet_dir).expanduser().resolve()

    _validate_roots(new_tracks_root, split_root, general_root, smartphone_root)

    session_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_root = new_tracks_root / f"{SESSION_FOLDER_PREFIX}{session_stamp}"
    report_root = session_root / REPORT_SUBFOLDER_NAME
    backup_root = session_root / BACKUP_SUBFOLDER_NAME
    log_root = session_root / LOG_SUBFOLDER_NAME
    split_backup_root = backup_root / BACKUP_SPLIT_FOLDER_NAME
    general_backup_root = backup_root / BACKUP_GENERAL_FOLDER_NAME
    android_backup_root = backup_root / BACKUP_ANDROID_FOLDER_NAME

    for folder in (
        report_root,
        split_backup_root,
        general_backup_root,
        android_backup_root,
        log_root,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    csv_path = report_root / f"{REPORT_FILE_STEM}.csv"
    html_path = report_root / f"{REPORT_FILE_STEM}.html"
    xlsx_path = report_root / f"{REPORT_FILE_STEM}.xlsx"
    log_path = log_root / f"{REPORT_FILE_STEM}.log"

    logs: list[str] = []

    def _log(message: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {message}"
        logs.append(line)
        if log_callback is not None:
            log_callback(message)

    _log(f"[REP003] Sessione avviata | path={session_root}")

    tracks_to_process = _build_track_jobs(assignments_snapshot)
    total_tracks = len(tracks_to_process)

    split_index = _index_mp3_by_normalized_name(split_root)
    general_index = _index_mp3_by_normalized_name(general_root)
    smartphone_index = _index_mp3_by_normalized_name(smartphone_root)

    records: list[Rep003OperationRecord] = []
    counters = _initial_rep003_counters()
    processed_tracks = 0
    interrupted = False
    session_policy = SESSION_POLICY_ASK

    for track in tracks_to_process:
        if _is_cancelled(cancel_event):
            interrupted = True
            break

        source_path = Path(track["source_path"])  # type: ignore[index]
        destinations = list(track["destinations"])  # type: ignore[index]
        normalized_name = _normalize_name(source_path.name)

        if not source_path.exists() or not source_path.is_file():
            track_result = _build_rep003_track_result(
                source_path=source_path,
                destinations=destinations,
                decision_label="Non disponibile",
                track_summary={
                    COUNTER_TRACKS_ERRORS: 1,
                    COUNTER_ERRORS: 1,
                },
                message="File sorgente non disponibile.",
                started_at=started_at,
            )
            records.append(track_result)
            _merge_rep003_counters(counters, {
                COUNTER_TRACKS_ERRORS: 1,
                COUNTER_ERRORS: 1,
            })
            processed_tracks += 1
            _emit_progress(progress_callback, processed_tracks, total_tracks, source_path.name, counters[COUNTER_TRACKS_INSERTED], counters[COUNTER_TRACKS_UPDATED], counters[COUNTER_TRACKS_KEPT], counters[COUNTER_ERRORS], started_at)
            continue

        target_ops = _build_target_operations(
            file_name=source_path.name,
            split_root=split_root,
            general_root=general_root,
            smartphone_root=smartphone_root,
            relative_destinations=destinations,
            split_backup_root=split_backup_root,
            general_backup_root=general_backup_root,
            android_backup_root=android_backup_root,
        )

        existing_paths = _collect_existing_paths(
            normalized_name,
            split_index,
            general_index,
            smartphone_index,
        )

        track_decision = DECISION_UPDATE_CURRENT
        if existing_paths:
            counters[COUNTER_DECISION_ASKED] += 1
            if session_policy == SESSION_POLICY_UPDATE_ALL:
                track_decision = DECISION_UPDATE_AND_BYPASS_SESSION
            elif session_policy == SESSION_POLICY_SKIP_ALL:
                track_decision = DECISION_SKIP_AND_BYPASS_SESSION
            else:
                if decision_callback is None:
                    track_decision = DECISION_SKIP_CURRENT
                else:
                    decision_payload = {
                        "source_path": str(source_path),
                        "source_name": source_path.name,
                        "existing_paths": [str(path) for path in existing_paths],
                        "request_reason": "Il file e gia presente nel repertorio.",
                    }
                    selected = decision_callback(decision_payload)
                    if selected is None:
                        interrupted = _is_cancelled(cancel_event)
                        if interrupted:
                            break
                        track_decision = DECISION_SKIP_CURRENT
                    else:
                        track_decision = str(selected)

            if track_decision == DECISION_UPDATE_AND_BYPASS_SESSION:
                session_policy = SESSION_POLICY_UPDATE_ALL
                counters[COUNTER_DECISION_UPDATE] += 1
            elif track_decision == DECISION_SKIP_AND_BYPASS_SESSION:
                session_policy = SESSION_POLICY_SKIP_ALL
                counters[COUNTER_DECISION_SKIP] += 1

        track_summary = _execute_rep003_track(
            source_path=source_path,
            destinations=destinations,
            target_ops=target_ops,
            decision=track_decision,
            started_at=started_at,
            log_callback=_log,
        )
        records.append(
            _build_rep003_track_result(
                source_path=source_path,
                destinations=destinations,
                decision_label=_rep003_decision_label(track_decision, bool(existing_paths)),
                track_summary=track_summary,
                message=track_summary.get("message", ""),
                started_at=started_at,
            )
        )
        _merge_rep003_counters(counters, track_summary)
        processed_tracks += 1
        _emit_progress(
            progress_callback,
            processed_tracks,
            total_tracks,
            source_path.name,
            counters[COUNTER_TRACKS_INSERTED],
            counters[COUNTER_TRACKS_UPDATED],
            counters[COUNTER_TRACKS_KEPT],
            counters[COUNTER_ERRORS],
            started_at,
        )

    if interrupted:
        for pending in tracks_to_process[processed_tracks:]:
            source_path = Path(pending["source_path"])  # type: ignore[index]
            records.append(
                Rep003OperationRecord(
                    nome_file=source_path.name,
                    percorso_sorgente=str(source_path),
                    cartelle_assegnate="",
                    copiato_nel_repertorio="0",
                    aggiornato_nel_repertorio="0",
                    saltato_nel_repertorio="0",
                    copiato_nel_generale="0",
                    aggiornato_nel_generale="0",
                    saltato_nel_generale="0",
                    copiato_android="0",
                    aggiornato_android="0",
                    saltato_android="0",
                    backup_effettuato="0",
                    errori="1",
                    decisione_utente="Interrotto",
                    tempo=_format_duration(int(max(0.0, time.monotonic() - started_at))),
                    messaggio_errore="Operazione interrotta.",
                    data_ora=_now_iso(),
                )
            )

    rows = [row.to_row() for row in records]
    _write_csv(csv_path, rows)
    _write_html(html_path, rows)
    _write_xlsx(xlsx_path, rows)
    _write_text_atomic(log_path, "\n".join(logs) + "\n")

    elapsed = max(0.0, time.monotonic() - started_at)

    return Rep003UpdateResult(
        success=not interrupted,
        interrupted=interrupted,
        error=None,
        total_tracks=total_tracks,
        processed_tracks=processed_tracks,
        copied_tracks=counters[COUNTER_TRACKS_INSERTED],
        updated_tracks=counters[COUNTER_TRACKS_UPDATED],
        kept_tracks=counters[COUNTER_TRACKS_KEPT],
        skipped_tracks=counters[COUNTER_TRACKS_SKIPPED],
        error_tracks=counters[COUNTER_TRACKS_ERRORS],
        elapsed_seconds=elapsed,
        session_folder=str(session_root),
        report_paths={
            "csv": str(csv_path),
            "html": str(html_path),
            "xlsx": str(xlsx_path),
        },
        log_path=str(log_path),
        records=records,
        counters=dict(counters),
    )


def _initial_rep003_counters() -> dict[str, int]:
    return {
        COUNTER_TRACKS_PROCESSED: 0,
        COUNTER_TRACKS_INSERTED: 0,
        COUNTER_TRACKS_UPDATED: 0,
        COUNTER_TRACKS_KEPT: 0,
        COUNTER_TRACKS_SKIPPED: 0,
        COUNTER_TRACKS_ERRORS: 0,
        COUNTER_SPLIT_COPIED: 0,
        COUNTER_SPLIT_UPDATED: 0,
        COUNTER_SPLIT_SKIPPED: 0,
        COUNTER_GENERAL_COPIED: 0,
        COUNTER_GENERAL_UPDATED: 0,
        COUNTER_GENERAL_SKIPPED: 0,
        COUNTER_ANDROID_COPIED: 0,
        COUNTER_ANDROID_UPDATED: 0,
        COUNTER_ANDROID_SKIPPED: 0,
        COUNTER_BACKUPS_CREATED: 0,
        COUNTER_ERRORS: 0,
        COUNTER_DECISION_ASKED: 0,
        COUNTER_DECISION_UPDATE: 0,
        COUNTER_DECISION_SKIP: 0,
    }


def _merge_rep003_counters(target: dict[str, int], updates: dict[str, int]) -> None:
    for key, value in updates.items():
        if key not in target:
            continue
        target[key] = int(target[key]) + int(value)


def _execute_rep003_track(
    *,
    source_path: Path,
    destinations: list[str],
    target_ops: list[dict[str, object]],
    decision: str,
    started_at: float,
    log_callback: LogCallback | None,
) -> dict[str, int | str]:
    summary = {
        COUNTER_TRACKS_PROCESSED: 1,
        COUNTER_TRACKS_INSERTED: 0,
        COUNTER_TRACKS_UPDATED: 0,
        COUNTER_TRACKS_KEPT: 0,
        COUNTER_TRACKS_SKIPPED: 0,
        COUNTER_TRACKS_ERRORS: 0,
        COUNTER_SPLIT_COPIED: 0,
        COUNTER_SPLIT_UPDATED: 0,
        COUNTER_SPLIT_SKIPPED: 0,
        COUNTER_GENERAL_COPIED: 0,
        COUNTER_GENERAL_UPDATED: 0,
        COUNTER_GENERAL_SKIPPED: 0,
        COUNTER_ANDROID_COPIED: 0,
        COUNTER_ANDROID_UPDATED: 0,
        COUNTER_ANDROID_SKIPPED: 0,
        COUNTER_BACKUPS_CREATED: 0,
        COUNTER_ERRORS: 0,
        "message": "",
    }

    if not target_ops:
        summary[COUNTER_TRACKS_SKIPPED] = 1
        summary["message"] = "Nessuna destinazione associata."
        return summary

    should_keep_existing = decision in {DECISION_SKIP_CURRENT, DECISION_SKIP_AND_BYPASS_SESSION}
    any_copy = False
    any_update = False
    any_keep = False
    any_error = False
    messages: list[str] = []

    for operation in target_ops:
        destination_type = str(operation["destination_type"])
        destination_path = Path(operation["destination_path"])
        backup_root = Path(operation["backup_root"])
        backup_relative = Path(str(operation["backup_relative"]))

        copied_key, updated_key, skipped_key = _rep003_counter_keys_for_destination(destination_type)
        destination_exists = destination_path.exists()

        try:
            if destination_exists and should_keep_existing:
                summary[skipped_key] = int(summary[skipped_key]) + 1
                any_keep = True
                continue

            if destination_exists:
                backup_path = backup_root / backup_relative
                _copy_file_atomic(destination_path, backup_path)
                summary[COUNTER_BACKUPS_CREATED] = int(summary[COUNTER_BACKUPS_CREATED]) + 1
                summary[updated_key] = int(summary[updated_key]) + 1
                any_update = True
            else:
                summary[copied_key] = int(summary[copied_key]) + 1
                any_copy = True

            _copy_file_atomic(source_path, destination_path)
        except Exception as error:
            any_error = True
            summary[COUNTER_ERRORS] = int(summary[COUNTER_ERRORS]) + 1
            messages.append(f"{destination_type}: {error}")
            if log_callback is not None:
                log_callback(f"[REP003][ERRORE] {source_path.name} -> {destination_path} | {error}")

    if any_update:
        summary[COUNTER_TRACKS_UPDATED] = 1
    elif any_copy:
        summary[COUNTER_TRACKS_INSERTED] = 1
    elif any_keep and not any_error:
        summary[COUNTER_TRACKS_KEPT] = 1
    elif any_error:
        summary[COUNTER_TRACKS_ERRORS] = 1
    else:
        summary[COUNTER_TRACKS_SKIPPED] = 1

    summary["message"] = "; ".join(messages)
    return summary


def _build_rep003_track_result(
    *,
    source_path: Path,
    destinations: list[str],
    decision_label: str,
    track_summary: dict[str, int | str],
    message: str,
    started_at: float,
) -> Rep003OperationRecord:
    return Rep003OperationRecord(
        nome_file=source_path.name,
        percorso_sorgente=str(source_path),
        cartelle_assegnate=_format_rep003_destinations(destinations),
        copiato_nel_repertorio=str(track_summary.get(COUNTER_SPLIT_COPIED, 0)),
        aggiornato_nel_repertorio=str(track_summary.get(COUNTER_SPLIT_UPDATED, 0)),
        saltato_nel_repertorio=str(track_summary.get(COUNTER_SPLIT_SKIPPED, 0)),
        copiato_nel_generale=str(track_summary.get(COUNTER_GENERAL_COPIED, 0)),
        aggiornato_nel_generale=str(track_summary.get(COUNTER_GENERAL_UPDATED, 0)),
        saltato_nel_generale=str(track_summary.get(COUNTER_GENERAL_SKIPPED, 0)),
        copiato_android=str(track_summary.get(COUNTER_ANDROID_COPIED, 0)),
        aggiornato_android=str(track_summary.get(COUNTER_ANDROID_UPDATED, 0)),
        saltato_android=str(track_summary.get(COUNTER_ANDROID_SKIPPED, 0)),
        backup_effettuato=str(track_summary.get(COUNTER_BACKUPS_CREATED, 0)),
        errori=str(track_summary.get(COUNTER_ERRORS, 0)),
        decisione_utente=decision_label,
        tempo=_format_duration(int(max(0.0, time.monotonic() - started_at))),
        messaggio_errore=str(message or track_summary.get("message", "")),
        data_ora=_now_iso(),
    )


def _format_rep003_destinations(destinations: list[str]) -> str:
    if not destinations:
        return ""
    labels: list[str] = []
    for destination in destinations:
        normalized = str(destination or "").strip().replace("\\", "/").strip("/")
        if not normalized or normalized == ".":
            labels.append("ROOT")
        else:
            labels.append(normalized.replace("/", "\\"))
    return ", ".join(labels)


def _rep003_decision_label(decision: str, decision_was_requested: bool) -> str:
    if decision == DECISION_UPDATE_CURRENT:
        return "Aggiorna questo"
    if decision == DECISION_SKIP_CURRENT:
        return "Salta questo"
    if decision == DECISION_UPDATE_AND_BYPASS_SESSION:
        return "Aggiorna tutti"
    if decision == DECISION_SKIP_AND_BYPASS_SESSION:
        return "Salta tutti"
    if decision_was_requested:
        return "Non disponibile"
    return "Nessuna conferma"


def _rep003_counter_keys_for_destination(destination_type: str) -> tuple[str, str, str]:
    if destination_type == DEST_SPLIT:
        return COUNTER_SPLIT_COPIED, COUNTER_SPLIT_UPDATED, COUNTER_SPLIT_SKIPPED
    if destination_type == DEST_GENERAL:
        return COUNTER_GENERAL_COPIED, COUNTER_GENERAL_UPDATED, COUNTER_GENERAL_SKIPPED
    return COUNTER_ANDROID_COPIED, COUNTER_ANDROID_UPDATED, COUNTER_ANDROID_SKIPPED


def _build_target_operations(
    *,
    file_name: str,
    split_root: Path,
    general_root: Path,
    smartphone_root: Path,
    relative_destinations: list[str],
    split_backup_root: Path,
    general_backup_root: Path,
    android_backup_root: Path,
) -> list[dict[str, object]]:
    operations: list[dict[str, object]] = []
    seen: set[str] = set()

    for relative in relative_destinations:
        clean_relative = _normalize_relative_destination(relative)
        split_path = split_root / clean_relative / file_name if clean_relative else split_root / file_name
        android_path = smartphone_root / clean_relative / file_name if clean_relative else smartphone_root / file_name

        split_backup_relative = _build_backup_relative_path(clean_relative, file_name)
        android_backup_relative = _build_backup_relative_path(clean_relative, file_name)

        for destination_type, destination_path, backup_root, backup_relative in (
            (DEST_SPLIT, split_path, split_backup_root, split_backup_relative),
            (DEST_ANDROID, android_path, android_backup_root, android_backup_relative),
        ):
            key = f"{destination_type}|{str(destination_path).casefold()}"
            if key in seen:
                continue
            seen.add(key)
            operations.append(
                {
                    "destination_type": destination_type,
                    "destination_path": destination_path,
                    "backup_root": backup_root,
                    "backup_relative": backup_relative,
                }
            )

    general_path = general_root / file_name
    key_general = f"{DEST_GENERAL}|{str(general_path).casefold()}"
    if key_general not in seen:
        operations.append(
            {
                "destination_type": DEST_GENERAL,
                "destination_path": general_path,
                "backup_root": general_backup_root,
                "backup_relative": _build_backup_relative_path("", file_name),
            }
        )

    android_general_path = smartphone_root / REPERTORY_GENERAL_TECH_FOLDER_NAME / file_name
    key_android_general = f"{DEST_ANDROID}|{str(android_general_path).casefold()}"
    if key_android_general not in seen:
        operations.append(
            {
                "destination_type": DEST_ANDROID,
                "destination_path": android_general_path,
                "backup_root": android_backup_root,
                "backup_relative": _build_backup_relative_path(REPERTORY_GENERAL_TECH_FOLDER_NAME, file_name),
            }
        )

    return operations


def _normalize_relative_destination(relative: str) -> str:
    normalized = str(relative or "").strip().replace("\\", "/").strip("/")
    if normalized == ".":
        return ""
    return normalized


def _build_backup_relative_path(relative: str, file_name: str) -> Path:
    clean_relative = _normalize_relative_destination(relative)
    if not clean_relative:
        return Path(file_name)
    return Path(clean_relative) / file_name


def _copy_file_atomic(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination_path.name}.", suffix=".tmp", dir=str(destination_path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, destination_path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


def _write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix or ".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(text, encoding=encoding)
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


def _build_track_jobs(assignments_snapshot: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    for source_path, payload in assignments_snapshot.items():
        status = str(payload.get("status", "") or "")
        if status.casefold() != "gestito":
            continue
        destinations = payload.get("destinations", [])
        if not isinstance(destinations, list):
            destinations = []
        normalized_destinations = sorted(
            {
                str(item or "").strip().replace("\\", "/").strip("/")
                for item in destinations
                if str(item or "").strip()
            }
        )
        jobs.append(
            {
                "source_path": str(source_path),
                "destinations": normalized_destinations,
            }
        )
    jobs.sort(key=lambda row: str(row["source_path"]).casefold())
    return jobs


def _collect_existing_paths(
    normalized_name: str,
    split_index: dict[str, list[Path]],
    general_index: dict[str, list[Path]],
    smartphone_index: dict[str, list[Path]],
) -> list[Path]:
    found = list(split_index.get(normalized_name, []))
    found.extend(general_index.get(normalized_name, []))
    found.extend(smartphone_index.get(normalized_name, []))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        token = str(path).casefold()
        if token in seen:
            continue
        seen.add(token)
        unique.append(path)
    return unique


def _index_mp3_by_normalized_name(root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for current_root, _dirs, files in os.walk(root):
        for file_name in files:
            if Path(file_name).suffix.casefold() != ".mp3":
                continue
            path = (Path(current_root) / file_name).resolve()
            key = _normalize_name(path.name)
            index.setdefault(key, []).append(path)
    for key in index:
        index[key].sort(key=lambda p: str(p).casefold())
    return index


def _copy_mp3(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    src_size, src_hash = _read_size_and_sha256(source_path)
    dst_size, dst_hash = _read_size_and_sha256(destination_path)
    if src_size != dst_size or src_hash != dst_hash:
        raise OSError("Verifica copia fallita: hash o dimensione non coerenti.")


def _read_size_and_sha256(path: Path) -> tuple[int, str]:
    return int(path.stat().st_size), _sha256_file(path)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _normalize_name(file_name: str) -> str:
    nfc_name = unicodedata.normalize("NFC", str(file_name))
    stripped = nfc_name.strip()
    return stripped.casefold()


def _validate_roots(new_tracks_root: Path, split_root: Path, general_root: Path, smartphone_root: Path) -> None:
    if not new_tracks_root.is_dir():
        raise RuntimeError("Cartella Nuovi Brani non valida.")
    if not split_root.is_dir():
        raise RuntimeError("Cartella Repertorio Suddiviso non valida.")
    if not general_root.is_dir():
        raise RuntimeError("Cartella Repertorio Generale non valida.")
    smartphone_root.mkdir(parents=True, exist_ok=True)
    if not smartphone_root.is_dir():
        raise RuntimeError("Cartella Smartphone/Tablet non valida.")
    resolved = {str(path.resolve()).casefold() for path in (new_tracks_root, split_root, general_root, smartphone_root)}
    if len(resolved) != 4:
        raise RuntimeError("Le cartelle di lavoro devono essere tutte differenti.")


def _is_cancelled(cancel_event: object | None) -> bool:
    if cancel_event is None:
        return False
    probe = getattr(cancel_event, "is_set", None)
    if callable(probe):
        try:
            return bool(probe())
        except Exception:
            return False
    return False


def _emit_progress(
    callback: ProgressCallback | None,
    current: int,
    total: int,
    current_name: str,
    copied: int,
    updated: int,
    kept: int,
    errors: int,
    started_at: float,
) -> None:
    if callback is None:
        return
    elapsed = max(0.0, time.monotonic() - started_at)
    eta_text = "--"
    if current > 0 and total > current:
        per_item = elapsed / float(current)
        eta_seconds = max(0, int(per_item * max(0, total - current)))
        eta_text = _format_duration(eta_seconds)
    percent = 0 if total <= 0 else int((current / float(total)) * 100)
    message = (
        f"Elaborazione {current}/{total} ({percent}%) - {current_name} "
        f"| copiati={copied} aggiornati={updated} mantenuti={kept} errori={errors} "
        f"| trascorso={_format_duration(int(elapsed))} eta={eta_text}"
    )
    callback(current, total, message)


def _format_duration(seconds: int) -> str:
    value = max(0, int(seconds))
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix or ".csv", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_HEADERS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({header: str(row.get(header, "")) for header in REPORT_HEADERS})
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


def _write_html(path: Path, rows: list[dict[str, str]]) -> None:
    lines = [
        "<!doctype html>",
        "<html lang='it'>",
        "<head>",
        "  <meta charset='utf-8'>",
        "  <title>Inserimento Nuovi Brani nel Repertorio</title>",
        "  <style>",
        "    body { font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #1f2937; }",
        "    h1 { margin: 0 0 8px 0; font-size: 22px; }",
        "    p { margin: 0 0 16px 0; color: #4b5563; }",
        "    table { width: 100%; border-collapse: collapse; font-size: 13px; }",
        "    th, td { border: 1px solid #d1d5db; padding: 6px; text-align: left; white-space: pre-wrap; vertical-align: top; }",
        "    th { background: #f3f4f6; }",
        "  </style>",
        "</head>",
        "<body>",
        "  <h1>Inserimento Nuovi Brani nel Repertorio</h1>",
        f"  <p>Generato: {_now_iso()}</p>",
    ]

    if not rows:
        lines.append("<p>Nessun dato.</p>")
    else:
        lines.append("<table>")
        lines.append("<thead><tr>" + "".join(f"<th>{html.escape(header)}</th>" for header in REPORT_HEADERS) + "</tr></thead>")
        lines.append("<tbody>")
        for row in rows:
            lines.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in REPORT_HEADERS) + "</tr>")
        lines.append("</tbody></table>")

    lines.extend(["</body>", "</html>"])
    _write_text_atomic(path, "\n".join(lines), encoding="utf-8")


def _write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix or ".xlsx", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    content_types = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        "<Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
        "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
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
        "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
        "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
        "<sheets><sheet name='Inserimento' sheetId='1' r:id='rId1'/></sheets></workbook>"
    )
    workbook_rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/>"
        "<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles' Target='styles.xml'/>"
        "</Relationships>"
    )
    styles = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<styleSheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<fonts count='2'>"
        "<font><sz val='11'/><name val='Calibri'/></font>"
        "<font><b/><sz val='11'/><name val='Calibri'/></font>"
        "</fonts>"
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
    sheet = _build_xlsx_sheet(rows)

    try:
        with ZipFile(temp_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/styles.xml", styles)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


def _build_xlsx_sheet(rows: list[dict[str, str]]) -> str:
    lines = [
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>",
        "<sheetViews><sheetView workbookViewId='0'><pane ySplit='1' topLeftCell='A2' activePane='bottomLeft' state='frozen'/></sheetView></sheetViews>",
        "<cols>",
    ]
    for index in range(1, len(REPORT_HEADERS) + 1):
        lines.append(f"<col min='{index}' max='{index}' width='34.00' customWidth='1'/>")
    lines.append("</cols>")
    lines.append("<sheetData>")

    lines.append("<row r='1'>")
    for index, label in enumerate(REPORT_HEADERS, start=1):
        col = _xlsx_col(index)
        lines.append(f"<c r='{col}1' s='1' t='inlineStr'><is><t>{html.escape(label)}</t></is></c>")
    lines.append("</row>")

    for row_index, row in enumerate(rows, start=2):
        lines.append(f"<row r='{row_index}'>")
        for col_index, header in enumerate(REPORT_HEADERS, start=1):
            col = _xlsx_col(col_index)
            value = html.escape(str(row.get(header, "")))
            lines.append(f"<c r='{col}{row_index}' s='2' t='inlineStr'><is><t>{value}</t></is></c>")
        lines.append("</row>")

    lines.append("</sheetData>")
    last_col = _xlsx_col(len(REPORT_HEADERS))
    last_row = max(1, len(rows) + 1)
    lines.append(f"<autoFilter ref='A1:{last_col}{last_row}'/>")
    lines.append("</worksheet>")
    return "".join(lines)


def _xlsx_col(index: int) -> str:
    result = ""
    value = max(1, int(index))
    while value > 0:
        value, rem = divmod(value - 1, 26)
        result = chr(65 + rem) + result
    return result
