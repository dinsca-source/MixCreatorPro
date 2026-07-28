# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Any
from zipfile import ZIP_DEFLATED, ZipFile

from ffmpeg_manager import FFmpegError, FFmpegManager
from mp3_diagnostics import MP3DiagnosticsEngine
from mp3_recovery import MP3RecoveryStatus, RecoveryMode, recover_mp3_from_original
from mp3_recovery_telemetry import HashTelemetrySink, RecoveryTelemetrySession
from session_summary import build_session_configuration_header, clean_path_value, format_si_no
from winlive_validation import AudioHashStatus, compute_mpeg_audio_hash

BatchProgressCallback = Callable[[int, int, str], None]
BatchLogCallback = Callable[[str], None]

CHECK_COMPATIBLE = "Compatibile"
CHECK_PROBABLY_COMPATIBLE = "Probabilmente compatibile"
CHECK_INCOMPATIBLE = "Incompatibile"
CHECK_NOT_DETERMINABLE = "Non determinabile"
CHECK_TECHNICAL_ERROR = "Errore tecnico"
CHECK_NOT_APPLICABLE = "Non applicabile"

OVERALL_COMPATIBLE = "Originale compatibile"
OVERALL_INCOMPATIBLE = "Originale incompatibile"
OVERALL_NOT_DETERMINABLE = "Compatibilita non determinabile"

DURATION_TOLERANCE_MS = 500
TEMPO_TOLERANCE_PERCENT = 1.0


def _is_cancelled(cancel_event: object | None) -> bool:
    return bool(cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set())


class MP3BatchOutcome(str, Enum):
    RECOVERED_TAGS = "Recuperato con TAG WinLive trasferiti"
    RECOVERED_UNCHANGED = "Recuperato come copia originale invariata"
    RECOVERED_FORCED = "Recuperato forzatamente"
    ORIGINAL_NOT_FOUND = "Originale non trovato"
    ORIGINAL_INCOMPATIBLE = "Originale incompatibile"
    MULTIPLE_COMPATIBLE_ORIGINALS = "Piu originali compatibili"
    MULTIPLE_SAME_NAME_ORIGINALS = "Piu originali con lo stesso nome"
    READ_ERROR = "Errore lettura"
    WRITE_ERROR = "Errore scrittura"
    FINAL_VERIFICATION_FAILED = "Verifica finale fallita"
    INTERRUPTED = "Interrotto"
    ERROR = "Errore"


OUTCOME_FOLDERS: dict[MP3BatchOutcome, str] = {
    MP3BatchOutcome.RECOVERED_TAGS: "Recuperati con TAG WinLive trasferiti",
    MP3BatchOutcome.RECOVERED_UNCHANGED: "Recuperati come copia originale invariata",
    MP3BatchOutcome.RECOVERED_FORCED: "Recuperati forzatamente",
    MP3BatchOutcome.ORIGINAL_NOT_FOUND: "Originale non trovato",
    MP3BatchOutcome.ORIGINAL_INCOMPATIBLE: "Originale incompatibile",
    MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS: "Piu originali compatibili",
    MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS: "Piu originali compatibili",
    MP3BatchOutcome.READ_ERROR: "Errori",
    MP3BatchOutcome.WRITE_ERROR: "Errori",
    MP3BatchOutcome.FINAL_VERIFICATION_FAILED: "Errori",
    MP3BatchOutcome.INTERRUPTED: "Interrotti",
    MP3BatchOutcome.ERROR: "Errori",
}


@dataclass(slots=True)
class MP3BatchItemResult:
    index: int
    problematic_name: str
    problematic_path: str
    original_name: str
    original_path: str
    outcome: MP3BatchOutcome
    strategy: str
    problematic_winlive_present: str
    original_winlive_present: str
    recovered_path: str
    duration_seconds: float
    note: str
    original_unchanged: str
    problematic_audio_hash: str
    original_audio_hash: str
    recovery_mode: str = "normal"
    forced_recovery: bool = False
    audio_comparison_executed: bool = True
    audio_comparison_reason: str = ""
    search_original_seconds: float = 0.0
    hash_problematic_seconds: float = 0.0
    hash_original_seconds: float = 0.0
    recovery_seconds: float = 0.0
    verification_final_seconds: float = 0.0
    total_file_seconds: float = 0.0
    session_folder: str = ""
    outcome_folder_path: str = ""
    esito_json_path: str = ""
    last_completed_phase: str = ""
    error_detail: str = ""
    copied_problematic_path: str = ""
    problematic_copy_created: bool = False
    problematic_copy_byte_identical: bool = False
    compatibility_analysis: dict[str, Any] = field(default_factory=dict)
    compatibility_problem_rows: list[dict[str, str]] = field(default_factory=list)
    integrity_policy_assessed: bool = False
    integrity_certified: bool = False
    integrity_classification_reason: str = ""
    blocking_issues_count: int = 0
    non_blocking_tail_issues_count: int = 0


@dataclass(slots=True)
class MP3RecoveryBatchResult:
    success: bool
    interrupted: bool
    error: str | None
    total_problematic: int
    processed_problematic: int
    counters: dict[str, int]
    elapsed_seconds: float
    report_paths: dict[str, str]
    output_root: str
    items: list[MP3BatchItemResult]
    originals_unchanged: bool
    examined_problematic: int = 0
    completed_problematic: int = 0
    session_folder: str = ""


@dataclass(slots=True)
class _HashLookupResult:
    status: AudioHashStatus
    audio_hash_sha256: str | None
    frames_count: int = 0
    first_frame_offset: int | None = None
    last_frame_end_offset: int | None = None
    anomalies: list[str] = field(default_factory=list)


def recover_mp3_batch_from_folders(
    *,
    problematic_dir: str | Path,
    originals_dir: str | Path,
    destination_dir: str | Path,
    progress_callback: BatchProgressCallback | None = None,
    log_callback: BatchLogCallback | None = None,
    cancel_event: object | None = None,
    recovery_mode: RecoveryMode | str = RecoveryMode.NORMAL,
    session_snapshot: dict[str, Any] | None = None,
) -> MP3RecoveryBatchResult:
    recovery_mode = RecoveryMode.coerce(recovery_mode)
    start_time = time.monotonic()

    problematic_root = Path(problematic_dir).expanduser().resolve()
    originals_root = Path(originals_dir).expanduser().resolve()
    destination_root = Path(destination_dir).expanduser().resolve()
    session_snapshot = _freeze_recovery_session_snapshot(
        snapshot=session_snapshot,
        problematic_root=problematic_root,
        originals_root=originals_root,
        destination_root=destination_root,
        recovery_mode=recovery_mode,
    )

    _validate_paths(problematic_root, originals_root, destination_root)

    output_root = destination_root
    session_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_root = output_root / f"Diagnosi Recupero {session_timestamp}"
    esiti_root = session_root / "Esito Recupero File"
    report_root = session_root / "Report"
    diagnostics_root_name = "Diagnostica Scanner MPEG"

    output_root.mkdir(parents=True, exist_ok=True)
    session_root.mkdir(parents=True, exist_ok=True)
    esiti_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    folder_map = _ensure_output_structure(esiti_root)
    telemetry = RecoveryTelemetrySession(
        session_root,
        log_callback,
        session_timestamp=session_timestamp,
        diagnostics_dir_name=diagnostics_root_name,
    )

    try:
        _log(log_callback, f"[TECH] Inizio batch | problematici={problematic_root} | originali={originals_root} | destinazione={output_root}")
        _log(log_callback, f"[TECH] Sessione esiti creata | path={session_root}")

        problematic_files = _scan_mp3_files_non_recursive(problematic_root)
        originals_files = _scan_mp3_files_non_recursive(originals_root)

        _log(log_callback, f"[TECH] Conteggio problematici={len(problematic_files)}")
        _log(log_callback, f"[TECH] Conteggio originali={len(originals_files)}")

        if not problematic_files:
            telemetry.emit_event("ERROR", phase="Batch", message="Nessun file MP3 trovato nella cartella dei problematici.", critical=True)
            raise RuntimeError("Nessun file MP3 trovato nella cartella dei problematici.")
        if not originals_files:
            telemetry.emit_event("ERROR", phase="Batch", message="Nessun file MP3 trovato nella cartella degli originali integri.", critical=True)
            raise RuntimeError("Nessun file MP3 trovato nella cartella degli originali integri.")

        original_index = _build_original_index_by_full_name(originals_files)
        original_audio_hash_cache: dict[Path, _HashLookupResult] = {}
        problematic_audio_hash_cache: dict[Path, _HashLookupResult] = {}
        duration_cache: dict[Path, tuple[int | None, str | None]] = {}

        original_snapshots_before = {path: _snapshot_file(path) for path in originals_files}

        total = len(problematic_files)
        counters = _new_counters()
        items: list[MP3BatchItemResult] = []
        interrupted = False
        examined_problematic = 0
        completed_problematic = 0

        processed_paths: set[str] = set()

        def _finalize_item_state(
            item: MP3BatchItemResult,
            *,
            last_phase: str,
            batch_status: str,
            files_examined: int,
            files_completed: int,
            originals_unchanged: bool,
        ) -> None:
            item.session_folder = str(session_root)
            item.outcome_folder_path = str(folder_map[item.outcome])
            item.last_completed_phase = last_phase
            if item.outcome == MP3BatchOutcome.ORIGINAL_NOT_FOUND and not item.original_path:
                item.original_unchanged = "N/D"
            else:
                item.original_unchanged = "SI" if originals_unchanged else "NO"

            if not item.recovered_path:
                problematic_source = Path(item.problematic_path) if item.problematic_path else None
                if problematic_source is not None and problematic_source.exists() and problematic_source.is_file():
                    try:
                        copied_target = _copy_with_collision(
                            problematic_source,
                            Path(item.outcome_folder_path),
                            item.problematic_name,
                        )
                        item.copied_problematic_path = str(copied_target)
                        item.problematic_copy_created = True
                        item.problematic_copy_byte_identical = _files_byte_identical(problematic_source, copied_target)
                    except OSError as copy_error:
                        item.problematic_copy_created = False
                        item.problematic_copy_byte_identical = False
                        item.error_detail = str(copy_error)
                        item.note = f"{item.note} | Errore copia problematico: {copy_error}".strip(" |")
                        item.outcome = MP3BatchOutcome.ERROR
                        item.outcome_folder_path = str(folder_map[item.outcome])

            if _requires_outcome_artifact(item.outcome):
                artifact_originals_unchanged: bool | None = originals_unchanged
                if item.outcome == MP3BatchOutcome.ORIGINAL_NOT_FOUND and not item.original_path:
                    artifact_originals_unchanged = None
                item.esito_json_path = _write_outcome_artifact(
                    item=item,
                    session_timestamp=session_timestamp,
                    originals_unchanged=artifact_originals_unchanged,
                )

            final_payload = telemetry.emit_event(
                "FILE_FINAL_STATE",
                problematic_file=item.problematic_name,
                full_path=item.problematic_path,
                phase="File completato",
                last_phase=last_phase,
                batch_status=batch_status,
                files_examined=files_examined,
                files_completed=files_completed,
                files_total=total,
                message=item.note,
                final_result=item.outcome.value,
                reason=item.note,
                session_folder=str(session_root),
                result_folder=item.outcome_folder_path,
                recovered_file=item.recovered_path or None,
                result_json=item.esito_json_path or None,
                final_timestamp=datetime.now().isoformat(timespec="seconds"),
            )
            telemetry.update_current_state(final_payload)

        for index, problematic_path in enumerate(problematic_files, start=1):
            if _is_cancelled(cancel_event):
                interrupted = True
                cancel_payload = telemetry.emit_event(
                    "CANCEL_REQUESTED",
                    phase="Interrotto",
                    last_phase="Batch",
                    batch_status="Interrotto",
                    files_examined=examined_problematic,
                    files_completed=completed_problematic,
                    files_total=total,
                    message="Interruzione richiesta prima del prossimo file.",
                    cancel_requested=True,
                    critical=True,
                )
                telemetry.update_current_state(cancel_payload)
                _append_interrupted_entries(problematic_files, index, items, counters)
                _log(log_callback, "[TECH] Interruzione richiesta prima del prossimo file")
                break

            file_start = time.monotonic()
            examined_problematic += 1
            problem_size = problematic_path.stat().st_size if problematic_path.exists() else 0
            telemetry.emit_file_start(problematic_path, problem_size)
            resolved_key = str(problematic_path.resolve()).casefold()
            if resolved_key in processed_paths:
                item = MP3BatchItemResult(
                index=index,
                problematic_name=problematic_path.name,
                problematic_path=str(problematic_path),
                original_name="",
                original_path="",
                outcome=MP3BatchOutcome.ERROR,
                strategy="",
                problematic_winlive_present="",
                original_winlive_present="",
                recovered_path="",
                duration_seconds=max(0.0, time.monotonic() - file_start),
                note="Percorso problematico duplicato rilevato, file saltato per prevenire loop.",
                original_unchanged="",
                problematic_audio_hash="",
                original_audio_hash="",
            )
                item.last_completed_phase = "Validazione input"
                items.append(item)
                _inc(counters, item.outcome)
                completed_problematic += 1
                telemetry.emit_file_end(problematic_path, item.outcome.value, item.note)
                _log(log_callback, f"[ERRORE] {problematic_path.name} - percorso duplicato")
                _emit_progress(progress_callback, index, total, problematic_path.name, counters, start_time)
                continue
            processed_paths.add(resolved_key)

            problematic_name = problematic_path.name
            matching_key = problematic_name.casefold()
            search_start = time.monotonic()
            candidates = list(original_index.get(matching_key, []))
            search_elapsed = max(0.0, time.monotonic() - search_start)
            telemetry.set_phase_duration(problematic_path, "search_original", search_elapsed)

            _log(log_callback, f"[TECH] Inizio file {index}/{total} | {problematic_name}")
            _log(log_callback, "[TECH] Fase -> Ricerca originale")

            if not candidates:
                item = MP3BatchItemResult(
                index=index,
                problematic_name=problematic_name,
                problematic_path=str(problematic_path),
                original_name="",
                original_path="",
                outcome=MP3BatchOutcome.ORIGINAL_NOT_FOUND,
                strategy="",
                problematic_winlive_present="",
                original_winlive_present="",
                recovered_path="",
                duration_seconds=max(0.0, time.monotonic() - file_start),
                note="Nessun originale con lo stesso nome (case-insensitive).",
                original_unchanged="",
                problematic_audio_hash="",
                original_audio_hash="",
                search_original_seconds=search_elapsed,
                total_file_seconds=max(0.0, time.monotonic() - file_start),
            )
                item.last_completed_phase = "Ricerca originale"
                items.append(item)
                _inc(counters, item.outcome)
                completed_problematic += 1
                telemetry.set_phase_duration(problematic_path, "total_file", item.total_file_seconds)
                telemetry.emit_file_end(problematic_path, item.outcome.value, item.note)
                _log(log_callback, f"[NON TROVATO] {problematic_name}")
                _emit_progress(progress_callback, index, total, problematic_name, counters, start_time)
                continue

            if len(candidates) > 1:
                item = MP3BatchItemResult(
                index=index,
                problematic_name=problematic_name,
                problematic_path=str(problematic_path),
                original_name="",
                original_path="",
                outcome=MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS,
                strategy="",
                problematic_winlive_present="",
                original_winlive_present="",
                recovered_path="",
                duration_seconds=max(0.0, time.monotonic() - file_start),
                note=f"Trovati {len(candidates)} originali con lo stesso nome (case-insensitive).",
                original_unchanged="",
                problematic_audio_hash="",
                original_audio_hash="",
                search_original_seconds=search_elapsed,
                total_file_seconds=max(0.0, time.monotonic() - file_start),
            )
                item.last_completed_phase = "Ricerca originale"
                items.append(item)
                _inc(counters, item.outcome)
                completed_problematic += 1
                telemetry.set_phase_duration(problematic_path, "total_file", item.total_file_seconds)
                telemetry.emit_file_end(problematic_path, item.outcome.value, item.note)
                _log(log_callback, f"[AMBIGUO] {problematic_name} - stesso nome multiplo")
                _emit_progress(progress_callback, index, total, problematic_name, counters, start_time)
                continue

            selected_original = candidates[0]
            _log(log_callback, f"[TECH] Candidato trovato | problematico={problematic_name} | originale={selected_original.name}")

            if recovery_mode == RecoveryMode.NORMAL:
                _log(log_callback, "[TECH] Fase -> Calcolo hash problematico")
                _log(log_callback, f"[TECH] Inizio hash | problematico={problematic_name}")
                problematic_hash_started = time.monotonic()
                problematic_hash_result = _get_audio_hash(
                problematic_path,
                problematic_audio_hash_cache,
                cancel_event=cancel_event,
                log_callback=log_callback,
                label=f"problematico={problematic_name}",
                telemetry=telemetry,
                problematic_path=problematic_path,
                role="problematico",
            )
                hash_problematic_elapsed = max(0.0, time.monotonic() - problematic_hash_started)
                telemetry.set_phase_duration(problematic_path, "hash_problematic", hash_problematic_elapsed)
                problematic_audio_hash = problematic_hash_result.audio_hash_sha256
                if problematic_hash_result.status == AudioHashStatus.CANCELLED:
                    interrupted = True
                    interrupted_item = MP3BatchItemResult(
                    index=index,
                    problematic_name=problematic_name,
                    problematic_path=str(problematic_path),
                    original_name=selected_original.name,
                    original_path=str(selected_original),
                    outcome=MP3BatchOutcome.INTERRUPTED,
                    strategy="",
                    problematic_winlive_present="",
                    original_winlive_present="",
                    recovered_path="",
                    duration_seconds=max(0.0, time.monotonic() - file_start),
                    note="Interrotto durante calcolo hash problematico.",
                    original_unchanged="",
                    problematic_audio_hash=problematic_audio_hash or "",
                    original_audio_hash="",
                    search_original_seconds=search_elapsed,
                    hash_problematic_seconds=hash_problematic_elapsed,
                    total_file_seconds=max(0.0, time.monotonic() - file_start),
                )
                    interrupted_item.last_completed_phase = "Calcolo hash problematico"
                    items.append(interrupted_item)
                    _inc(counters, interrupted_item.outcome)
                    telemetry.set_phase_duration(problematic_path, "total_file", interrupted_item.total_file_seconds)
                    telemetry.emit_file_end(problematic_path, interrupted_item.outcome.value, interrupted_item.note)
                    cancel_payload = telemetry.emit_event(
                        "CANCEL_REQUESTED",
                        problematic_file=problematic_name,
                        full_path=str(problematic_path.resolve()),
                        file_size=problem_size,
                        phase="Interrotto",
                        last_phase="Calcolo hash problematico",
                        batch_status="Interrotto",
                        files_examined=examined_problematic,
                        files_completed=completed_problematic,
                        files_total=total,
                        message="Richiesta cancel durante hash problematico.",
                        cancel_requested=True,
                        critical=True,
                    )
                    telemetry.update_current_state(cancel_payload)
                    _log(log_callback, "[TECH] Hash ha rilevato cancel (problematico)")
                    _append_interrupted_entries(problematic_files, index + 1, items, counters)
                    break
                _log(log_callback, f"[TECH] Fine hash | problematico={problematic_name}")

                _log(log_callback, "[TECH] Fase -> Calcolo hash originale")
                _log(log_callback, f"[TECH] Inizio hash | originale={selected_original.name}")
                original_hash_started = time.monotonic()
                original_hash_result = _get_audio_hash(
                selected_original,
                original_audio_hash_cache,
                cancel_event=cancel_event,
                log_callback=log_callback,
                label=f"originale={selected_original.name}",
                telemetry=telemetry,
                problematic_path=problematic_path,
                role="originale",
            )
                hash_original_elapsed = max(0.0, time.monotonic() - original_hash_started)
                telemetry.set_phase_duration(problematic_path, "hash_original", hash_original_elapsed)
                selected_original_hash = original_hash_result.audio_hash_sha256
                if original_hash_result.status == AudioHashStatus.CANCELLED:
                    interrupted = True
                    interrupted_item = MP3BatchItemResult(
                    index=index,
                    problematic_name=problematic_name,
                    problematic_path=str(problematic_path),
                    original_name=selected_original.name,
                    original_path=str(selected_original),
                    outcome=MP3BatchOutcome.INTERRUPTED,
                    strategy="",
                    problematic_winlive_present="",
                    original_winlive_present="",
                    recovered_path="",
                    duration_seconds=max(0.0, time.monotonic() - file_start),
                    note="Interrotto durante calcolo hash originale.",
                    original_unchanged="",
                    problematic_audio_hash=problematic_audio_hash or "",
                    original_audio_hash=selected_original_hash or "",
                    search_original_seconds=search_elapsed,
                    hash_problematic_seconds=hash_problematic_elapsed,
                    hash_original_seconds=hash_original_elapsed,
                    total_file_seconds=max(0.0, time.monotonic() - file_start),
                )
                    interrupted_item.last_completed_phase = "Calcolo hash originale"
                    items.append(interrupted_item)
                    _inc(counters, interrupted_item.outcome)
                    telemetry.set_phase_duration(problematic_path, "total_file", interrupted_item.total_file_seconds)
                    telemetry.emit_file_end(problematic_path, interrupted_item.outcome.value, interrupted_item.note)
                    cancel_payload = telemetry.emit_event(
                        "CANCEL_REQUESTED",
                        problematic_file=problematic_name,
                        full_path=str(problematic_path.resolve()),
                        file_size=problem_size,
                        phase="Interrotto",
                        last_phase="Calcolo hash originale",
                        batch_status="Interrotto",
                        files_examined=examined_problematic,
                        files_completed=completed_problematic,
                        files_total=total,
                        message="Richiesta cancel durante hash originale.",
                        cancel_requested=True,
                        critical=True,
                    )
                    telemetry.update_current_state(cancel_payload)
                    _log(log_callback, "[TECH] Hash ha rilevato cancel (originale)")
                    _append_interrupted_entries(problematic_files, index + 1, items, counters)
                    break
                _log(log_callback, f"[TECH] Fine hash | originale={selected_original.name}")

                compatibility_analysis = _compute_compatibility_analysis(
                    problematic_path=problematic_path,
                    original_path=selected_original,
                    problematic_hash_result=problematic_hash_result,
                    original_hash_result=original_hash_result,
                    duration_cache=duration_cache,
                )
            else:
                hash_problematic_elapsed = 0.0
                hash_original_elapsed = 0.0
                problematic_audio_hash = None
                selected_original_hash = None
                compatibility_analysis = _compute_forced_compatibility_analysis(
                    problematic_path=problematic_path,
                    original_path=selected_original,
                    duration_cache=duration_cache,
                )

            if recovery_mode == RecoveryMode.NORMAL and compatibility_analysis.get("overall_status") != OVERALL_COMPATIBLE:
                causes = compatibility_analysis.get("reasons") or []
                if causes:
                    note = " / ".join(str(value) for value in causes)
                else:
                    note = "Originale con stesso nome trovato ma compatibilita non determinabile."
                item = MP3BatchItemResult(
                index=index,
                problematic_name=problematic_name,
                problematic_path=str(problematic_path),
                original_name=selected_original.name,
                original_path=str(selected_original),
                outcome=MP3BatchOutcome.ORIGINAL_INCOMPATIBLE,
                strategy="",
                problematic_winlive_present="",
                original_winlive_present="",
                recovered_path="",
                duration_seconds=max(0.0, time.monotonic() - file_start),
                note=note,
                original_unchanged="",
                problematic_audio_hash=problematic_audio_hash or "",
                original_audio_hash=selected_original_hash or "",
                search_original_seconds=search_elapsed,
                hash_problematic_seconds=hash_problematic_elapsed,
                hash_original_seconds=hash_original_elapsed,
                total_file_seconds=max(0.0, time.monotonic() - file_start),
                compatibility_analysis=compatibility_analysis,
            )
                item.compatibility_problem_rows = _build_problem_rows(item)
                item.last_completed_phase = "Confronto hash audio"
                items.append(item)
                _inc(counters, item.outcome)
                completed_problematic += 1
                telemetry.set_phase_duration(problematic_path, "total_file", item.total_file_seconds)
                telemetry.emit_file_end(problematic_path, item.outcome.value, item.note)
                _log(log_callback, f"[INCOMPATIBILE] {problematic_name}")
                _emit_progress(progress_callback, index, total, problematic_name, counters, start_time)
                continue

            _log(log_callback, "[TECH] Fase -> Recupero TAG WinLive")
            _log(log_callback, f"[TECH] Inizio recupero singolo | {problematic_name}")
            recovery_started = time.monotonic()
            phase_marks: dict[str, float] = {}

            def _recovery_log_callback(message: str) -> None:
                if message == "[TECH] Fase -> Verifica finale" and "verification_started" not in phase_marks:
                    phase_marks["verification_started"] = time.monotonic()
                _log(log_callback, message)

            recovery_result = recover_mp3_from_original(
            problematic_path=problematic_path,
            original_path=selected_original,
            output_dir=session_root,
            output_name=problematic_name,
            log_callback=_recovery_log_callback,
            cancel_event=cancel_event,
            recovery_mode=recovery_mode,
            precomputed_original_audio_hash=selected_original_hash,
            precomputed_problematic_audio_hash=problematic_audio_hash,
        )
            recovery_finished = time.monotonic()
            verification_started = phase_marks.get("verification_started", recovery_finished)
            recovery_elapsed = max(0.0, verification_started - recovery_started)
            verification_elapsed = max(0.0, recovery_finished - verification_started)
            telemetry.set_phase_duration(problematic_path, "recovery", recovery_elapsed)
            telemetry.set_phase_duration(problematic_path, "verification_final", verification_elapsed)
            _log(log_callback, f"[TECH] Fine recupero singolo | {problematic_name} | status={recovery_result.status.value}")

            if recovery_result.status == MP3RecoveryStatus.CANCELLED:
                interrupted = True
                current_item = MP3BatchItemResult(
                index=index,
                problematic_name=problematic_name,
                problematic_path=str(problematic_path),
                original_name=selected_original.name,
                original_path=str(selected_original),
                outcome=MP3BatchOutcome.INTERRUPTED,
                strategy="",
                problematic_winlive_present="SI" if recovery_result.problematic_winlive_present else "NO",
                original_winlive_present="SI" if recovery_result.original_winlive_present else "NO",
                recovered_path="",
                duration_seconds=max(0.0, time.monotonic() - file_start),
                note=recovery_result.error or "Operazione interrotta.",
                original_unchanged="SI" if recovery_result.original_sha256_before == recovery_result.original_sha256_after else "NO",
                problematic_audio_hash=problematic_audio_hash or "",
                original_audio_hash=selected_original_hash or "",
                search_original_seconds=search_elapsed,
                hash_problematic_seconds=hash_problematic_elapsed,
                hash_original_seconds=hash_original_elapsed,
                recovery_seconds=recovery_elapsed,
                verification_final_seconds=verification_elapsed,
                total_file_seconds=max(0.0, time.monotonic() - file_start),
            )
                current_item.last_completed_phase = "Recupero TAG WinLive"
                items.append(current_item)
                _inc(counters, current_item.outcome)
                telemetry.set_phase_duration(problematic_path, "total_file", current_item.total_file_seconds)
                telemetry.emit_file_end(problematic_path, current_item.outcome.value, current_item.note)
                cancel_payload = telemetry.emit_event(
                    "CANCEL_REQUESTED",
                    problematic_file=problematic_name,
                    full_path=str(problematic_path.resolve()),
                    file_size=problem_size,
                    phase="Interrotto",
                    last_phase="Recupero TAG WinLive",
                    batch_status="Interrotto",
                    files_examined=examined_problematic,
                    files_completed=completed_problematic,
                    files_total=total,
                    message=current_item.note,
                    cancel_requested=True,
                    critical=True,
                )
                telemetry.update_current_state(cancel_payload)
                _append_interrupted_entries(problematic_files, index + 1, items, counters)
                _log(log_callback, f"[INTERROTTO] {problematic_name}")
                _emit_progress(progress_callback, index, total, problematic_name, counters, start_time)
                break

            item = _build_item_from_recovery_result(
            index=index,
            problematic_path=problematic_path,
            selected_original=selected_original,
            problematic_audio_hash=problematic_audio_hash,
            selected_original_hash=selected_original_hash,
            recovery_result=recovery_result,
            folder_map=folder_map,
            elapsed_seconds=max(0.0, time.monotonic() - file_start),
            search_original_seconds=search_elapsed,
            hash_problematic_seconds=hash_problematic_elapsed,
            hash_original_seconds=hash_original_elapsed,
            recovery_seconds=recovery_elapsed,
            verification_final_seconds=verification_elapsed,
                recovery_mode=recovery_mode,
                compatibility_analysis=compatibility_analysis,
        )

            items.append(item)
            _inc(counters, item.outcome)
            completed_problematic += 1
            telemetry.set_phase_duration(problematic_path, "total_file", item.total_file_seconds)
            telemetry.emit_file_end(problematic_path, item.outcome.value, item.note)
            _log_row(log_callback, item)
            _emit_progress(progress_callback, index, total, problematic_name, counters, start_time)

        processed = examined_problematic
        original_snapshots_after = {path: _snapshot_file(path) for path in originals_files}
        originals_unchanged = all(
            original_snapshots_before[path] == original_snapshots_after[path] for path in originals_files
        )

        batch_status_label = "Interrotto" if interrupted else "Completato"
        for item in items:
            if not item.session_folder:
                _finalize_item_state(
                    item,
                    last_phase=item.last_completed_phase or "Batch",
                    batch_status=batch_status_label,
                    files_examined=examined_problematic,
                    files_completed=completed_problematic,
                    originals_unchanged=originals_unchanged,
                )
            elif _requires_outcome_artifact(item.outcome) and not item.esito_json_path:
                artifact_originals_unchanged: bool | None = originals_unchanged
                if item.outcome == MP3BatchOutcome.ORIGINAL_NOT_FOUND and not item.original_path:
                    artifact_originals_unchanged = None
                item.esito_json_path = _write_outcome_artifact(
                    item=item,
                    session_timestamp=session_timestamp,
                    originals_unchanged=artifact_originals_unchanged,
                )

        if items:
            last_item = items[-1]
            final_batch_payload = telemetry.emit_event(
                "BATCH_FINAL_STATE",
                problematic_file=last_item.problematic_name,
                full_path=last_item.problematic_path,
                phase="File completato",
                last_phase=last_item.last_completed_phase or "Batch",
                batch_status=batch_status_label,
                files_examined=examined_problematic,
                files_completed=completed_problematic,
                files_total=total,
                message=last_item.note,
                final_result=last_item.outcome.value,
                reason=last_item.note,
                session_folder=str(session_root),
                result_folder=last_item.outcome_folder_path,
                recovered_file=last_item.recovered_path or None,
                result_json=last_item.esito_json_path or None,
                final_timestamp=datetime.now().isoformat(timespec="seconds"),
            )
            telemetry.update_current_state(final_batch_payload)

        _log(log_callback, "[TECH] Fase -> Report")
        _log(log_callback, "[TECH] Scrittura report")
        report_paths = _write_reports(report_root, items, session_timestamp)
        _log(log_callback, f"[TECH] Report CSV={report_paths.get('csv', '')}")

        elapsed = max(0.0, time.monotonic() - start_time)

        result = MP3RecoveryBatchResult(
            success=not interrupted,
            interrupted=interrupted,
            error=None,
            total_problematic=total,
            processed_problematic=processed,
            counters=counters,
            elapsed_seconds=elapsed,
            report_paths=report_paths,
            output_root=str(session_root),
            items=items,
            originals_unchanged=originals_unchanged,
            examined_problematic=examined_problematic,
            completed_problematic=completed_problematic,
            session_folder=str(session_root),
        )
        _write_session_summary(
            session_root=session_root,
            session_timestamp=session_timestamp,
            started_at=start_time,
            ended_at=time.monotonic(),
            total_problematic=total,
            examined_problematic=examined_problematic,
            completed_problematic=completed_problematic,
            counters=counters,
            interrupted=interrupted,
            log_callback=log_callback,
            session_snapshot=session_snapshot,
        )
        _ensure_session_not_empty(session_root, reason="Sessione completata senza artefatti.")
        _cleanup_empty_session_dirs(session_root, log_callback=log_callback)
        telemetry.close(final_message="Sessione completata." if not interrupted else "Sessione interrotta.", cancelled=interrupted)
        return result
    except Exception as error:
        telemetry.emit_event("ERROR", phase="Batch", message=str(error), critical=True)
        telemetry.close(final_message="Sessione terminata con errore.", cancelled=_is_cancelled(cancel_event))
        _write_session_error_file(session_root, str(error))
        _ensure_session_not_empty(session_root, reason=str(error))
        _cleanup_empty_session_dirs(session_root, log_callback=log_callback)
        raise


def _build_item_from_recovery_result(
    *,
    index: int,
    problematic_path: Path,
    selected_original: Path,
    problematic_audio_hash: str | None,
    selected_original_hash: str | None,
    recovery_result,
    folder_map: dict[MP3BatchOutcome, Path],
    elapsed_seconds: float,
    search_original_seconds: float,
    hash_problematic_seconds: float,
    hash_original_seconds: float,
    recovery_seconds: float,
    verification_final_seconds: float,
    recovery_mode: RecoveryMode,
    compatibility_analysis: dict[str, Any],
) -> MP3BatchItemResult:
    status_map = {
        MP3RecoveryStatus.ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS: MP3BatchOutcome.RECOVERED_TAGS,
        MP3RecoveryStatus.UNCHANGED_ORIGINAL_COPY: MP3BatchOutcome.RECOVERED_UNCHANGED,
        MP3RecoveryStatus.FORCED_COPY_WITH_REPLACED_WINLIVE_TAGS: MP3BatchOutcome.RECOVERED_FORCED,
        MP3RecoveryStatus.FORCED_UNCHANGED_ORIGINAL_COPY: MP3BatchOutcome.RECOVERED_FORCED,
        MP3RecoveryStatus.ORIGINAL_FILE_NOT_COMPATIBLE: MP3BatchOutcome.ORIGINAL_INCOMPATIBLE,
        MP3RecoveryStatus.READ_ERROR: MP3BatchOutcome.READ_ERROR,
        MP3RecoveryStatus.WRITE_ERROR: MP3BatchOutcome.WRITE_ERROR,
        MP3RecoveryStatus.FINAL_VERIFICATION_FAILED: MP3BatchOutcome.FINAL_VERIFICATION_FAILED,
    }
    outcome = status_map.get(recovery_result.status, MP3BatchOutcome.ERROR)

    recovered_path = ""
    note = (recovery_result.error or "; ".join(recovery_result.notes)).strip()
    integrity_policy_assessed = False
    integrity_certified = False
    integrity_classification_reason = ""
    blocking_issues_count = 0
    non_blocking_tail_issues_count = 0

    if recovery_result.success and recovery_result.output_path:
        source_path = Path(recovery_result.output_path)
        integrity_policy = _assess_recovered_integrity_policy(source_path)
        integrity_policy_assessed = bool(integrity_policy.get("assessed", False))
        integrity_certified = bool(integrity_policy.get("integrity_certified", False))
        integrity_classification_reason = str(integrity_policy.get("classification_reason") or "")
        blocking_issues_count = int(integrity_policy.get("blocking_issues_count") or 0)
        non_blocking_tail_issues_count = int(integrity_policy.get("non_blocking_tail_issues_count") or 0)

        if blocking_issues_count > 0:
            outcome = MP3BatchOutcome.FINAL_VERIFICATION_FAILED
            if integrity_classification_reason:
                note = (
                    f"{note} | Integrita finale non certificata: {integrity_classification_reason}" if note
                    else f"Integrita finale non certificata: {integrity_classification_reason}"
                )
        elif non_blocking_tail_issues_count > 0 and integrity_classification_reason:
            note = (
                f"{note} | Avvertimenti di coda non bloccanti: {integrity_classification_reason}" if note
                else f"Avvertimenti di coda non bloccanti: {integrity_classification_reason}"
            )

        target_folder = folder_map[outcome]
        recovered_target = _move_with_collision(source_path, target_folder, problematic_path.name)
        recovered_path = str(recovered_target)
        if not note:
            note = "Recupero completato."

    item = MP3BatchItemResult(
        index=index,
        problematic_name=problematic_path.name,
        problematic_path=str(problematic_path),
        original_name=selected_original.name,
        original_path=str(selected_original),
        outcome=outcome,
        strategy=recovery_result.strategy or "",
        problematic_winlive_present="SI" if recovery_result.problematic_winlive_present else "NO",
        original_winlive_present="SI" if recovery_result.original_winlive_present else "NO",
        recovered_path=recovered_path,
        duration_seconds=elapsed_seconds,
        note=note,
        original_unchanged="SI" if recovery_result.original_sha256_before == recovery_result.original_sha256_after else "NO",
        problematic_audio_hash=problematic_audio_hash or "",
        original_audio_hash=selected_original_hash or "",
        recovery_mode=str(recovery_mode.value),
        forced_recovery=bool(getattr(recovery_result, "forced_recovery", False)),
        audio_comparison_executed=bool(getattr(recovery_result, "audio_comparison_executed", True)),
        audio_comparison_reason=str(getattr(recovery_result, "audio_comparison_reason", "")),
        search_original_seconds=search_original_seconds,
        hash_problematic_seconds=hash_problematic_seconds,
        hash_original_seconds=hash_original_seconds,
        recovery_seconds=recovery_seconds,
        verification_final_seconds=verification_final_seconds,
        total_file_seconds=elapsed_seconds,
        compatibility_analysis=compatibility_analysis,
        integrity_policy_assessed=integrity_policy_assessed,
        integrity_certified=integrity_certified,
        integrity_classification_reason=integrity_classification_reason,
        blocking_issues_count=blocking_issues_count,
        non_blocking_tail_issues_count=non_blocking_tail_issues_count,
    )
    item.compatibility_problem_rows = _build_problem_rows(item)
    item.last_completed_phase = "Verifica finale"
    return item


def _append_interrupted_entries(
    problematic_files: list[Path],
    start_index: int,
    items: list[MP3BatchItemResult],
    counters: dict[str, int],
) -> None:
    for index in range(start_index, len(problematic_files) + 1):
        problematic_path = problematic_files[index - 1]
        item = MP3BatchItemResult(
            index=index,
            problematic_name=problematic_path.name,
            problematic_path=str(problematic_path),
            original_name="",
            original_path="",
            outcome=MP3BatchOutcome.INTERRUPTED,
            strategy="",
            problematic_winlive_present="",
            original_winlive_present="",
            recovered_path="",
            duration_seconds=0.0,
            note="Non elaborato: operazione interrotta.",
            original_unchanged="",
            problematic_audio_hash="",
            original_audio_hash="",
        )
        item.last_completed_phase = "Interrotto prima elaborazione"
        items.append(item)
        _inc(counters, item.outcome)


def _validate_paths(problematic_root: Path, originals_root: Path, destination_root: Path) -> None:
    if not problematic_root.is_dir():
        raise RuntimeError(f"Cartella problematici non valida: {problematic_root}")
    if not originals_root.is_dir():
        raise RuntimeError(f"Cartella originali non valida: {originals_root}")
    if problematic_root == originals_root:
        raise RuntimeError("La cartella problematici deve essere diversa dalla cartella originali.")
    if destination_root == originals_root:
        raise RuntimeError("La cartella di destinazione non puo coincidere con la cartella originali.")

    destination_root.mkdir(parents=True, exist_ok=True)

    _assert_readable_dir(problematic_root)
    _assert_readable_dir(originals_root)
    _assert_writable_dir(destination_root)


def _assert_readable_dir(path: Path) -> None:
    try:
        with os.scandir(path) as it:
            for _ in it:
                break
    except OSError as exc:
        raise RuntimeError(f"Cartella non accessibile in lettura: {path} ({exc})") from exc


def _assert_writable_dir(path: Path) -> None:
    probe = path / f".writable_probe_{int(time.time() * 1000)}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cartella non accessibile in scrittura: {path} ({exc})") from exc
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_output_structure(output_root: Path) -> dict[MP3BatchOutcome, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    mapping: dict[MP3BatchOutcome, Path] = {}
    for outcome, folder_name in OUTCOME_FOLDERS.items():
        folder = output_root / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        mapping[outcome] = folder
    return mapping


def _scan_mp3_files_non_recursive(root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    try:
        entries = sorted(list(os.scandir(root)), key=lambda e: e.name.casefold())
    except OSError:
        return []

    for entry in entries:
        path = Path(entry.path)
        if not entry.is_file(follow_symlinks=False):
            continue
        if path.suffix.casefold() != ".mp3":
            continue
        resolved = path.resolve()
        found[str(resolved).casefold()] = resolved

    files = list(found.values())
    files.sort(key=lambda p: p.name.casefold())
    return files


def _build_original_index_by_full_name(original_files: list[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in original_files:
        key = path.name.casefold()
        index.setdefault(key, []).append(path)
    for key in index:
        index[key].sort(key=lambda p: str(p).casefold())
    return index


def _get_audio_hash(
    path: Path,
    cache: dict[Path, _HashLookupResult],
    *,
    cancel_event: object | None,
    log_callback: BatchLogCallback | None,
    label: str,
    telemetry: RecoveryTelemetrySession,
    problematic_path: Path,
    role: str,
) -> _HashLookupResult:
    if _is_cancelled(cancel_event):
        _log(log_callback, f"[TECH] Hash annullato prima della lettura | {label}")
        telemetry.emit_event("CANCEL_REQUESTED", problematic_file=problematic_path.name, full_path=str(problematic_path.resolve()), phase=f"Calcolo hash {role}", message=f"Hash annullato prima della lettura | {label}", cancel_requested=True, critical=True)
        return _HashLookupResult(status=AudioHashStatus.CANCELLED, audio_hash_sha256=None)

    cache_hit = path in cache
    if cache_hit:
        _log(log_callback, f"[TECH] Hash cache hit | {label}")
        return cache[path]

    _log(log_callback, f"[TECH] Hash cache miss | {label}")
    try:
        _log(log_callback, f"[TECH] Apertura file hash | {label} | path={path}")
        raw = path.read_bytes()
        _log(log_callback, f"[TECH] Dimensione file hash | {label} | bytes={len(raw)}")
        sink = HashTelemetrySink(
            telemetry,
            problematic_path=problematic_path,
            full_path=path.resolve(),
            file_size=len(raw),
            role=role,
            gui_log_callback=log_callback,
        )
        result = compute_mpeg_audio_hash(
            raw,
            cancel_event=cancel_event,
            debug_callback=sink,
            source_label=label,
        )
    except OSError as exc:
        _log(log_callback, f"[TECH] Errore lettura hash | {label} | {exc}")
        telemetry.emit_event("ERROR", problematic_file=problematic_path.name, full_path=str(path.resolve()), file_size=0, phase=f"Calcolo hash {role}", message=str(exc), critical=True)
        lookup_error = _HashLookupResult(
            status=AudioHashStatus.NO_AUDIO_STREAM,
            audio_hash_sha256=None,
            anomalies=[str(exc)],
        )
        cache[path] = lookup_error
        return lookup_error

    lookup_result = _HashLookupResult(
        status=result.status,
        audio_hash_sha256=result.audio_hash_sha256,
        frames_count=int(getattr(result, "frames_count", 0) or 0),
        first_frame_offset=getattr(result, "first_frame_offset", None),
        last_frame_end_offset=getattr(result, "last_frame_end_offset", None),
        anomalies=list(getattr(result, "anomalies", []) or []),
    )
    cache[path] = lookup_result
    return lookup_result


def _move_with_collision(source_path: Path, destination_folder: Path, preferred_name: str) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    base = destination_folder / preferred_name
    if not base.exists():
        os.replace(source_path, base)
        return base

    stem = base.stem
    suffix = base.suffix or ".mp3"
    index = 1
    while True:
        candidate = destination_folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            os.replace(source_path, candidate)
            return candidate
        index += 1


def _copy_with_collision(source_path: Path, destination_folder: Path, preferred_name: str) -> Path:
    destination_folder.mkdir(parents=True, exist_ok=True)
    base = destination_folder / preferred_name
    if not base.exists():
        shutil.copy2(source_path, base)
        return base

    stem = base.stem
    suffix = base.suffix or ".mp3"
    index = 1
    while True:
        candidate = destination_folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            shutil.copy2(source_path, candidate)
            return candidate
        index += 1


def _files_byte_identical(path_a: Path, path_b: Path) -> bool:
    try:
        if path_a.stat().st_size != path_b.stat().st_size:
            return False
        with path_a.open("rb") as handle_a, path_b.open("rb") as handle_b:
            while True:
                chunk_a = handle_a.read(1024 * 1024)
                chunk_b = handle_b.read(1024 * 1024)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


def _snapshot_file(path: Path) -> tuple[str, int, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data), int(path.stat().st_mtime)


def _new_counters() -> dict[str, int]:
    return {
        MP3BatchOutcome.RECOVERED_TAGS.value: 0,
        MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
        MP3BatchOutcome.RECOVERED_FORCED.value: 0,
        MP3BatchOutcome.ORIGINAL_NOT_FOUND.value: 0,
        MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
        MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value: 0,
        MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS.value: 0,
        MP3BatchOutcome.READ_ERROR.value: 0,
        MP3BatchOutcome.WRITE_ERROR.value: 0,
        MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value: 0,
        MP3BatchOutcome.INTERRUPTED.value: 0,
        MP3BatchOutcome.ERROR.value: 0,
    }


def _inc(counters: dict[str, int], outcome: MP3BatchOutcome) -> None:
    counters[outcome.value] = counters.get(outcome.value, 0) + 1


def _format_elapsed(seconds: float) -> str:
    whole = int(max(0, seconds))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _emit_progress(
    callback: BatchProgressCallback | None,
    current: int,
    total: int,
    current_name: str,
    counters: dict[str, int],
    started_at: float,
) -> None:
    if callback is None:
        return

    elapsed = _format_elapsed(time.monotonic() - started_at)
    message = (
        f"Analisi {current} di {total} - {current_name} | "
        f"TAG:{counters.get(MP3BatchOutcome.RECOVERED_TAGS.value, 0)} "
        f"COPIA:{counters.get(MP3BatchOutcome.RECOVERED_UNCHANGED.value, 0)} "
        f"FORZATI:{counters.get(MP3BatchOutcome.RECOVERED_FORCED.value, 0)} "
        f"NON TROVATO:{counters.get(MP3BatchOutcome.ORIGINAL_NOT_FOUND.value, 0)} "
        f"INCOMPATIBILE:{counters.get(MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value, 0)} "
        f"AMBIGUO:{counters.get(MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value, 0) + counters.get(MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS.value, 0)} "
        f"ERRORI:{counters.get(MP3BatchOutcome.ERROR.value, 0) + counters.get(MP3BatchOutcome.READ_ERROR.value, 0) + counters.get(MP3BatchOutcome.WRITE_ERROR.value, 0) + counters.get(MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value, 0)} "
        f"TEMPO:{elapsed}"
    )
    callback(current, total, message)


def _log(callback: BatchLogCallback | None, text: str) -> None:
    if callback is not None:
        callback(text)


def _log_row(callback: BatchLogCallback | None, item: MP3BatchItemResult) -> None:
    if callback is None:
        return

    if item.outcome == MP3BatchOutcome.RECOVERED_TAGS:
        callback(f"[RECUPERATO TAG] {item.problematic_name}")
    elif item.outcome == MP3BatchOutcome.RECOVERED_UNCHANGED:
        callback(f"[COPIA INVARIATA] {item.problematic_name}")
    elif item.outcome == MP3BatchOutcome.RECOVERED_FORCED:
        callback(f"[RECUPERATO FORZATO] {item.problematic_name}")
    elif item.outcome == MP3BatchOutcome.ORIGINAL_NOT_FOUND:
        callback(f"[NON TROVATO] {item.problematic_name}")
    elif item.outcome == MP3BatchOutcome.ORIGINAL_INCOMPATIBLE:
        callback(f"[INCOMPATIBILE] {item.problematic_name}")
    elif item.outcome in (MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS, MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS):
        callback(f"[AMBIGUO] {item.problematic_name}")
    elif item.outcome == MP3BatchOutcome.INTERRUPTED:
        callback(f"[INTERROTTO] {item.problematic_name}")
    else:
        detail = f" - {item.note}" if item.note else ""
        callback(f"[ERRORE] {item.problematic_name}{detail}")


def _assess_recovered_integrity_policy(file_path: Path) -> dict[str, Any]:
    try:
        engine = MP3DiagnosticsEngine()
        payload = engine._certify_mp3_candidate(
            file_path=file_path,
            repair_mode=False,
            cancel_event=None,
        )
    except Exception as exc:
        return {
            "assessed": False,
            "integrity_certified": False,
            "classification_reason": f"Valutazione integrita non disponibile: {exc}",
            "blocking_issues_count": 0,
            "non_blocking_tail_issues_count": 0,
        }

    evaluated = list(payload.get("evaluated") or [])
    blocking = len([item for item in evaluated if bool(getattr(item, "blocking_for_final_outcome", False))])
    non_blocking_tail = len([item for item in evaluated if bool(getattr(item, "within_non_blocking_tail", False))])
    final_outcome = str(payload.get("final_outcome") or "")
    integrity_certified = final_outcome in {"Integro", "Riparato"}
    return {
        "assessed": True,
        "integrity_certified": integrity_certified,
        "classification_reason": str(payload.get("classification_reason") or ""),
        "blocking_issues_count": blocking,
        "non_blocking_tail_issues_count": non_blocking_tail,
    }


def _write_reports(report_dir: Path, items: list[MP3BatchItemResult], session_timestamp: str) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)

    csv_path = report_dir / f"report_recupero_mp3_{session_timestamp}.csv"
    problems_csv_path = report_dir / f"report_recupero_mp3_problemi_{session_timestamp}.csv"
    html_path = report_dir / f"report_recupero_mp3_{session_timestamp}.html"
    xlsx_path = report_dir / f"report_recupero_mp3_{session_timestamp}.xlsx"

    rows = [_item_to_row(item) for item in items]
    problems_rows = _collect_problem_rows(items)
    _write_csv(csv_path, rows)
    _write_csv(problems_csv_path, problems_rows)
    _write_html(html_path, rows, problems_rows)
    _write_xlsx(xlsx_path, rows, problems_rows)

    return {
        "csv": str(csv_path),
        "csv_problemi": str(problems_csv_path),
        "html": str(html_path),
        "xlsx": str(xlsx_path),
    }


def _item_to_row(item: MP3BatchItemResult) -> dict[str, str]:
    analysis = item.compatibility_analysis or {}
    checks = analysis.get("checks") if isinstance(analysis, dict) else {}
    checks = checks if isinstance(checks, dict) else {}
    duration_check = checks.get("duration") if isinstance(checks.get("duration"), dict) else {}
    tonality_check = checks.get("tonality") if isinstance(checks.get("tonality"), dict) else {}
    tempo_check = checks.get("tempo") if isinstance(checks.get("tempo"), dict) else {}
    offset_check = checks.get("offset") if isinstance(checks.get("offset"), dict) else {}
    cut_start_check = checks.get("cut_start") if isinstance(checks.get("cut_start"), dict) else {}
    cut_end_check = checks.get("cut_end") if isinstance(checks.get("cut_end"), dict) else {}
    mpeg_check = checks.get("mpeg_structure") if isinstance(checks.get("mpeg_structure"), dict) else {}
    reasons = analysis.get("reasons") if isinstance(analysis, dict) else []
    if not isinstance(reasons, list):
        reasons = []

    return {
        "Numero progressivo": str(item.index),
        "File problematico": item.problematic_name,
        "Nome file problematico": item.problematic_name,
        "Percorso file problematico": item.problematic_path,
        "File originale": item.original_name,
        "Nome originale associato": item.original_name,
        "Percorso originale associato": item.original_path,
        "Esito complessivo": str(analysis.get("overall_status") or OVERALL_NOT_DETERMINABLE),
        "Contenuto audio": str((checks.get("audio_content") or {}).get("status", CHECK_NOT_DETERMINABLE)),
        "Tonalita": str(tonality_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Tonalita file problematico": str(tonality_check.get("problematic_value", "")),
        "Tonalita file originale": str(tonality_check.get("original_value", "")),
        "Differenza tonalita stimata": str(tonality_check.get("semitone_difference", "")),
        "Affidabilita stima tonalita": str(tonality_check.get("confidence", "")),
        "Durata file problematico": str(analysis.get("problematic_duration_formatted") or "Non determinabile"),
        "Durata file originale": str(analysis.get("original_duration_formatted") or "Non determinabile"),
        "Differenza durata": str(analysis.get("duration_difference_formatted") or "Non determinabile"),
        "Differenza durata %": str(analysis.get("duration_difference_percent") if analysis.get("duration_difference_percent") is not None else "Non determinabile"),
        "Esito durata": str(duration_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Velocita/tempo": str(tempo_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Offset iniziale": str(offset_check.get("offset_initial", "Non determinabile")),
        "Taglio iniziale": str(cut_start_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Taglio finale": str(cut_end_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Struttura MPEG": str(mpeg_check.get("status", CHECK_NOT_DETERMINABLE)),
        "Cause incompatibilita": " / ".join(str(value) for value in reasons),
        "Analisi completate": ", ".join(str(value) for value in analysis.get("completed_checks", [])),
        "Analisi non determinabili": ", ".join(str(value) for value in analysis.get("non_determinable_checks", [])),
        "Errori tecnici analisi": " / ".join(str(value) for value in analysis.get("technical_errors", [])),
        "Esito finale": item.outcome.value,
        "Strategia applicata": item.strategy,
        "Modalita recupero": item.recovery_mode,
        "Recupero forzato": "SI" if item.forced_recovery else "NO",
        "Confronto audio eseguito": "SI" if item.audio_comparison_executed else "NO",
        "Motivo mancato confronto audio": item.audio_comparison_reason,
        "Policy integrita valutata": "SI" if item.integrity_policy_assessed else "NO",
        "Integrita certificata": "SI" if item.integrity_certified else "NO",
        "Anomalie bloccanti": str(item.blocking_issues_count),
        "Anomalie ultimo secondo non bloccanti": str(item.non_blocking_tail_issues_count),
        "Motivo classificazione integrita": item.integrity_classification_reason,
        "Presenza TAG WinLive nel problematico": item.problematic_winlive_present,
        "Presenza TAG WinLive nell'originale": item.original_winlive_present,
        "Percorso file recuperato": item.recovered_path,
        "File recuperato": "SI" if bool(item.recovered_path) else "NO",
        "Copia problematico": "SI" if item.problematic_copy_created else "NO",
        "Percorso copia problematico": item.copied_problematic_path,
        "Copia byte-identica": "SI" if item.problematic_copy_byte_identical else "NO",
        "Durata elaborazione": f"{item.duration_seconds:.3f}",
        "Dettaglio errore o nota": item.note,
        "Conferma originali invariati": item.original_unchanged,
        "Hash audio problematico": item.problematic_audio_hash,
        "Hash audio originale": item.original_audio_hash,
        "Tempo ricerca originale": f"{item.search_original_seconds:.6f}",
        "Tempo hash problematico": f"{item.hash_problematic_seconds:.6f}",
        "Tempo hash originale": f"{item.hash_original_seconds:.6f}",
        "Tempo recupero": f"{item.recovery_seconds:.6f}",
        "Tempo verifica finale": f"{item.verification_final_seconds:.6f}",
        "Tempo totale file": f"{item.total_file_seconds:.6f}",
        "Cartella sessione": item.session_folder,
        "Cartella esito": item.outcome_folder_path,
        "Percorso file esito JSON": item.esito_json_path,
        "Ultima fase completata": item.last_completed_phase,
    }


def _collect_problem_rows(items: list[MP3BatchItemResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        rows.extend(item.compatibility_problem_rows)
    return rows


def _requires_outcome_artifact(outcome: MP3BatchOutcome) -> bool:
    return outcome not in {
        MP3BatchOutcome.RECOVERED_TAGS,
        MP3BatchOutcome.RECOVERED_UNCHANGED,
    }


def _write_outcome_artifact(
    *,
    item: MP3BatchItemResult,
    session_timestamp: str,
    originals_unchanged: bool | None,
) -> str:
    if not item.outcome_folder_path:
        return ""
    folder = Path(item.outcome_folder_path)
    folder.mkdir(parents=True, exist_ok=True)
    artifact_base_name = item.problematic_name
    if item.copied_problematic_path:
        artifact_base_name = Path(item.copied_problematic_path).name
    artifact_path = folder / f"{artifact_base_name}.esito.json"
    payload = {
        "problematic_file": item.problematic_name,
        "problematic_path": item.problematic_path,
        "problematic_file_name": item.problematic_name,
        "problematic_file_path": item.problematic_path,
        "original_file": item.original_name,
        "original_path": item.original_path,
        "original_file_name": item.original_name,
        "original_file_path": item.original_path,
        "copied_problematic_file": Path(item.copied_problematic_path).name if item.copied_problematic_path else None,
        "copied_problematic_path": item.copied_problematic_path or None,
        "problematic_copy_created": bool(item.problematic_copy_created),
        "problematic_copy_byte_identical": bool(item.problematic_copy_byte_identical),
        "final_result": item.outcome.value,
        "reason": item.note,
        "strategy": item.strategy,
        "recovery_mode": item.recovery_mode,
        "forced_recovery": item.forced_recovery,
        "audio_comparison": {
            "executed": item.audio_comparison_executed,
            "status": "Eseguito" if item.audio_comparison_executed else "Non eseguito",
            "reason": item.audio_comparison_reason,
        },
        "problematic_audio_hash": item.problematic_audio_hash,
        "original_audio_hash": item.original_audio_hash,
        "duration_total_seconds": item.total_file_seconds,
        "phase_durations_seconds": {
            "search_original": item.search_original_seconds,
            "hash_problematic": item.hash_problematic_seconds,
            "hash_original": item.hash_original_seconds,
            "recovery": item.recovery_seconds,
            "verification_final": item.verification_final_seconds,
        },
        "session_timestamp": session_timestamp,
        "session_folder": item.session_folder,
        "result_folder": item.outcome_folder_path,
        "outcome_folder": item.outcome_folder_path,
        "recovered_file": item.recovered_path or None,
        "recovered_file_path": item.recovered_path or None,
        "originals_unchanged": originals_unchanged,
        "error": item.error_detail or "",
        "last_completed_phase": item.last_completed_phase,
        "final_timestamp": datetime.now().isoformat(timespec="seconds"),
        "compatibility_analysis": item.compatibility_analysis or {},
        "integrity_policy": {
            "assessed": item.integrity_policy_assessed,
            "integrity_certified": item.integrity_certified,
            "blocking_issues_count": item.blocking_issues_count,
            "non_blocking_tail_issues_count": item.non_blocking_tail_issues_count,
            "classification_reason": item.integrity_classification_reason,
        },
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(artifact_path)


def _write_session_summary(
    *,
    session_root: Path,
    session_timestamp: str,
    started_at: float,
    ended_at: float,
    total_problematic: int,
    examined_problematic: int,
    completed_problematic: int,
    counters: dict[str, int],
    interrupted: bool,
    log_callback: BatchLogCallback | None,
    session_snapshot: dict[str, Any],
) -> None:
    recovered_tags = counters.get(MP3BatchOutcome.RECOVERED_TAGS.value, 0)
    recovered_unchanged = counters.get(MP3BatchOutcome.RECOVERED_UNCHANGED.value, 0)
    recovered_forced = counters.get(MP3BatchOutcome.RECOVERED_FORCED.value, 0)
    recovered_total = recovered_tags + recovered_unchanged + recovered_forced
    non_recovered = max(0, total_problematic - recovered_total)
    duration_seconds = max(0.0, ended_at - started_at)
    status = "Interrotto" if interrupted else "Completato"
    options = [
        ("Modalità recupero", str(session_snapshot.get("recovery_mode_label", ""))),
        ("Recupero forzato", format_si_no(bool(session_snapshot.get("forced_recovery", False)))),
        ("Confronto audio", format_si_no(bool(session_snapshot.get("audio_comparison_enabled", False)))),
        ("Matching per nome file", format_si_no(bool(session_snapshot.get("matching_by_filename", True)))),
    ]
    paths = [
        ("Percorso file da recuperare", str(session_snapshot.get("problematic_dir", ""))),
        ("Percorso file originali integri", str(session_snapshot.get("originals_dir", ""))),
        ("Percorso file diagnosticati", str(session_snapshot.get("destination_dir", ""))),
        ("Percorso completo esito della sessione", str(session_root)),
    ]
    lines = build_session_configuration_header(
        processing_type="Recupero MP3",
        options=options,
        paths=paths,
    )
    lines.extend([
        f"Data e ora inizio sessione: {session_timestamp}",
        f"Data e ora fine sessione: {datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}",
        f"Durata complessiva (s): {duration_seconds:.3f}",
        f"File totali: {total_problematic}",
        f"File esaminati: {examined_problematic}",
        f"File completati: {completed_problematic}",
        f"Recuperati con TAG: {recovered_tags}",
        f"Recuperati come copia invariata: {recovered_unchanged}",
        f"Recuperati forzatamente: {recovered_forced}",
        f"Originali non trovati: {counters.get(MP3BatchOutcome.ORIGINAL_NOT_FOUND.value, 0)}",
        f"Originali incompatibili: {counters.get(MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value, 0)}",
        (
            "Piu originali compatibili: "
            f"{counters.get(MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value, 0) + counters.get(MP3BatchOutcome.MULTIPLE_SAME_NAME_ORIGINALS.value, 0)}"
        ),
        (
            "Errori: "
            f"{counters.get(MP3BatchOutcome.ERROR.value, 0) + counters.get(MP3BatchOutcome.READ_ERROR.value, 0) + counters.get(MP3BatchOutcome.WRITE_ERROR.value, 0) + counters.get(MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value, 0)}"
        ),
        f"Interrotti: {counters.get(MP3BatchOutcome.INTERRUPTED.value, 0)}",
        f"Stato batch: {status}",
        f"Percorso completo esito della sessione: {session_root}",
    ])
    if recovered_total <= 0:
        main_reason = counters.get(MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value, 0)
        lines.append("Nessun file MP3 recuperato.")
        if main_reason > 0:
            lines.append(f"Motivo principale: {main_reason} originale incompatibile.")
    else:
        lines.append(f"File non recuperati: {non_recovered}")
    summary_path = session_root / "Riepilogo sessione.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(log_callback, f"[TECH] Riepilogo sessione: {summary_path}")


def _write_session_error_file(session_root: Path, reason: str) -> None:
    try:
        session_root.mkdir(parents=True, exist_ok=True)
        error_path = session_root / "Errore sessione.txt"
        error_path.write_text(f"Sessione terminata con errore:\n{reason}\n", encoding="utf-8")
    except Exception:
        return


def _freeze_recovery_session_snapshot(
    *,
    snapshot: dict[str, Any] | None,
    problematic_root: Path,
    originals_root: Path,
    destination_root: Path,
    recovery_mode: RecoveryMode,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    mode_value = str(source.get("recovery_mode", recovery_mode.value)).strip().lower()
    coerced_mode = RecoveryMode.coerce(mode_value)
    forced = coerced_mode == RecoveryMode.FORCED
    mode_label = "Forzato" if forced else "Normale"
    return {
        "recovery_mode": coerced_mode.value,
        "recovery_mode_label": str(source.get("recovery_mode_label", mode_label)) or mode_label,
        "forced_recovery": bool(source.get("forced_recovery", forced)),
        "audio_comparison_enabled": bool(source.get("audio_comparison_enabled", not forced)),
        "matching_by_filename": bool(source.get("matching_by_filename", True)),
        "problematic_dir": clean_path_value(source.get("problematic_dir") or problematic_root),
        "originals_dir": clean_path_value(source.get("originals_dir") or originals_root),
        "destination_dir": clean_path_value(source.get("destination_dir") or destination_root),
    }


def _ensure_session_not_empty(session_root: Path, reason: str) -> None:
    try:
        if not session_root.exists():
            session_root.mkdir(parents=True, exist_ok=True)
        has_content = any(session_root.iterdir())
        if not has_content:
            (session_root / "Errore sessione.txt").write_text(
                f"Sessione senza artefatti utili.\nCausa: {reason}\n",
                encoding="utf-8",
            )
    except Exception:
        return


def _cleanup_empty_session_dirs(session_root: Path, *, log_callback: BatchLogCallback | None) -> None:
    if not session_root.exists() or not session_root.is_dir():
        return
    all_dirs = [path for path in session_root.rglob("*") if path.is_dir()]
    all_dirs.sort(key=lambda path: len(path.parts), reverse=True)
    for directory in all_dirs:
        try:
            if any(directory.iterdir()):
                continue
            directory.rmdir()
            _log(log_callback, f"[CLEANUP] Rimossa cartella vuota: {directory}")
        except OSError as error:
            _log(log_callback, f"[CLEANUP] Impossibile rimuovere: {directory} | {error}")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_html(path: Path, rows: list[dict[str, str]], problem_rows: list[dict[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else []
    problem_headers = list(problem_rows[0].keys()) if problem_rows else []
    lines = [
        "<!doctype html>",
        "<html lang='it'>",
        "<head>",
        "  <meta charset='utf-8'>",
        "  <title>Report Recupero MP3</title>",
        "  <style>",
        "    body { font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #1f2937; }",
        "    h1 { margin: 0 0 8px 0; font-size: 22px; }",
        "    p { margin: 0 0 16px 0; color: #4b5563; }",
        "    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 16px; }",
        "    th, td { border: 1px solid #d1d5db; padding: 6px; text-align: left; white-space: pre-wrap; vertical-align: top; }",
        "    th { background: #f3f4f6; }",
        "  </style>",
        "</head>",
        "<body>",
        "  <h1>Recupero MP3 da Originali</h1>",
        f"  <p>Generato: {datetime.now().isoformat(timespec='seconds')}</p>",
    ]

    if not headers:
        lines.append("<p>Nessun dato.</p>")
    else:
        lines.append("<table>")
        lines.append("<thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr></thead>")
        lines.append("<tbody>")
        for row in rows:
            lines.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(h, '')))}</td>" for h in headers) + "</tr>")
        lines.append("</tbody></table>")

    lines.append("  <h1>Problemi</h1>")
    if not problem_headers:
        lines.append("<p>Nessun problema rilevato.</p>")
    else:
        lines.append("<table>")
        lines.append("<thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in problem_headers) + "</tr></thead>")
        lines.append("<tbody>")
        for row in problem_rows:
            lines.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(h, '')))}</td>" for h in problem_headers) + "</tr>")
        lines.append("</tbody></table>")

    lines.extend(["</body>", "</html>"])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_xlsx(path: Path, rows: list[dict[str, str]], problem_rows: list[dict[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else []
    problem_headers = list(problem_rows[0].keys()) if problem_rows else []
    content_types = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        "<Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        "<Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
        "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
        "<Override PartName='/xl/worksheets/sheet2.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
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
        "<sheets>"
        "<sheet name='Recupero' sheetId='1' r:id='rId1'/>"
        "<sheet name='Problemi' sheetId='2' r:id='rId2'/>"
        "</sheets></workbook>"
    )
    workbook_rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/>"
        "<Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet2.xml'/>"
        "</Relationships>"
    )

    sheet_xml = _build_sheet_xml(headers, rows)
    problems_sheet_xml = _build_sheet_xml(problem_headers, problem_rows)

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/worksheets/sheet2.xml", problems_sheet_xml)


def _compute_compatibility_analysis(
    *,
    problematic_path: Path,
    original_path: Path,
    problematic_hash_result: _HashLookupResult,
    original_hash_result: _HashLookupResult,
    duration_cache: dict[Path, tuple[int | None, str | None]],
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    technical_errors: list[str] = []
    completed_checks: list[str] = []
    non_determinable_checks: list[str] = []

    def _register(check_key: str, payload: dict[str, Any]) -> None:
        checks[check_key] = payload
        status = str(payload.get("status") or CHECK_NOT_DETERMINABLE)
        if status in {CHECK_COMPATIBLE, CHECK_PROBABLY_COMPATIBLE, CHECK_INCOMPATIBLE}:
            completed_checks.append(check_key)
        if status in {CHECK_NOT_DETERMINABLE, CHECK_NOT_APPLICABLE, CHECK_TECHNICAL_ERROR}:
            non_determinable_checks.append(check_key)
        error_text = str(payload.get("technical_error") or "").strip()
        if error_text:
            technical_errors.append(f"{check_key}: {error_text}")

    audio_status = CHECK_NOT_DETERMINABLE
    audio_detail = ""
    if problematic_hash_result.audio_hash_sha256 and original_hash_result.audio_hash_sha256:
        if problematic_hash_result.audio_hash_sha256 == original_hash_result.audio_hash_sha256:
            if (
                problematic_hash_result.status == AudioHashStatus.VALID_AUDIO_STREAM
                and original_hash_result.status == AudioHashStatus.VALID_AUDIO_STREAM
            ):
                audio_status = CHECK_COMPATIBLE
                audio_detail = "Hash audio MPEG coincidente."
            else:
                audio_status = CHECK_PROBABLY_COMPATIBLE
                audio_detail = "Hash audio coincidente su stream non pienamente validato."
        else:
            audio_status = CHECK_INCOMPATIBLE
            audio_detail = "Hash audio MPEG differente."
            reasons.append("Contenuto audio differente")
    else:
        audio_detail = (
            f"Hash non disponibile (problematico={problematic_hash_result.status.value}, "
            f"originale={original_hash_result.status.value})."
        )
    _register(
        "audio_content",
        {
            "status": audio_status,
            "detail": audio_detail,
            "problematic_hash_status": problematic_hash_result.status.value,
            "original_hash_status": original_hash_result.status.value,
        },
    )

    tonality_check = _estimate_tonality(problematic_path=problematic_path, original_path=original_path)
    if str(tonality_check.get("status")) == CHECK_INCOMPATIBLE:
        reasons.append("Tonalita differente")
    _register("tonality", tonality_check)

    problematic_duration_ms, problematic_duration_error = _probe_duration_ms(problematic_path, duration_cache)
    original_duration_ms, original_duration_error = _probe_duration_ms(original_path, duration_cache)
    duration_difference_ms: int | None = None
    duration_difference_percent: float | None = None
    duration_status = CHECK_NOT_DETERMINABLE
    duration_detail = ""
    if problematic_duration_ms is None or original_duration_ms is None:
        duration_detail = "Durata non determinabile su almeno un file."
        duration_error_parts = [
            f"problematico: {problematic_duration_error}" if problematic_duration_error else "",
            f"originale: {original_duration_error}" if original_duration_error else "",
        ]
        duration_error = " | ".join(part for part in duration_error_parts if part)
    else:
        duration_difference_ms = abs(problematic_duration_ms - original_duration_ms)
        denominator = max(problematic_duration_ms, original_duration_ms, 1)
        duration_difference_percent = round((duration_difference_ms / float(denominator)) * 100.0, 4)
        if duration_difference_ms <= DURATION_TOLERANCE_MS:
            duration_status = CHECK_COMPATIBLE
            duration_detail = "Differenza durata entro tolleranza."
        else:
            duration_status = CHECK_INCOMPATIBLE
            duration_detail = "Differenza durata oltre tolleranza."
            reasons.append("Durata differente")
        duration_error = ""
    _register(
        "duration",
        {
            "status": duration_status,
            "detail": duration_detail,
            "difference_ms": duration_difference_ms,
            "difference_percent": duration_difference_percent,
            "tolerance_ms": DURATION_TOLERANCE_MS,
            "technical_error": duration_error,
        },
    )

    tempo_status = CHECK_NOT_DETERMINABLE
    tempo_detail = ""
    tempo_ratio: float | None = None
    tempo_percent: float | None = None
    if problematic_duration_ms is not None and original_duration_ms is not None and problematic_duration_ms > 0:
        tempo_ratio = round(original_duration_ms / float(problematic_duration_ms), 6)
        tempo_percent = round(abs(1.0 - tempo_ratio) * 100.0, 4)
        if tempo_percent <= TEMPO_TOLERANCE_PERCENT:
            tempo_status = CHECK_COMPATIBLE
            tempo_detail = "Rapporto temporale entro tolleranza."
        else:
            tempo_status = CHECK_NOT_DETERMINABLE
            tempo_detail = "Rapporto temporale stimato oltre soglia ma non sufficiente da solo per classificazione incompatibile."
    else:
        tempo_detail = "Durate insufficienti per stimare il rapporto temporale."
    _register(
        "tempo",
        {
            "status": tempo_status,
            "detail": tempo_detail,
            "ratio": tempo_ratio,
            "difference_percent": tempo_percent,
        },
    )

    offset_check = {
        "status": CHECK_NOT_DETERMINABLE,
        "detail": "Offset temporale non stimabile in modo affidabile con i dati correnti.",
        "offset_initial": "Non determinabile",
    }
    if (
        problematic_hash_result.first_frame_offset is not None
        and original_hash_result.first_frame_offset is not None
    ):
        byte_delta = int(original_hash_result.first_frame_offset - problematic_hash_result.first_frame_offset)
        offset_check = {
            "status": CHECK_NOT_DETERMINABLE,
            "detail": "Differenza offset frame rilevata in byte, conversione temporale non affidabile.",
            "offset_initial": f"{byte_delta} byte",
        }
    _register("offset", offset_check)
    _register(
        "cut_start",
        {
            "status": CHECK_NOT_DETERMINABLE,
            "detail": "Taglio iniziale non determinabile con i controlli disponibili.",
        },
    )
    _register(
        "cut_end",
        {
            "status": CHECK_NOT_DETERMINABLE,
            "detail": "Taglio finale non determinabile con i controlli disponibili.",
        },
    )

    mpeg_status = CHECK_NOT_DETERMINABLE
    mpeg_detail = "Dati insufficienti."
    if problematic_hash_result.audio_hash_sha256 and original_hash_result.audio_hash_sha256:
        if problematic_hash_result.audio_hash_sha256 == original_hash_result.audio_hash_sha256:
            mpeg_status = CHECK_COMPATIBLE
            mpeg_detail = "Sequenza audio MPEG allineata (hash coincidente)."
        else:
            mpeg_status = CHECK_NOT_DETERMINABLE
            mpeg_detail = "Frame audio differenti: possibile contenuto differente o ricodifica non distinguibile automaticamente."
    elif problematic_hash_result.status == AudioHashStatus.PARTIAL_AUDIO_STREAM or original_hash_result.status == AudioHashStatus.PARTIAL_AUDIO_STREAM:
        mpeg_status = CHECK_NOT_DETERMINABLE
        mpeg_detail = "Stream parziale: possibile equivalenza con ricodifica non verificabile."
    _register(
        "mpeg_structure",
        {
            "status": mpeg_status,
            "detail": mpeg_detail,
            "problematic_frames": problematic_hash_result.frames_count,
            "original_frames": original_hash_result.frames_count,
            "problematic_anomalies": list(problematic_hash_result.anomalies),
            "original_anomalies": list(original_hash_result.anomalies),
        },
    )

    dedup_reasons: list[str] = []
    for reason in reasons:
        if reason not in dedup_reasons:
            dedup_reasons.append(reason)

    incompatible_present = any(
        str(value.get("status")) == CHECK_INCOMPATIBLE
        for value in checks.values()
        if isinstance(value, dict)
    )
    deterministic_present = any(
        str(value.get("status")) in {CHECK_COMPATIBLE, CHECK_PROBABLY_COMPATIBLE, CHECK_INCOMPATIBLE}
        for value in checks.values()
        if isinstance(value, dict)
    )
    if incompatible_present:
        overall_status = OVERALL_INCOMPATIBLE
    elif deterministic_present and str((checks.get("audio_content") or {}).get("status")) in {CHECK_COMPATIBLE, CHECK_PROBABLY_COMPATIBLE}:
        overall_status = OVERALL_COMPATIBLE
    else:
        overall_status = OVERALL_NOT_DETERMINABLE

    return {
        "completed": True,
        "overall_status": overall_status,
        "reasons": dedup_reasons,
        "problematic_duration_ms": problematic_duration_ms,
        "problematic_duration_formatted": _format_duration_ms(problematic_duration_ms),
        "original_duration_ms": original_duration_ms,
        "original_duration_formatted": _format_duration_ms(original_duration_ms),
        "duration_difference_ms": duration_difference_ms,
        "duration_difference_formatted": _format_duration_ms(duration_difference_ms),
        "duration_difference_percent": duration_difference_percent,
        "duration_tolerance_ms": DURATION_TOLERANCE_MS,
        "checks": checks,
        "technical_errors": technical_errors,
        "completed_checks": completed_checks,
        "non_determinable_checks": non_determinable_checks,
    }


def _compute_forced_compatibility_analysis(
    *,
    problematic_path: Path,
    original_path: Path,
    duration_cache: dict[Path, tuple[int | None, str | None]],
) -> dict[str, Any]:
    problematic_duration_ms, problematic_duration_error = _probe_duration_ms(problematic_path, duration_cache)
    original_duration_ms, original_duration_error = _probe_duration_ms(original_path, duration_cache)

    duration_difference_ms: int | None = None
    duration_difference_percent: float | None = None
    if problematic_duration_ms is not None and original_duration_ms is not None:
        duration_difference_ms = abs(problematic_duration_ms - original_duration_ms)
        denominator = max(problematic_duration_ms, original_duration_ms, 1)
        duration_difference_percent = round((duration_difference_ms / float(denominator)) * 100.0, 4)

    checks = {
        "audio_content": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Confronto disabilitato dall'utente.",
        },
        "tonality": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificata in modalita forzata.",
        },
        "duration": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificata in modalita forzata.",
            "difference_ms": duration_difference_ms,
            "difference_percent": duration_difference_percent,
            "tolerance_ms": DURATION_TOLERANCE_MS,
            "technical_error": "",
        },
        "tempo": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificato in modalita forzata.",
            "ratio": None,
            "difference_percent": None,
        },
        "offset": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificato in modalita forzata.",
            "offset_initial": "Non determinabile",
        },
        "cut_start": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificato in modalita forzata.",
        },
        "cut_end": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificato in modalita forzata.",
        },
        "mpeg_structure": {
            "status": CHECK_NOT_APPLICABLE,
            "detail": "Non verificato in modalita forzata.",
        },
    }

    return {
        "completed": False,
        "overall_status": OVERALL_NOT_DETERMINABLE,
        "reasons": [],
        "problematic_duration_ms": problematic_duration_ms,
        "problematic_duration_formatted": _format_duration_ms(problematic_duration_ms),
        "original_duration_ms": original_duration_ms,
        "original_duration_formatted": _format_duration_ms(original_duration_ms),
        "duration_difference_ms": duration_difference_ms,
        "duration_difference_formatted": _format_duration_ms(duration_difference_ms),
        "duration_difference_percent": duration_difference_percent,
        "duration_tolerance_ms": DURATION_TOLERANCE_MS,
        "checks": checks,
        "technical_errors": [
            part
            for part in (
                f"problematico: {problematic_duration_error}" if problematic_duration_error else "",
                f"originale: {original_duration_error}" if original_duration_error else "",
            )
            if part
        ],
        "completed_checks": [],
        "non_determinable_checks": ["audio_content", "tonality", "duration", "tempo", "offset", "cut_start", "cut_end", "mpeg_structure"],
    }


def _estimate_tonality(*, problematic_path: Path, original_path: Path) -> dict[str, Any]:
    return {
        "status": CHECK_NOT_DETERMINABLE,
        "detail": "Analisi tonalita non disponibile nel motore corrente.",
        "problematic_value": None,
        "original_value": None,
        "semitone_difference": None,
        "confidence": "Bassa",
        "technical_error": "",
    }


def _probe_duration_ms(path: Path, cache: dict[Path, tuple[int | None, str | None]]) -> tuple[int | None, str | None]:
    cached = cache.get(path)
    if cached is not None:
        return cached
    try:
        duration_seconds = FFmpegManager(base_dir=Path(__file__).resolve().parent).get_duration(path)
        duration_ms = int(round(max(0.0, duration_seconds) * 1000.0))
        if duration_ms <= 0:
            result = (None, "Durata non positiva.")
        else:
            result = (duration_ms, None)
    except FFmpegError as error:
        result = (None, str(error))
    except Exception as error:
        result = (None, str(error))
    cache[path] = result
    return result


def _format_duration_ms(value_ms: int | None) -> str | None:
    if value_ms is None:
        return None
    total_ms = max(0, int(value_ms))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _build_problem_rows(item: MP3BatchItemResult) -> list[dict[str, str]]:
    analysis = item.compatibility_analysis or {}
    checks = analysis.get("checks") if isinstance(analysis, dict) else {}
    checks = checks if isinstance(checks, dict) else {}
    rows: list[dict[str, str]] = []
    tolerance_duration = _format_duration_ms(analysis.get("duration_tolerance_ms")) or "-"

    for key, payload in checks.items():
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or CHECK_NOT_DETERMINABLE)
        technical_error = str(payload.get("technical_error") or "").strip()
        include = status == CHECK_INCOMPATIBLE or bool(technical_error)
        if not include:
            continue
        if key == "duration":
            problematic_value = str(analysis.get("problematic_duration_formatted") or "Non determinabile")
            original_value = str(analysis.get("original_duration_formatted") or "Non determinabile")
            difference = str(analysis.get("duration_difference_formatted") or "Non determinabile")
            tolerance = tolerance_duration
            detail = str(payload.get("detail") or "")
        elif key == "tonality":
            problematic_value = str(payload.get("problematic_value") or "Non determinabile")
            original_value = str(payload.get("original_value") or "Non determinabile")
            difference = str(payload.get("semitone_difference") if payload.get("semitone_difference") is not None else "Non determinabile")
            tolerance = "-"
            detail = str(payload.get("detail") or "")
        else:
            problematic_value = str(payload.get("problematic_value") or "-")
            original_value = str(payload.get("original_value") or "-")
            difference = str(payload.get("difference") or payload.get("detail") or "-")
            tolerance = str(payload.get("tolerance") or "-")
            detail = str(payload.get("detail") or "")
        rows.append(
            {
                "File": item.problematic_name,
                "File originale": item.original_name,
                "Tipo controllo": key,
                "Esito controllo": status,
                "Valore problematico": problematic_value,
                "Valore originale": original_value,
                "Differenza": difference,
                "Tolleranza": tolerance,
                "Dettaglio": detail,
                "Affidabilita": str(payload.get("confidence") or "-"),
                "Errore tecnico": technical_error or "-",
            }
        )
    return rows


def _build_sheet_xml(headers: list[str], rows: list[dict[str, str]]) -> str:
    lines = [
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>",
    ]

    if headers:
        lines.append("<row r='1'>")
        for index, header in enumerate(headers, start=1):
            col = _xlsx_col(index)
            lines.append(f"<c r='{col}1' t='inlineStr'><is><t>{html.escape(header)}</t></is></c>")
        lines.append("</row>")

    for row_index, row in enumerate(rows, start=2):
        lines.append(f"<row r='{row_index}'>")
        for col_index, header in enumerate(headers, start=1):
            col = _xlsx_col(col_index)
            value = html.escape(str(row.get(header, "")))
            lines.append(f"<c r='{col}{row_index}' t='inlineStr'><is><t>{value}</t></is></c>")
        lines.append("</row>")

    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def _xlsx_col(index: int) -> str:
    col = ""
    value = index
    while value > 0:
        value, rem = divmod(value - 1, 26)
        col = chr(65 + rem) + col
    return col
