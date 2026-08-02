# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import os
import stat
import shutil
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Any
from zipfile import ZIP_DEFLATED, ZipFile

OrganizeProgressCallback = Callable[[int, int, str], None]
OrganizeLogCallback = Callable[[str], None]
DecisionCallback = Callable[[dict[str, Any]], str | None]

DECISION_UPDATE_CURRENT = "UPDATE_CURRENT"
DECISION_SKIP_CURRENT = "SKIP_CURRENT"
DECISION_UPDATE_AND_BYPASS_SESSION = "UPDATE_AND_BYPASS_SESSION"
DECISION_SKIP_AND_BYPASS_SESSION = "SKIP_AND_BYPASS_SESSION"

SESSION_MTIME_POLICY_ASK = "ASK"
SESSION_MTIME_POLICY_UPDATE_ALL = "UPDATE_ALL"
SESSION_MTIME_POLICY_SKIP_ALL = "SKIP_ALL"

SMARTPHONE_TABLET_ROOT = Path(r"C:\MixCreatorPro-File per aggiornamento Smartphone-Tablet")
REPERTORY_NON_TROVATI_FOLDER_NAME = "File Non trovati in Repertorio"
REPERTORY_GENERAL_TECH_FOLDER_NAME = "REPERTORIO_GENERALE_DA_MIXCREATOR"
REPERTORY_TO_INSERT_FOLDER_NAME = "Brani non trovati in Repertorio da inserire"

COUNTER_SMARTPHONE_TABLET_COPIATI = "SMARTPHONE_TABLET_COPIATI"
COUNTER_SMARTPHONE_TABLET_ERRORI = "SMARTPHONE_TABLET_ERRORI"
COUNTER_FILE_MANTENUTI = "FILE_MANTENUTI"
COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO = "FILE_NON_TROVATI_NEL_REPERTORIO"
COUNTER_FILE_NON_TROVATI_COPIATI = "FILE_NON_TROVATI_COPIATI"
COUNTER_FILE_NON_TROVATI_ERRORI_COPIA = "FILE_NON_TROVATI_ERRORI_COPIA"
COUNTER_BRANI_DA_INSERIRE = "BRANI_DA_INSERIRE"
COUNTER_BRANI_DA_INSERIRE_ERRORI = "BRANI_DA_INSERIRE_ERRORI"
COUNTER_BRANI_AGGIORNATI = "BRANI_AGGIORNATI"
COUNTER_COPIE_AGGIORNATE_REPERTORIO = "COPIE_AGGIORNATE_REPERTORIO"
COUNTER_COPIE_AGGIORNATE_REPERTORIO_GENERALE = "COPIE_AGGIORNATE_REPERTORIO_GENERALE"

REPORT_HEADERS = [
    "file_sorgente",
    "percorso_sorgente",
    "data_ora_sorgente",
    "file_repertorio",
    "percorso_destinazione",
    "data_ora_destinazione_precedente",
    "differenza_temporale",
    "motivo_confronto",
    "stato",
    "decisione_utente",
    "motivo",
    "copiato_smartphone_tablet",
    "percorso_copia_smartphone_tablet",
    "errore_copia_smartphone_tablet",
    "repertorio_generale_aggiornato",
    "percorso_repertorio_generale",
    "file_presente_precedentemente_repertorio_generale",
    "backup_repertorio_generale_eseguito",
    "percorso_backup_repertorio_generale",
    "esito_backup_repertorio_generale",
    "dettaglio_errore_backup_repertorio_generale",
    "percorso_copia_smartphone_repertorio_generale",
    "errore_copia_smartphone_repertorio_generale",
    "percorso_file_non_trovato",
    "timestamp_sorgente",
    "timestamp_destinazione_precedente",
    "session_id",
    "data_ora",
    "nome_normalizzato",
    "numero_corrispondenze",
    "dimensione_sorgente",
    "hash_sorgente",
    "dimensione_precedente",
    "hash_precedente",
    "destinazione_piu_recente",
    "controllo_data_ora_eseguito",
    "bypass_data_ora_sessione",
    "aggiornamento_saltato_per_data_ora",
    "backup_eseguito",
    "percorso_backup",
    "copia_eseguita",
    "verifica_finale",
    "durata_ms",
]

REPORT_HEADER_LABELS = {
    "file_sorgente": "File sorgente",
    "percorso_sorgente": "Percorso sorgente",
    "data_ora_sorgente": "Data/Ora sorgente",
    "file_repertorio": "File repertorio",
    "percorso_destinazione": "Percorso repertorio",
    "data_ora_destinazione_precedente": "Data/Ora repertorio",
    "differenza_temporale": "Differenza temporale",
    "motivo_confronto": "Motivo confronto",
    "stato": "Esito",
    "decisione_utente": "Decisione",
    "motivo": "Motivo/Note",
    "copiato_smartphone_tablet": "Copiato Smartphone/Tablet",
    "percorso_copia_smartphone_tablet": "Percorso copia Smartphone/Tablet",
    "errore_copia_smartphone_tablet": "Errore copia Smartphone/Tablet",
    "repertorio_generale_aggiornato": "Repertorio Generale aggiornato",
    "percorso_repertorio_generale": "Percorso Repertorio Generale",
    "file_presente_precedentemente_repertorio_generale": "File presente precedentemente nel Repertorio Generale",
    "backup_repertorio_generale_eseguito": "Backup Repertorio Generale eseguito",
    "percorso_backup_repertorio_generale": "Percorso backup Repertorio Generale",
    "esito_backup_repertorio_generale": "Esito backup Repertorio Generale",
    "dettaglio_errore_backup_repertorio_generale": "Dettaglio errore backup Repertorio Generale",
    "percorso_copia_smartphone_repertorio_generale": "Percorso copia Smartphone/Tablet Repertorio Generale",
    "errore_copia_smartphone_repertorio_generale": "Errore copia Smartphone/Tablet Repertorio Generale",
    "percorso_file_non_trovato": "Percorso File Non trovato",
    "timestamp_sorgente": "Timestamp sorgente",
    "timestamp_destinazione_precedente": "Timestamp repertorio",
    "session_id": "Sessione",
    "data_ora": "Data/Ora report",
    "nome_normalizzato": "Nome normalizzato",
    "numero_corrispondenze": "Numero corrispondenze",
    "dimensione_sorgente": "Dimensione sorgente",
    "hash_sorgente": "Hash sorgente",
    "dimensione_precedente": "Dimensione repertorio",
    "hash_precedente": "Hash repertorio",
    "destinazione_piu_recente": "Repertorio piu recente",
    "controllo_data_ora_eseguito": "Controllo data/ora eseguito",
    "bypass_data_ora_sessione": "Bypass data/ora sessione",
    "aggiornamento_saltato_per_data_ora": "Aggiornamento saltato per data/ora",
    "backup_eseguito": "Backup eseguito",
    "percorso_backup": "Percorso backup",
    "copia_eseguita": "Copia eseguita",
    "verifica_finale": "Verifica finale",
    "durata_ms": "Durata (ms)",
}


class RepertoryStatus(str, Enum):
    AGGIORNATO = "AGGIORNATO"
    AGGIORNATO_MULTIPLO = "AGGIORNATO_MULTIPLO"
    NON_TROVATO = "NON_TROVATO"
    ERRORE_SORGENTE = "ERRORE_SORGENTE"
    ERRORE_BACKUP = "ERRORE_BACKUP"
    ERRORE_COPIA = "ERRORE_COPIA"
    ERRORE_VERIFICA = "ERRORE_VERIFICA"
    AMBIGUO = "AMBIGUO"
    SALTATO_FILE_DESTINAZIONE_PIU_RECENTE = "SALTATO_FILE_DESTINAZIONE_PIU_RECENTE"
    INTERROTTO = "INTERROTTO"


@dataclass(slots=True)
class RepertoryOrganizeResult:
    success: bool
    interrupted: bool
    error: str | None
    total_source_files: int
    processed_source_files: int
    elapsed_seconds: float
    counters: dict[str, int]
    session_folder: str
    report_paths: dict[str, str]
    log_path: str
    smartphone_tablet_root: str
    repertory_not_found_dir: str
    repertory_to_insert_dir: str


def organize_repertory_from_folders(
    *,
    updates_dir: str | Path,
    repertory_dir: str | Path,
    repertory_general_dir: str | Path | None = None,
    results_dir: str | Path,
    backup_enabled: bool = True,
    smartphone_tablet_dir: str | Path | None = None,
    progress_callback: OrganizeProgressCallback | None = None,
    log_callback: OrganizeLogCallback | None = None,
    decision_callback: DecisionCallback | None = None,
    cancel_event: object | None = None,
) -> RepertoryOrganizeResult:
    start_time = time.monotonic()

    updates_root = Path(updates_dir).expanduser().resolve()
    repertory_root = Path(repertory_dir).expanduser().resolve()
    repertory_general_root = Path(repertory_general_dir if repertory_general_dir is not None else repertory_root).expanduser().resolve()
    general_sync_enabled = repertory_general_dir is not None
    results_root = Path(results_dir).expanduser().resolve()
    smartphone_tablet_root = resolve_smartphone_tablet_root(smartphone_tablet_dir)

    _validate_roots(
        updates_root=updates_root,
        repertory_root=repertory_root,
        repertory_general_root=repertory_general_root,
        results_root=results_root,
        smartphone_tablet_root=smartphone_tablet_root,
    )

    session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"Organizzazione_Repertorio_{session_timestamp}"
    session_root = results_root / session_id
    backup_root = session_root / "Backup"
    report_root = session_root / "Report"
    log_root = session_root / "Log"

    session_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    log_path = log_root / "Organizzazione_Repertorio.log"
    logs: list[str] = []

    def _log_line(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp}  {message}"
        logs.append(line)
        if log_callback is not None:
            log_callback(message)

    _log_line(f"[TECH] Sessione esiti creata | path={session_root}")

    updates_files = _scan_mp3_non_recursive(updates_root)
    repertory_files = _scan_mp3_recursive(repertory_root)

    non_trovati_folder_name_folded = REPERTORY_NON_TROVATI_FOLDER_NAME.casefold()
    repertory_files = [
        path
        for path in repertory_files
        if not any(part.casefold() == non_trovati_folder_name_folded for part in path.relative_to(repertory_root).parts)
    ]

    _log_line(f"[TECH] Sorgenti trovate={len(updates_files)}")
    _log_line(f"[TECH] Repertorio trovato={len(repertory_files)}")
    _log_line(f"[TECH] Cartella Smartphone/Tablet={smartphone_tablet_root}")

    repertory_index: dict[str, list[Path]] = {}
    for file_path in repertory_files:
        key = _normalize_name(file_path.name)
        repertory_index.setdefault(key, []).append(file_path)
    for key in repertory_index:
        repertory_index[key].sort(key=lambda item: str(item).casefold())

    source_by_key: dict[str, list[Path]] = {}
    for file_path in updates_files:
        key = _normalize_name(file_path.name)
        source_by_key.setdefault(key, []).append(file_path)

    repertory_general_index: dict[str, list[Path]] = {}
    if general_sync_enabled:
        repertory_general_files = _scan_mp3_non_recursive(repertory_general_root)
        for file_path in repertory_general_files:
            key = _normalize_name(file_path.name)
            repertory_general_index.setdefault(key, []).append(file_path)

    rows: list[dict[str, str]] = []
    counters = _new_counters()
    interrupted = False
    matches_found = 0
    files_updated = 0
    files_not_found = 0
    errors_count = 0
    session_mtime_policy = SESSION_MTIME_POLICY_ASK
    repertory_not_found_dir_path: Path | None = None
    repertory_to_insert_dir_path: Path | None = None
    backup_preflight_errors: list[str] = []

    total = len(updates_files)
    processed = 0

    if backup_enabled:
        not_found_source_dir = repertory_root / REPERTORY_NON_TROVATI_FOLDER_NAME
        backup_not_found_dir = backup_root / REPERTORY_NON_TROVATI_FOLDER_NAME
        if not_found_source_dir.is_dir():
            try:
                shutil.copytree(
                    not_found_source_dir,
                    backup_not_found_dir,
                    copy_function=shutil.copy2,
                    dirs_exist_ok=True,
                )
                _log_line(
                    "[BACKUP] Copia preventiva cartella 'File Non trovati in Repertorio' completata "
                    f"| sorgente={not_found_source_dir} | backup={backup_not_found_dir}"
                )
            except Exception as backup_not_found_error:
                backup_error_message = (
                    "Errore backup cartella 'File Non trovati in Repertorio': "
                    f"{backup_not_found_error}"
                )
                backup_preflight_errors.append(backup_error_message)
                _log_line(f"[BACKUP][ERRORE] {backup_error_message}")
        elif not_found_source_dir.exists() and not not_found_source_dir.is_dir():
            backup_error_message = (
                "Errore backup cartella 'File Non trovati in Repertorio': "
                f"il percorso esiste ma non e una cartella ({not_found_source_dir})"
            )
            backup_preflight_errors.append(backup_error_message)
            _log_line(f"[BACKUP][ERRORE] {backup_error_message}")
        else:
            _log_line(
                "[BACKUP] Cartella 'File Non trovati in Repertorio' non presente prima dell'avvio: "
                "nessuna copia preventiva necessaria."
            )

    if backup_preflight_errors:
        for backup_error_message in backup_preflight_errors:
            rows.append(
                _base_row(
                    session_id=session_id,
                    source_path=updates_root,
                    normalized_name="session_backup_precheck",
                    status=RepertoryStatus.ERRORE_BACKUP,
                    reason=backup_error_message,
                    duration_ms=0,
                )
            )
            counters[RepertoryStatus.ERRORE_BACKUP.value] += 1
            errors_count += 1

    for index, source_path in enumerate(updates_files, start=1):
        if _is_cancelled(cancel_event):
            interrupted = True
            for pending in updates_files[index - 1 :]:
                normalized_name = _normalize_name(pending.name)
                rows.append(
                    _base_row(
                        session_id=session_id,
                        source_path=pending,
                        normalized_name=normalized_name,
                        status=RepertoryStatus.INTERROTTO,
                        reason="Non elaborato: operazione interrotta.",
                        duration_ms=0,
                    )
                )
                counters[RepertoryStatus.INTERROTTO.value] += 1
                _log_line(f"[INTERROTTO] {pending.name}")
            break

        source_start = time.monotonic()
        normalized_name = _normalize_name(source_path.name)

        # Detect logical collisions in source set after NFC+strip+casefold.
        source_collisions = source_by_key.get(normalized_name, [])
        if len(source_collisions) > 1:
            collision_names = ", ".join(path.name for path in source_collisions)
            rows.append(
                _base_row(
                    session_id=session_id,
                    source_path=source_path,
                    normalized_name=normalized_name,
                    status=RepertoryStatus.AMBIGUO,
                    matches=len(source_collisions),
                    reason=f"Collisione logica nella sorgente: {collision_names}",
                    duration_ms=int(max(0.0, (time.monotonic() - source_start) * 1000.0)),
                )
            )
            counters[RepertoryStatus.AMBIGUO.value] += 1
            errors_count += 1
            processed += 1
            _log_line(f"[AMBIGUO] {source_path.name} -> {collision_names}")
            _emit_progress(
                progress_callback,
                current=processed,
                total=total,
                current_name=source_path.name,
                matches_found=matches_found,
                files_updated=files_updated,
                files_not_found=files_not_found,
                errors_count=errors_count,
                started_at=start_time,
            )
            continue

        try:
            source_size, source_hash = _read_size_and_sha256(source_path)
            source_mtime = source_path.stat().st_mtime
        except OSError as exc:
            rows.append(
                _base_row(
                    session_id=session_id,
                    source_path=source_path,
                    normalized_name=normalized_name,
                    status=RepertoryStatus.ERRORE_SORGENTE,
                    reason=f"Errore lettura sorgente: {exc}",
                    source_size="",
                    source_hash="",
                    source_mtime=None,
                    duration_ms=int(max(0.0, (time.monotonic() - source_start) * 1000.0)),
                )
            )
            counters[RepertoryStatus.ERRORE_SORGENTE.value] += 1
            errors_count += 1
            processed += 1
            _log_line(f"[ERRORE] {source_path.name} | ERRORE_SORGENTE | {exc}")
            _emit_progress(
                progress_callback,
                current=processed,
                total=total,
                current_name=source_path.name,
                matches_found=matches_found,
                files_updated=files_updated,
                files_not_found=files_not_found,
                errors_count=errors_count,
                started_at=start_time,
            )
            continue

        destinations = list(repertory_index.get(normalized_name, []))
        destinations_general = list(repertory_general_index.get(normalized_name, []))
        source_had_successful_update = False

        if not destinations:
            if general_sync_enabled and not destinations_general:
                to_insert_row = _base_row(
                    session_id=session_id,
                    source_path=source_path,
                    normalized_name=normalized_name,
                    status=RepertoryStatus.NON_TROVATO,
                    source_size=str(source_size),
                    source_hash=source_hash,
                    source_mtime=source_mtime,
                    reason="File assente sia nel Repertorio suddiviso sia nel Repertorio generale: da inserire.",
                    duration_ms=int(max(0.0, (time.monotonic() - source_start) * 1000.0)),
                )
                to_insert_row["stato"] = "Brano non trovato da inserire manualmente"
                to_insert_row["decisione_utente"] = "Copiato in \"Brani non trovati in Repertorio da inserire\""
                to_insert_row["percorso_destinazione"] = "Da inserire"
                to_insert_row["copiato_smartphone_tablet"] = "Non applicabile"
                to_insert_row["errore_copia_smartphone_tablet"] = ""

                _log_line(f"[DIAGNOSI][DA INSERIRE] File non trovato in entrambi i repertori: {source_path}")

                try:
                    repertory_to_insert_dir_path = _ensure_repertory_to_insert_dir(session_root)
                    copied_path = _copy_with_collision_suffix(
                        source_path=source_path,
                        destination_folder=repertory_to_insert_dir_path,
                        preferred_name=source_path.name,
                    )
                    copied_size, copied_hash = _read_size_and_sha256(copied_path)
                    if copied_size != source_size or copied_hash != source_hash:
                        raise VerificationError(
                            "Verifica copia brano da inserire fallita: dimensione/hash non corrispondenti."
                        )
                    to_insert_row["percorso_file_non_trovato"] = str(copied_path)
                    counters[COUNTER_BRANI_DA_INSERIRE] += 1
                    _log_line(f"[DIAGNOSI][DA INSERIRE] Copiato in: {copied_path}")
                except Exception as to_insert_error:
                    to_insert_row["decisione_utente"] = "Errore copia in \"Brani non trovati in Repertorio da inserire\""
                    to_insert_row["percorso_file_non_trovato"] = ""
                    to_insert_row["motivo"] = _append_note(
                        to_insert_row["motivo"],
                        f"Errore copia brano da inserire: {to_insert_error}",
                    )
                    counters[COUNTER_BRANI_DA_INSERIRE_ERRORI] += 1
                    errors_count += 1
                    _log_line(f"[DIAGNOSI][DA INSERIRE][ERRORE] {to_insert_error}")

                rows.append(to_insert_row)
                counters[RepertoryStatus.NON_TROVATO.value] += 1
                files_not_found += 1
                processed += 1
                _emit_progress(
                    progress_callback,
                    current=processed,
                    total=total,
                    current_name=source_path.name,
                    matches_found=matches_found,
                    files_updated=files_updated,
                    files_not_found=files_not_found,
                    errors_count=errors_count,
                    started_at=start_time,
                )
                continue

            not_found_row = _base_row(
                session_id=session_id,
                source_path=source_path,
                normalized_name=normalized_name,
                status=RepertoryStatus.NON_TROVATO,
                source_size=str(source_size),
                source_hash=source_hash,
                source_mtime=source_mtime,
                reason="Nessuna corrispondenza trovata nel Repertorio",
                duration_ms=int(max(0.0, (time.monotonic() - source_start) * 1000.0)),
            )
            not_found_row["stato"] = "File non trovato nel Repertorio"
            not_found_row["decisione_utente"] = "Copiato in \"File Non trovati in Repertorio\""
            not_found_row["percorso_destinazione"] = "Non trovato"
            not_found_row["copiato_smartphone_tablet"] = "Non applicabile"
            not_found_row["errore_copia_smartphone_tablet"] = ""

            _log_line(f"[REPERTORIO] File non trovato: {source_path}")

            try:
                repertory_not_found_dir_path = _ensure_repertory_non_trovati_dir(repertory_root)
                copied_path = _copy_with_collision_suffix(
                    source_path=source_path,
                    destination_folder=repertory_not_found_dir_path,
                    preferred_name=source_path.name,
                )
                copied_size, copied_hash = _read_size_and_sha256(copied_path)
                if copied_size != source_size or copied_hash != source_hash:
                    raise VerificationError(
                        "Verifica copia file non trovato fallita: dimensione/hash non corrispondenti."
                    )
                not_found_row["percorso_file_non_trovato"] = str(copied_path)
                counters[COUNTER_FILE_NON_TROVATI_COPIATI] += 1
                _log_line(f"[REPERTORIO] Copiato in: {copied_path}")
            except Exception as non_found_copy_error:
                not_found_row["decisione_utente"] = "Errore copia in \"File Non trovati in Repertorio\""
                not_found_row["percorso_file_non_trovato"] = ""
                not_found_row["motivo"] = _append_note(
                    not_found_row["motivo"],
                    f"Errore copia file non trovato: {non_found_copy_error}",
                )
                counters[COUNTER_FILE_NON_TROVATI_ERRORI_COPIA] += 1
                errors_count += 1
                _log_line(f"[REPERTORIO] Errore copia file non trovato: {non_found_copy_error}")

            rows.append(
                not_found_row
            )
            counters[RepertoryStatus.NON_TROVATO.value] += 1
            counters[COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO] += 1
            files_not_found += 1
            processed += 1
            _emit_progress(
                progress_callback,
                current=processed,
                total=total,
                current_name=source_path.name,
                matches_found=matches_found,
                files_updated=files_updated,
                files_not_found=files_not_found,
                errors_count=errors_count,
                started_at=start_time,
            )
            continue

        matches_found += len(destinations)
        general_synced_for_source = False

        for destination_path in destinations:
            status_if_ok = (
                RepertoryStatus.AGGIORNATO
                if len(destinations) == 1
                else RepertoryStatus.AGGIORNATO_MULTIPLO
            )
            txn_start = time.monotonic()

            row = _base_row(
                session_id=session_id,
                source_path=source_path,
                normalized_name=normalized_name,
                status=status_if_ok,
                matches=len(destinations),
                destination_path=destination_path,
                source_size=str(source_size),
                source_hash=source_hash,
                source_mtime=source_mtime,
                duration_ms=0,
            )

            backup_path = ""
            temp_path: Path | None = None
            previous_size = ""
            previous_hash = ""

            try:
                previous_size_int, previous_hash_str = _read_size_and_sha256(destination_path)
                destination_mtime = destination_path.stat().st_mtime
                previous_size = str(previous_size_int)
                previous_hash = previous_hash_str
                row["dimensione_precedente"] = previous_size
                row["hash_precedente"] = previous_hash
                row["timestamp_destinazione_precedente"] = _format_timestamp_number(destination_mtime)
                row["data_ora_destinazione_precedente"] = _format_timestamp_human(destination_mtime)
                row["differenza_temporale"] = _format_mtime_delta_compact(source_mtime, destination_mtime)
                row["motivo_confronto"] = _format_mtime_comparison_reason(source_mtime, destination_mtime)

                destination_is_newer = destination_mtime > source_mtime
                requires_mtime_decision = source_mtime <= destination_mtime
                row["bypass_data_ora_sessione"] = "SI" if session_mtime_policy != SESSION_MTIME_POLICY_ASK else "NO"
                row["controllo_data_ora_eseguito"] = "SI"
                row["destinazione_piu_recente"] = "SI" if destination_is_newer else "NO"
                row["decisione_utente"] = "Aggiornato automaticamente: sorgente più recente"

                _log_line(
                    "[MTIME] Confronto timestamp | "
                    f"sorgente={source_path.name} ({source_mtime:.6f}) | "
                    f"destinazione={destination_path} ({destination_mtime:.6f})"
                )
                _log_line(
                    "[MTIME] Confronto data e ora | "
                    f"sorgente={row['data_ora_sorgente']} | "
                    f"repertorio={row['data_ora_destinazione_precedente']} | "
                    f"differenza={row['differenza_temporale']} | "
                    f"motivo={row['motivo_confronto']}"
                )

                if requires_mtime_decision and session_mtime_policy == SESSION_MTIME_POLICY_UPDATE_ALL:
                    row["controllo_data_ora_eseguito"] = "NO"
                    row["decisione_utente"] = "Aggiornato automaticamente per scelta di sessione"
                    row["motivo"] = "Aggiornamento automatico eseguito per scelta sessione: aggiorna tutti i successivi."
                    _log_line(f"[MTIME] Aggiornamento automatico per policy sessione: {destination_path}")
                elif requires_mtime_decision and session_mtime_policy == SESSION_MTIME_POLICY_SKIP_ALL:
                    row["controllo_data_ora_eseguito"] = "NO"
                    row["decisione_utente"] = "Mantenuto automaticamente per scelta di sessione"
                    row["stato"] = RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value
                    row["motivo"] = "Mantenimento automatico eseguito per scelta sessione: mantieni tutti i successivi."
                    row["backup_eseguito"] = "NO"
                    row["copia_eseguita"] = "NO"
                    row["verifica_finale"] = "NON_ESEGUITA"
                    row["aggiornamento_saltato_per_data_ora"] = "SI"
                    row["copiato_smartphone_tablet"] = "No"
                    counters[RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value] += 1
                    counters[COUNTER_FILE_MANTENUTI] += 1
                    files_not_found += 1
                    _log_line(f"[MTIME] Mantenimento automatico per policy sessione: {destination_path}")
                    rows.append(row)
                    continue
                elif requires_mtime_decision:
                    _log_line(f"[MTIME] Controllo data/ora richiede decisione: {destination_path}")
                    delta_seconds = abs(float(destination_mtime) - float(source_mtime))
                    request_payload = {
                        "source_name": source_path.name,
                        "source_path": str(source_path),
                        "source_size": int(source_size),
                        "source_mtime": float(source_mtime),
                        "source_mtime_human": _format_timestamp_human(source_mtime),
                        "destination_name": destination_path.name,
                        "destination_path": str(destination_path),
                        "destination_size": int(previous_size_int),
                        "destination_mtime": float(destination_mtime),
                        "destination_mtime_human": _format_timestamp_human(destination_mtime),
                        "mtime_delta_seconds": float(delta_seconds),
                        "mtime_delta_human": _format_time_delta_human(delta_seconds),
                        "mtime_delta_compact": row["differenza_temporale"],
                        "comparison_reason": row["motivo_confronto"],
                        "comparison_summary": _format_mtime_summary_sentence(source_mtime, destination_mtime),
                    }
                    _log_line("[MTIME] Apertura richiesta decisione utente")
                    decision = decision_callback(request_payload) if decision_callback is not None else DECISION_SKIP_CURRENT
                    if decision is None or _is_cancelled(cancel_event):
                        interrupted = True
                        row["stato"] = RepertoryStatus.INTERROTTO.value
                        row["motivo"] = "Interrotto durante attesa decisione data/ora."
                        row["decisione_utente"] = "INTERRUPTED"
                        counters[RepertoryStatus.INTERROTTO.value] += 1
                        rows.append(row)
                        _log_line("[MTIME] Interruzione durante attesa decisione")
                        if temp_path is not None:
                            _safe_unlink(temp_path)
                        for pending in updates_files[index:]:
                            pending_key = _normalize_name(pending.name)
                            rows.append(
                                _base_row(
                                    session_id=session_id,
                                    source_path=pending,
                                    normalized_name=pending_key,
                                    status=RepertoryStatus.INTERROTTO,
                                    reason="Non elaborato: operazione interrotta.",
                                    duration_ms=0,
                                )
                            )
                            counters[RepertoryStatus.INTERROTTO.value] += 1
                        processed = index
                        break

                    row["decisione_utente"] = "Aggiornato manualmente"
                    _log_line(f"[MTIME] Scelta utente: {decision}")
                    if decision == DECISION_SKIP_CURRENT:
                        row["decisione_utente"] = "Mantenuto manualmente"
                        row["stato"] = RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value
                        row["motivo"] = "File mantenuto per decisione utente."
                        row["backup_eseguito"] = "NO"
                        row["copia_eseguita"] = "NO"
                        row["verifica_finale"] = "NON_ESEGUITA"
                        row["aggiornamento_saltato_per_data_ora"] = "SI"
                        row["copiato_smartphone_tablet"] = "No"
                        counters[RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value] += 1
                        counters[COUNTER_FILE_MANTENUTI] += 1
                        files_not_found += 1
                        _log_line(f"[MTIME] File saltato per data/ora: {destination_path}")
                        rows.append(row)
                        continue
                    if decision == DECISION_SKIP_AND_BYPASS_SESSION:
                        session_mtime_policy = SESSION_MTIME_POLICY_SKIP_ALL
                        row["bypass_data_ora_sessione"] = "SI"
                        row["decisione_utente"] = "Mantenuto manualmente"
                        row["stato"] = RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value
                        row["motivo"] = "File mantenuto per decisione utente con applicazione ai successivi."
                        row["backup_eseguito"] = "NO"
                        row["copia_eseguita"] = "NO"
                        row["verifica_finale"] = "NON_ESEGUITA"
                        row["aggiornamento_saltato_per_data_ora"] = "SI"
                        row["copiato_smartphone_tablet"] = "No"
                        counters[RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value] += 1
                        counters[COUNTER_FILE_MANTENUTI] += 1
                        files_not_found += 1
                        _log_line("[MTIME] Policy sessione attivata: mantieni tutti i successivi")
                        rows.append(row)
                        continue
                    if decision == DECISION_UPDATE_AND_BYPASS_SESSION:
                        session_mtime_policy = SESSION_MTIME_POLICY_UPDATE_ALL
                        row["bypass_data_ora_sessione"] = "SI"
                        _log_line("[MTIME] Policy sessione attivata: aggiorna tutti i successivi")
                    row["aggiornamento_saltato_per_data_ora"] = "NO"
                    row["motivo"] = "Sostituzione autorizzata dall'utente."
                else:
                    row["aggiornamento_saltato_per_data_ora"] = "NO"

                temp_path = _create_temp_in_same_dir(destination_path)
                shutil.copy2(source_path, temp_path)

                temp_size, temp_hash = _read_size_and_sha256(temp_path)
                if temp_size != source_size or temp_hash != source_hash:
                    raise VerificationError(
                        "Verifica temporaneo fallita: dimensione/hash non corrispondenti."
                    )

                if backup_enabled:
                    rel_path = destination_path.relative_to(repertory_root)
                    backup_target = backup_root / rel_path
                    backup_target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(destination_path, backup_target)
                    except OSError as backup_error:
                        raise BackupError(f"Backup fallito: {backup_error}") from backup_error
                    backup_path = str(backup_target)
                    row["backup_eseguito"] = "SI"
                    row["percorso_backup"] = backup_path
                else:
                    row["backup_eseguito"] = "NO"
                    row["percorso_backup"] = ""

                try:
                    os.replace(str(temp_path), str(destination_path))
                except OSError as replace_error:
                    restore_outcome = "Ripristino non tentato (backup disattivato)."
                    if backup_enabled and backup_path:
                        try:
                            shutil.copy2(backup_path, destination_path)
                            restore_outcome = "Ripristino da backup completato."
                        except OSError as restore_error:
                            restore_outcome = f"Ripristino fallito: {restore_error}"
                    raise CopyError(
                        f"Sostituzione fallita: {replace_error}. {restore_outcome}"
                    ) from replace_error
                else:
                    temp_path = None

                row["copia_eseguita"] = "SI"

                final_size, final_hash = _read_size_and_sha256(destination_path)
                if final_size != source_size or final_hash != source_hash:
                    raise VerificationError(
                        "Verifica finale fallita: dimensione/hash non corrispondenti."
                    )

                row["verifica_finale"] = "SI"
                if not row.get("motivo"):
                    row["motivo"] = ""

                try:
                    relative_path = destination_path.relative_to(repertory_root)
                    smartphone_target = smartphone_tablet_root / relative_path
                    smartphone_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination_path, smartphone_target)
                    copied_size, copied_hash = _read_size_and_sha256(smartphone_target)
                    if copied_size != source_size or copied_hash != source_hash:
                        raise VerificationError(
                            "Verifica copia Smartphone/Tablet fallita: dimensione/hash non corrispondenti."
                        )
                    row["copiato_smartphone_tablet"] = "Si"
                    row["percorso_copia_smartphone_tablet"] = str(smartphone_target)
                    row["errore_copia_smartphone_tablet"] = ""
                    counters[COUNTER_SMARTPHONE_TABLET_COPIATI] += 1
                    _log_line(f"[SMARTPHONE] Copia completata: {smartphone_target}")
                except Exception as smartphone_error:
                    row["copiato_smartphone_tablet"] = "Errore"
                    row["errore_copia_smartphone_tablet"] = str(smartphone_error)
                    try:
                        row["percorso_copia_smartphone_tablet"] = str(smartphone_target)
                    except Exception:
                        row["percorso_copia_smartphone_tablet"] = ""
                    row["motivo"] = _append_note(
                        row.get("motivo", ""),
                        f"Errore copia Smartphone/Tablet: {smartphone_error}",
                    )
                    counters[COUNTER_SMARTPHONE_TABLET_ERRORI] += 1
                    _log_line(f"[SMARTPHONE][ERRORE] {destination_path} | {smartphone_error}")

                if general_sync_enabled and not general_synced_for_source:
                    general_destination = repertory_general_root / source_path.name
                    was_missing_in_general = not general_destination.exists()
                    row["percorso_repertorio_generale"] = str(general_destination)
                    row["file_presente_precedentemente_repertorio_generale"] = "SI" if not was_missing_in_general else "NO"
                    row["backup_repertorio_generale_eseguito"] = "NO"
                    row["percorso_backup_repertorio_generale"] = ""
                    row["esito_backup_repertorio_generale"] = "Non applicabile"
                    row["dettaglio_errore_backup_repertorio_generale"] = ""
                    try:
                        general_destination.parent.mkdir(parents=True, exist_ok=True)

                        if not was_missing_in_general:
                            if backup_enabled:
                                backup_general_folder = backup_root / REPERTORY_GENERAL_TECH_FOLDER_NAME
                                try:
                                    backup_general_target = _copy_with_collision_suffix(
                                        source_path=general_destination,
                                        destination_folder=backup_general_folder,
                                        preferred_name=general_destination.name,
                                    )
                                except OSError as backup_copy_error:
                                    raise BackupError(
                                        f"Backup Repertorio Generale fallito: {backup_copy_error}"
                                    ) from backup_copy_error
                                original_general_size, original_general_hash = _read_size_and_sha256(general_destination)
                                backup_general_size, backup_general_hash = _read_size_and_sha256(backup_general_target)
                                if original_general_size != backup_general_size or original_general_hash != backup_general_hash:
                                    raise BackupError(
                                        "Verifica backup Repertorio Generale fallita: dimensione/hash non corrispondenti."
                                    )
                                row["backup_repertorio_generale_eseguito"] = "SI"
                                row["percorso_backup_repertorio_generale"] = str(backup_general_target)
                                row["esito_backup_repertorio_generale"] = "OK"
                                if backup_general_target.name != general_destination.name:
                                    row["motivo"] = _append_note(
                                        row.get("motivo", ""),
                                        f"Collisione nome nel backup generale: salvato come {backup_general_target.name}",
                                    )
                                    _log_line(
                                        "[BACKUP][GENERALE][COLLISIONE] "
                                        f"sorgente={general_destination.name} salvato={backup_general_target.name}"
                                    )
                            else:
                                row["esito_backup_repertorio_generale"] = "Backup disattivato"

                        shutil.copy2(source_path, general_destination)
                        general_size, general_hash = _read_size_and_sha256(general_destination)
                        if general_size != source_size or general_hash != source_hash:
                            raise VerificationError(
                                "Verifica copia Repertorio Generale fallita: dimensione/hash non corrispondenti."
                            )

                        smartphone_general_target = smartphone_tablet_root / REPERTORY_GENERAL_TECH_FOLDER_NAME / source_path.name
                        smartphone_general_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(general_destination, smartphone_general_target)
                        smart_general_size, smart_general_hash = _read_size_and_sha256(smartphone_general_target)
                        if smart_general_size != source_size or smart_general_hash != source_hash:
                            raise VerificationError(
                                "Verifica copia Smartphone/Tablet Repertorio Generale fallita: dimensione/hash non corrispondenti."
                            )

                        row["repertorio_generale_aggiornato"] = "Si"
                        row["percorso_copia_smartphone_repertorio_generale"] = str(smartphone_general_target)
                        row["errore_copia_smartphone_repertorio_generale"] = ""
                        if was_missing_in_general:
                            note_text = "Inserito automaticamente nel Repertorio Generale perche precedentemente assente."
                            row["motivo"] = _append_note(row.get("motivo", ""), note_text)
                            _log_line(f"[GENERALE] {note_text} | file={source_path.name}")
                        counters[COUNTER_SMARTPHONE_TABLET_COPIATI] += 1
                        counters[COUNTER_COPIE_AGGIORNATE_REPERTORIO_GENERALE] += 1
                        source_had_successful_update = True
                        _log_line(f"[GENERALE] Aggiornato: {general_destination}")
                        _log_line(f"[SMARTPHONE][GENERALE] Copia completata: {smartphone_general_target}")
                    except Exception as general_error:
                        row["repertorio_generale_aggiornato"] = "Errore"
                        row["errore_copia_smartphone_repertorio_generale"] = str(general_error)
                        if isinstance(general_error, BackupError):
                            row["esito_backup_repertorio_generale"] = "Errore"
                            row["dettaglio_errore_backup_repertorio_generale"] = str(general_error)
                            row["motivo"] = _append_note(
                                row.get("motivo", ""),
                                f"Errore backup Repertorio Generale: {general_error}",
                            )
                            row["stato"] = RepertoryStatus.ERRORE_BACKUP.value
                            counters[RepertoryStatus.ERRORE_BACKUP.value] += 1
                        else:
                            row["stato"] = RepertoryStatus.ERRORE_COPIA.value
                            counters[RepertoryStatus.ERRORE_COPIA.value] += 1
                            counters[COUNTER_SMARTPHONE_TABLET_ERRORI] += 1
                        row["motivo"] = _append_note(
                            row.get("motivo", ""),
                            f"Errore aggiornamento Repertorio Generale: {general_error}",
                        )
                        row["verifica_finale"] = "NO"
                        errors_count += 1
                        _log_line(f"[GENERALE][ERRORE] {source_path.name} | {general_error}")
                    general_synced_for_source = True

                if row.get("stato") == status_if_ok.value:
                    counters[status_if_ok.value] += 1
                    counters[COUNTER_COPIE_AGGIORNATE_REPERTORIO] += 1
                    files_updated += 1
                    source_had_successful_update = True
                    _log_line(f"[{status_if_ok.value}] {source_path.name} -> {destination_path}")
            except VerificationError as exc:
                row["stato"] = RepertoryStatus.ERRORE_VERIFICA.value
                row["motivo"] = str(exc)
                row["verifica_finale"] = "NO"
                counters[RepertoryStatus.ERRORE_VERIFICA.value] += 1
                errors_count += 1
                _log_line(f"[ERRORE] {source_path.name} | ERRORE_VERIFICA | {exc}")
            except CopyError as exc:
                row["stato"] = RepertoryStatus.ERRORE_COPIA.value
                row["motivo"] = str(exc)
                row["verifica_finale"] = "NO"
                counters[RepertoryStatus.ERRORE_COPIA.value] += 1
                errors_count += 1
                _log_line(f"[ERRORE] {source_path.name} | ERRORE_COPIA | {exc}")
            except BackupError as exc:
                row["stato"] = RepertoryStatus.ERRORE_BACKUP.value
                row["motivo"] = str(exc)
                row["verifica_finale"] = "NO"
                counters[RepertoryStatus.ERRORE_BACKUP.value] += 1
                errors_count += 1
                _log_line(f"[ERRORE] {source_path.name} | ERRORE_BACKUP | {exc}")
            except OSError as exc:
                row["stato"] = RepertoryStatus.ERRORE_COPIA.value
                counters[RepertoryStatus.ERRORE_COPIA.value] += 1
                _log_line(f"[ERRORE] {source_path.name} | ERRORE_COPIA | {exc}")
                row["motivo"] = str(exc)
                row["verifica_finale"] = "NO"
                errors_count += 1
            finally:
                if temp_path is not None:
                    _safe_unlink(temp_path)
                row["durata_ms"] = str(int(max(0.0, (time.monotonic() - txn_start) * 1000.0)))
                if not any(existing is row for existing in rows):
                    rows.append(row)

            if interrupted:
                break

        if interrupted:
            break

        if source_had_successful_update:
            counters[COUNTER_BRANI_AGGIORNATI] += 1

        processed += 1
        _emit_progress(
            progress_callback,
            current=processed,
            total=total,
            current_name=source_path.name,
            matches_found=matches_found,
            files_updated=files_updated,
            files_not_found=files_not_found,
            errors_count=errors_count,
            started_at=start_time,
        )

    elapsed = max(0.0, time.monotonic() - start_time)

    report_paths = _write_reports(report_root=report_root, rows=rows)
    _write_log(log_path=log_path, lines=logs)
    _cleanup_empty_dirs(session_root)

    return RepertoryOrganizeResult(
        success=not interrupted and all(row["stato"] not in {
            RepertoryStatus.ERRORE_SORGENTE.value,
            RepertoryStatus.ERRORE_BACKUP.value,
            RepertoryStatus.ERRORE_COPIA.value,
            RepertoryStatus.ERRORE_VERIFICA.value,
        } for row in rows),
        interrupted=interrupted,
        error="\n".join(backup_preflight_errors) if backup_preflight_errors else None,
        total_source_files=total,
        processed_source_files=processed,
        elapsed_seconds=elapsed,
        counters=counters,
        session_folder=str(session_root),
        report_paths=report_paths,
        log_path=str(log_path),
        smartphone_tablet_root=str(smartphone_tablet_root),
        repertory_not_found_dir=str(repertory_not_found_dir_path) if repertory_not_found_dir_path is not None else "",
        repertory_to_insert_dir=str(repertory_to_insert_dir_path) if repertory_to_insert_dir_path is not None else "",
    )


def resolve_smartphone_tablet_root(path_override: str | Path | None = None) -> Path:
    if path_override is None:
        return SMARTPHONE_TABLET_ROOT.expanduser().resolve()
    return Path(path_override).expanduser().resolve()


def _validate_roots(*, updates_root: Path, repertory_root: Path, repertory_general_root: Path, results_root: Path, smartphone_tablet_root: Path) -> None:
    if not updates_root.is_dir():
        raise RuntimeError(f"Cartella aggiornamenti non valida: {updates_root}")
    if not repertory_root.is_dir():
        raise RuntimeError(f"Cartella repertorio non valida: {repertory_root}")
    if not repertory_general_root.is_dir():
        raise RuntimeError(f"Cartella repertorio generale non valida: {repertory_general_root}")

    results_root.mkdir(parents=True, exist_ok=True)

    _assert_readable_dir(updates_root)
    _assert_readable_dir(repertory_root)
    _assert_readable_dir(repertory_general_root)
    _assert_writable_dir(repertory_general_root)
    _assert_writable_dir(results_root)
    assert_smartphone_tablet_dir_accessible(smartphone_tablet_root, require_exists=True)


def assert_smartphone_tablet_dir_accessible(path: str | Path, *, require_exists: bool = True) -> None:
    target = Path(path).expanduser().resolve()
    if require_exists and not target.is_dir():
        raise RuntimeError(f"Cartella Smartphone/Tablet non valida: {target}")
    if not target.exists() and not require_exists:
        return
    if not target.is_dir():
        raise RuntimeError(f"Il percorso Smartphone/Tablet non e una cartella: {target}")
    _assert_readable_dir(target)
    _assert_writable_dir(target)


def reset_smartphone_tablet_dir(path: str | Path, *, expected_root: str | Path | None = None) -> tuple[int, int]:
    raw_path = str(path or "").strip()
    if not raw_path:
        raise RuntimeError("Cartella Smartphone/Tablet non valida: percorso vuoto.")

    target = Path(raw_path).expanduser().resolve()
    if expected_root is not None:
        expected = Path(expected_root).expanduser().resolve()
        if target != expected:
            raise RuntimeError(
                "Operazione annullata: il percorso richiesto non coincide con la cartella Smartphone/Tablet configurata."
            )

    if target.parent == target:
        raise RuntimeError(
            "Operazione annullata: il reset della root del disco non e consentito."
        )

    if not target.is_dir():
        raise RuntimeError(f"Cartella Smartphone/Tablet non trovata: {target}")

    return _delete_dir_children_no_follow(target)


def _assert_readable_dir(path: Path) -> None:
    try:
        with os.scandir(path) as iterator:
            for _entry in iterator:
                break
    except OSError as exc:
        raise RuntimeError(f"Cartella non accessibile in lettura: {path} ({exc})") from exc


def _assert_writable_dir(path: Path) -> None:
    probe = path / f".probe_write_{int(time.time() * 1000)}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cartella non accessibile in scrittura: {path} ({exc})") from exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def _is_cancelled(cancel_event: object | None) -> bool:
    return bool(cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set())


def _scan_mp3_non_recursive(root: Path) -> list[Path]:
    found: list[Path] = []
    try:
        entries = sorted(list(os.scandir(root)), key=lambda entry: entry.name.casefold())
    except OSError:
        return []

    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        path = Path(entry.path)
        if path.suffix.casefold() != ".mp3":
            continue
        found.append(path.resolve())

    return found


def _scan_mp3_recursive(root: Path) -> list[Path]:
    found: list[Path] = []
    for current_root, _dirs, files in os.walk(root):
        for name in files:
            if Path(name).suffix.casefold() != ".mp3":
                continue
            found.append((Path(current_root) / name).resolve())
    found.sort(key=lambda path: str(path).casefold())
    return found


def _normalize_name(file_name: str) -> str:
    nfc_name = unicodedata.normalize("NFC", str(file_name))
    stripped = nfc_name.strip()
    return stripped.casefold()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_size_and_sha256(path: Path) -> tuple[int, str]:
    file_size = path.stat().st_size
    file_hash = _sha256_file(path)
    return file_size, file_hash


def _create_temp_in_same_dir(destination_path: Path) -> Path:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".tmp.mp3",
        prefix=f".{destination_path.stem}_",
        dir=str(destination_path.parent),
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _new_counters() -> dict[str, int]:
    return {
        RepertoryStatus.AGGIORNATO.value: 0,
        RepertoryStatus.AGGIORNATO_MULTIPLO.value: 0,
        RepertoryStatus.NON_TROVATO.value: 0,
        RepertoryStatus.ERRORE_SORGENTE.value: 0,
        RepertoryStatus.ERRORE_BACKUP.value: 0,
        RepertoryStatus.ERRORE_COPIA.value: 0,
        RepertoryStatus.ERRORE_VERIFICA.value: 0,
        RepertoryStatus.AMBIGUO.value: 0,
        RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value: 0,
        RepertoryStatus.INTERROTTO.value: 0,
        COUNTER_SMARTPHONE_TABLET_COPIATI: 0,
        COUNTER_SMARTPHONE_TABLET_ERRORI: 0,
        COUNTER_FILE_MANTENUTI: 0,
        COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO: 0,
        COUNTER_FILE_NON_TROVATI_COPIATI: 0,
        COUNTER_FILE_NON_TROVATI_ERRORI_COPIA: 0,
        COUNTER_BRANI_DA_INSERIRE: 0,
        COUNTER_BRANI_DA_INSERIRE_ERRORI: 0,
        COUNTER_BRANI_AGGIORNATI: 0,
        COUNTER_COPIE_AGGIORNATE_REPERTORIO: 0,
        COUNTER_COPIE_AGGIORNATE_REPERTORIO_GENERALE: 0,
    }


def _base_row(
    *,
    session_id: str,
    source_path: Path,
    normalized_name: str,
    status: RepertoryStatus,
    reason: str = "",
    matches: int = 0,
    destination_path: Path | None = None,
    source_size: str = "",
    source_hash: str = "",
    source_mtime: float | None = None,
    duration_ms: int = 0,
) -> dict[str, str]:
    return {
        "session_id": session_id,
        "data_ora": datetime.now().isoformat(timespec="seconds"),
        "file_sorgente": source_path.name,
        "percorso_sorgente": str(source_path),
        "file_repertorio": destination_path.name if destination_path is not None else "",
        "nome_normalizzato": normalized_name,
        "numero_corrispondenze": str(matches),
        "percorso_destinazione": str(destination_path) if destination_path is not None else "",
        "dimensione_sorgente": source_size,
        "hash_sorgente": source_hash,
        "data_ora_sorgente": _format_timestamp_human(source_mtime),
        "timestamp_sorgente": _format_timestamp_number(source_mtime),
        "dimensione_precedente": "",
        "hash_precedente": "",
        "data_ora_destinazione_precedente": "",
        "timestamp_destinazione_precedente": "",
        "differenza_temporale": "",
        "motivo_confronto": "",
        "destinazione_piu_recente": "NO",
        "controllo_data_ora_eseguito": "NO",
        "decisione_utente": "",
        "copiato_smartphone_tablet": "Non applicabile",
        "percorso_copia_smartphone_tablet": "",
        "errore_copia_smartphone_tablet": "",
        "repertorio_generale_aggiornato": "No",
        "percorso_repertorio_generale": "",
        "file_presente_precedentemente_repertorio_generale": "NO",
        "backup_repertorio_generale_eseguito": "NO",
        "percorso_backup_repertorio_generale": "",
        "esito_backup_repertorio_generale": "Non applicabile",
        "dettaglio_errore_backup_repertorio_generale": "",
        "percorso_copia_smartphone_repertorio_generale": "",
        "errore_copia_smartphone_repertorio_generale": "",
        "percorso_file_non_trovato": "",
        "bypass_data_ora_sessione": "NO",
        "aggiornamento_saltato_per_data_ora": "NO",
        "backup_eseguito": "NO",
        "percorso_backup": "",
        "copia_eseguita": "NO",
        "verifica_finale": "NO",
        "stato": status.value,
        "motivo": reason,
        "durata_ms": str(max(0, int(duration_ms))),
    }


def _emit_progress(
    callback: OrganizeProgressCallback | None,
    *,
    current: int,
    total: int,
    current_name: str,
    matches_found: int,
    files_updated: int,
    files_not_found: int,
    errors_count: int,
    started_at: float,
) -> None:
    if callback is None:
        return
    elapsed = int(max(0.0, time.monotonic() - started_at))
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    elapsed_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    message = (
        f"Elaborazione {current}/{total} - {current_name} | "
        f"match={matches_found} aggiornati={files_updated} non_trovati={files_not_found} errori={errors_count} tempo={elapsed_text}"
    )
    callback(current, total, message)


def _write_reports(*, report_root: Path, rows: list[dict[str, str]]) -> dict[str, str]:
    report_root.mkdir(parents=True, exist_ok=True)
    csv_path = report_root / "Organizzazione_Repertorio.csv"
    html_path = report_root / "Organizzazione_Repertorio.html"
    xlsx_path = report_root / "Organizzazione_Repertorio.xlsx"

    _write_csv(csv_path, rows)
    _write_html(html_path, rows)
    _write_xlsx(xlsx_path, rows)

    return {
        "csv": str(csv_path),
        "html": str(html_path),
        "xlsx": str(xlsx_path),
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    headers = _resolve_report_headers(rows)
    labels = [_report_header_label(key) for key in headers]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(labels)
        for row in rows:
            writer.writerow([str(row.get(key, "")) for key in headers])


def _write_html(path: Path, rows: list[dict[str, str]]) -> None:
    headers = _resolve_report_headers(rows)
    labels = [_report_header_label(key) for key in headers]
    lines = [
        "<!doctype html>",
        "<html lang='it'>",
        "<head>",
        "  <meta charset='utf-8'>",
        "  <title>Organizzazione repertorio</title>",
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
        "  <h1>Organizzazione repertorio</h1>",
        f"  <p>Generato: {datetime.now().isoformat(timespec='seconds')}</p>",
    ]

    if not headers:
        lines.append("<p>Nessun dato.</p>")
    else:
        lines.append("<table>")
        lines.append("<thead><tr>" + "".join(f"<th>{html.escape(label)}</th>" for label in labels) + "</tr></thead>")
        lines.append("<tbody>")
        for row in rows:
            lines.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers) + "</tr>")
        lines.append("</tbody></table>")

    lines.extend(["</body>", "</html>"])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
    headers = _resolve_report_headers(rows)
    labels = [_report_header_label(key) for key in headers]

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
        "<sheets><sheet name='Organizzazione' sheetId='1' r:id='rId1'/></sheets></workbook>"
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

    sheet_xml = _build_sheet_xml(headers, labels, rows)

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _build_sheet_xml(headers: list[str], labels: list[str], rows: list[dict[str, str]]) -> str:
    lines = [
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>",
    ]

    if headers:
        lines.append("<sheetViews><sheetView workbookViewId='0'><pane ySplit='1' topLeftCell='A2' activePane='bottomLeft' state='frozen'/></sheetView></sheetViews>")
        lines.append("<cols>")
        for index, header in enumerate(headers, start=1):
            width = _xlsx_column_width(header)
            lines.append(f"<col min='{index}' max='{index}' width='{width:.2f}' customWidth='1'/>")
        lines.append("</cols>")

    lines.append("<sheetData>")

    if headers:
        lines.append("<row r='1'>")
        for index, label in enumerate(labels, start=1):
            col = _xlsx_col(index)
            lines.append(f"<c r='{col}1' s='1' t='inlineStr'><is><t>{html.escape(label)}</t></is></c>")
        lines.append("</row>")

    for row_index, row in enumerate(rows, start=2):
        lines.append(f"<row r='{row_index}'>")
        for col_index, header in enumerate(headers, start=1):
            col = _xlsx_col(col_index)
            value = html.escape(str(row.get(header, "")))
            lines.append(f"<c r='{col}{row_index}' s='{_xlsx_cell_style_id(header)}' t='inlineStr'><is><t>{value}</t></is></c>")
        lines.append("</row>")

    lines.append("</sheetData>")
    if headers:
        last_col = _xlsx_col(len(headers))
        last_row = max(1, len(rows) + 1)
        lines.append(f"<autoFilter ref='A1:{last_col}{last_row}'/>")
    lines.append("</worksheet>")
    return "".join(lines)


def _resolve_report_headers(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return list(REPORT_HEADERS)

    present: set[str] = set()
    extras: list[str] = []
    for row in rows:
        for key in row.keys():
            if key in present:
                continue
            present.add(key)
            if key not in REPORT_HEADERS:
                extras.append(key)

    ordered = [header for header in REPORT_HEADERS if header in present]
    ordered.extend(extras)
    return ordered


def _report_header_label(key: str) -> str:
    return REPORT_HEADER_LABELS.get(key, key)


def _xlsx_column_width(header: str) -> float:
    very_wide = {
        "percorso_sorgente",
        "percorso_destinazione",
        "percorso_backup",
        "percorso_backup_repertorio_generale",
        "percorso_copia_smartphone_tablet",
        "percorso_file_non_trovato",
        "errore_copia_smartphone_tablet",
        "dettaglio_errore_backup_repertorio_generale",
        "errore_copia_smartphone_repertorio_generale",
    }
    wide = {"motivo", "differenza_temporale", "motivo_confronto"}
    medium = {
        "file_sorgente",
        "file_repertorio",
        "data_ora_sorgente",
        "data_ora_destinazione_precedente",
        "timestamp_sorgente",
        "timestamp_destinazione_precedente",
        "stato",
        "decisione_utente",
        "copiato_smartphone_tablet",
        "file_presente_precedentemente_repertorio_generale",
        "backup_repertorio_generale_eseguito",
        "esito_backup_repertorio_generale",
    }
    hash_cols = {"hash_sorgente", "hash_precedente"}
    if header in very_wide:
        return 72.0
    if header in hash_cols:
        return 68.0
    if header in wide:
        return 46.0
    if header in medium:
        return 26.0
    return 18.0


def _xlsx_col(index: int) -> str:
    col = ""
    value = index
    while value > 0:
        value, rem = divmod(value - 1, 26)
        col = chr(65 + rem) + col
    return col


def _write_log(*, log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _cleanup_empty_dirs(session_root: Path) -> None:
    if not session_root.exists() or not session_root.is_dir():
        return

    all_dirs = [path for path in session_root.rglob("*") if path.is_dir()]
    all_dirs.sort(key=lambda path: len(path.parts), reverse=True)
    for directory in all_dirs:
        try:
            if any(directory.iterdir()):
                continue
            directory.rmdir()
        except OSError:
            pass


class VerificationError(RuntimeError):
    pass


class BackupError(RuntimeError):
    pass


class CopyError(RuntimeError):
    pass


def _format_timestamp_human(value: float | None) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return ""


def _format_timestamp_number(value: float | None) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except Exception:
        return ""


def _format_time_delta_human(delta_seconds: float) -> str:
    total = int(max(0.0, float(delta_seconds)))
    if total <= 0:
        return "Stessa data e ora"

    units = [
        (365 * 24 * 3600, "anno", "anni"),
        (30 * 24 * 3600, "mese", "mesi"),
        (24 * 3600, "giorno", "giorni"),
        (3600, "ora", "ore"),
        (60, "minuto", "minuti"),
        (1, "secondo", "secondi"),
    ]

    remaining = total
    parts: list[str] = []
    for unit_seconds, singular, plural in units:
        if remaining < unit_seconds:
            continue
        qty, remaining = divmod(remaining, unit_seconds)
        label = singular if qty == 1 else plural
        parts.append(f"{qty} {label}")

    if not parts:
        return "Stessa data e ora"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} e {parts[1]}"
    return ", ".join(parts[:-1]) + f" e {parts[-1]}"


def _format_mtime_delta_compact(source_mtime: float, destination_mtime: float) -> str:
    source = float(source_mtime)
    destination = float(destination_mtime)
    delta = abs(destination - source)
    if delta <= 0.0:
        return "Stessa data e ora"
    return _format_time_delta_human(delta)


def _format_mtime_comparison_reason(source_mtime: float, destination_mtime: float) -> str:
    source = float(source_mtime)
    destination = float(destination_mtime)
    if source > destination:
        return "File della cartella Aggiornamenti più recente"
    if source < destination:
        return "File del repertorio più recente"
    return "Stessa data e ora di modifica"


def _format_mtime_summary_sentence(source_mtime: float, destination_mtime: float) -> str:
    source = float(source_mtime)
    destination = float(destination_mtime)
    delta_compact = _format_mtime_delta_compact(source, destination)
    if source > destination:
        return (
            "Il file nella cartella Aggiornamenti è più recente "
            f"di quello presente nel Repertorio di {delta_compact}."
        )
    if source < destination:
        return (
            "Il file nella cartella Aggiornamenti è più vecchio "
            f"di quello presente nel Repertorio di {delta_compact}."
        )
    return (
        "Il file nella cartella Aggiornamenti e il file presente nel Repertorio "
        "hanno la stessa data e ora di modifica."
    )


def _append_note(base_text: str, addition: str) -> str:
    base = str(base_text or "").strip()
    extra = str(addition or "").strip()
    if not base:
        return extra
    if not extra:
        return base
    return f"{base} | {extra}"


def _ensure_repertory_non_trovati_dir(repertory_root: Path) -> Path:
    root = repertory_root.expanduser().resolve()
    target = root / REPERTORY_NON_TROVATI_FOLDER_NAME

    if target.exists():
        if target.is_symlink() or _is_reparse_point(target):
            raise RuntimeError(
                f"Cartella non sicura per file non trovati (link/reparse non consentito): {target}"
            )
        if not target.is_dir():
            raise RuntimeError(f"Percorso non valido per file non trovati: {target}")
    else:
        target.mkdir(parents=False, exist_ok=True)

    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"Percorso file non trovati non valido: deve rimanere sotto la radice repertorio ({root})"
        ) from exc
    return resolved


def _ensure_repertory_to_insert_dir(session_root: Path) -> Path:
    target = session_root / REPERTORY_TO_INSERT_FOLDER_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def _copy_with_collision_suffix(source_path: Path, destination_folder: Path, preferred_name: str) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    base_name = Path(preferred_name).name
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix

    candidate = destination_folder / base_name
    index = 1
    while candidate.exists():
        candidate = destination_folder / f"{stem} ({index}){suffix}"
        index += 1

    shutil.copy2(source_path, candidate)
    return candidate


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_point and (attributes & reparse_point) == reparse_point)


def _delete_dir_children_no_follow(root: Path) -> tuple[int, int]:
    deleted_files = 0
    deleted_dirs = 0
    with os.scandir(root) as entries:
        for entry in entries:
            entry_path = Path(entry.path)
            if entry.is_symlink() or _is_reparse_point(entry_path):
                if entry.is_dir(follow_symlinks=False):
                    os.rmdir(entry.path)
                    deleted_dirs += 1
                else:
                    os.unlink(entry.path)
                    deleted_files += 1
                continue

            if entry.is_dir(follow_symlinks=False):
                nested_files, nested_dirs = _delete_dir_children_no_follow(entry_path)
                deleted_files += nested_files
                deleted_dirs += nested_dirs
                os.rmdir(entry.path)
                deleted_dirs += 1
            else:
                os.unlink(entry.path)
                deleted_files += 1
    return deleted_files, deleted_dirs


def _xlsx_cell_style_id(header: str) -> int:
    wrapped_headers = {
        "percorso_sorgente",
        "percorso_destinazione",
        "differenza_temporale",
        "motivo_confronto",
        "motivo",
        "percorso_backup",
        "percorso_copia_smartphone_tablet",
        "percorso_file_non_trovato",
        "errore_copia_smartphone_tablet",
    }
    if header in wrapped_headers:
        return 2
    return 0
