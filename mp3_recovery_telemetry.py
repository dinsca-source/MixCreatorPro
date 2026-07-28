# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Any


TelemetryLogCallback = Callable[[str], None]


@dataclass(slots=True)
class FilePhaseDurations:
    search_original_seconds: float = 0.0
    hash_problematic_seconds: float = 0.0
    hash_original_seconds: float = 0.0
    recovery_seconds: float = 0.0
    verification_final_seconds: float = 0.0
    total_file_seconds: float = 0.0


@dataclass(slots=True)
class HashSummary:
    role: str
    plan_elapsed_seconds: float = 0.0
    scan_elapsed_seconds: float = 0.0
    sha_elapsed_seconds: float = 0.0
    hash_total_elapsed_seconds: float = 0.0
    frames_found: int = 0
    frames_valid: int = 0
    frames_rejected: int = 0
    bytes_processed: int = 0
    audio_bytes_hashed: int = 0
    last_offset: int = 0
    outer_iterations: int = 0
    inner_iterations: int = 0
    average_speed_mb_s: float = 0.0
    parse_calls_total: int = 0
    unique_offsets_total: int = 0
    parse_cache_hits: int = 0
    parse_cache_misses: int = 0
    inner_per_valid_ratio: float = 0.0
    scanner_duration_seconds: float = 0.0
    scanner_average_speed_mb_s: float = 0.0
    error_or_cancel_reason: str = ""


@dataclass(slots=True)
class FileSummary:
    file_name: str
    full_path: str
    file_size: int
    outcome: str = ""
    cause: str = ""
    phase_durations: FilePhaseDurations = field(default_factory=FilePhaseDurations)
    problematic_hash: HashSummary = field(default_factory=lambda: HashSummary(role="problematico"))
    original_hash: HashSummary = field(default_factory=lambda: HashSummary(role="originale"))


class RecoveryTelemetrySession:
    def __init__(
        self,
        output_root: Path,
        log_callback: TelemetryLogCallback | None = None,
        *,
        session_timestamp: str | None = None,
        diagnostics_dir_name: str = "Diagnostica",
    ) -> None:
        self.output_root = output_root
        self.log_callback = log_callback
        self.started_at = time.monotonic()
        self.session_id = uuid.uuid4().hex
        self.timestamp = session_timestamp or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.diagnostics_dir = self.output_root / diagnostics_dir_name
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.diagnostics_dir / f"scanner_mpeg_{self.timestamp}.jsonl"
        self.current_state_path = self.diagnostics_dir / "scanner_mpeg_current_state.json"
        self.summary_csv_path = self.diagnostics_dir / f"scanner_mpeg_summary_{self.timestamp}.csv"
        self._handle = self.jsonl_path.open("a", encoding="utf-8")
        self._files: dict[str, FileSummary] = {}
        self.emit_event("SESSION_START", phase="Sessione", message="Avvio telemetria scanner MPEG.", critical=True)

    def close(self, *, final_message: str = "Sessione completata.", cancelled: bool = False) -> None:
        try:
            self.emit_event(
                "SESSION_END",
                phase="Sessione",
                message=final_message,
                cancel_requested=cancelled,
                critical=True,
            )
            self.write_summary_csv()
        finally:
            try:
                self._handle.close()
            except Exception:
                pass

    def ensure_file(self, problematic_path: Path, file_size: int) -> FileSummary:
        key = str(problematic_path.resolve())
        summary = self._files.get(key)
        if summary is None:
            summary = FileSummary(
                file_name=problematic_path.name,
                full_path=key,
                file_size=int(file_size),
            )
            self._files[key] = summary
        return summary

    def emit_file_start(self, problematic_path: Path, file_size: int) -> None:
        self.ensure_file(problematic_path, file_size)
        self.emit_event(
            "FILE_START",
            problematic_file=problematic_path.name,
            full_path=str(problematic_path.resolve()),
            file_size=file_size,
            phase="File",
            message="Inizio elaborazione file.",
        )

    def emit_file_end(self, problematic_path: Path, outcome: str, cause: str = "") -> None:
        summary = self.ensure_file(problematic_path, problematic_path.stat().st_size if problematic_path.exists() else 0)
        summary.outcome = outcome
        summary.cause = cause
        self.emit_event(
            "FILE_END",
            problematic_file=summary.file_name,
            full_path=summary.full_path,
            file_size=summary.file_size,
            phase="File",
            message=cause or outcome,
        )

    def set_phase_duration(self, problematic_path: Path, phase_name: str, seconds: float) -> None:
        summary = self.ensure_file(problematic_path, problematic_path.stat().st_size if problematic_path.exists() else 0)
        if phase_name == "search_original":
            summary.phase_durations.search_original_seconds = max(0.0, seconds)
        elif phase_name == "hash_problematic":
            summary.phase_durations.hash_problematic_seconds = max(0.0, seconds)
        elif phase_name == "hash_original":
            summary.phase_durations.hash_original_seconds = max(0.0, seconds)
        elif phase_name == "recovery":
            summary.phase_durations.recovery_seconds = max(0.0, seconds)
        elif phase_name == "verification_final":
            summary.phase_durations.verification_final_seconds = max(0.0, seconds)
        elif phase_name == "total_file":
            summary.phase_durations.total_file_seconds = max(0.0, seconds)

    def update_hash_summary(self, problematic_path: Path, role: str, data: dict[str, Any]) -> None:
        summary = self.ensure_file(problematic_path, problematic_path.stat().st_size if problematic_path.exists() else 0)
        target = summary.problematic_hash if role == "problematico" else summary.original_hash
        target.plan_elapsed_seconds = float(data.get("plan_elapsed_seconds", target.plan_elapsed_seconds) or 0.0)
        target.scan_elapsed_seconds = float(data.get("scan_elapsed_seconds", target.scan_elapsed_seconds) or 0.0)
        target.sha_elapsed_seconds = float(data.get("sha_elapsed_seconds", target.sha_elapsed_seconds) or 0.0)
        target.hash_total_elapsed_seconds = float(data.get("hash_total_elapsed_seconds", target.hash_total_elapsed_seconds) or 0.0)
        target.frames_found = int(data.get("frames_found", target.frames_found) or 0)
        target.frames_valid = int(data.get("frames_valid", target.frames_valid) or 0)
        target.frames_rejected = int(data.get("frames_rejected", target.frames_rejected) or 0)
        target.bytes_processed = int(data.get("bytes_processed", target.bytes_processed) or 0)
        target.audio_bytes_hashed = int(data.get("audio_bytes_hashed", target.audio_bytes_hashed) or 0)
        target.last_offset = int(data.get("offset", target.last_offset) or 0)
        target.outer_iterations = int(data.get("outer_iteration", target.outer_iterations) or 0)
        target.inner_iterations = int(data.get("inner_iteration", target.inner_iterations) or 0)
        target.average_speed_mb_s = float(data.get("speed_mb_s", target.average_speed_mb_s) or 0.0)
        target.parse_calls_total = int(data.get("parse_calls_total", target.parse_calls_total) or 0)
        target.unique_offsets_total = int(data.get("unique_offsets_total", target.unique_offsets_total) or 0)
        target.parse_cache_hits = int(data.get("parse_cache_hits", target.parse_cache_hits) or 0)
        target.parse_cache_misses = int(data.get("parse_cache_misses", target.parse_cache_misses) or 0)
        target.scanner_duration_seconds = float(data.get("scan_elapsed_seconds", data.get("scanner_duration_seconds", target.scanner_duration_seconds)) or 0.0)
        target.scanner_average_speed_mb_s = float(data.get("scanner_average_speed_mb_s", target.scanner_average_speed_mb_s) or 0.0)
        if target.frames_valid > 0:
            target.inner_per_valid_ratio = target.inner_iterations / float(target.frames_valid)
        if data.get("message"):
            target.error_or_cancel_reason = str(data.get("message"))

    def emit_event(
        self,
        event_type: str,
        *,
        problematic_file: str = "",
        full_path: str = "",
        file_size: int = 0,
        phase: str = "",
        offset: int | None = None,
        previous_offset: int | None = None,
        next_offset: int | None = None,
        frame_length: int | None = None,
        outer_iteration: int | None = None,
        inner_iteration: int | None = None,
        frames_found: int | None = None,
        frames_valid: int | None = None,
        frames_rejected: int | None = None,
        bytes_processed: int | None = None,
        percent: float | None = None,
        speed_mb_s: float | None = None,
        message: str = "",
        cancel_requested: bool = False,
        thread_id: int | None = None,
        monotonic_elapsed: float | None = None,
        critical: bool = False,
        last_phase: str = "",
        batch_status: str = "",
        files_examined: int | None = None,
        files_completed: int | None = None,
        files_total: int | None = None,
        **extra_fields: Any,
    ) -> dict[str, Any]:
        payload = {
            "local_timestamp": datetime.now().isoformat(timespec="seconds"),
            "monotonic_elapsed": float(max(0.0, monotonic_elapsed if monotonic_elapsed is not None else time.monotonic() - self.started_at)),
            "session_id": self.session_id,
            "event_type": event_type,
            "problematic_file": problematic_file,
            "full_path": full_path,
            "file_size": int(file_size),
            "phase": phase,
            "offset": offset,
            "previous_offset": previous_offset,
            "next_offset": next_offset,
            "frame_length": frame_length,
            "outer_iteration": outer_iteration,
            "inner_iteration": inner_iteration,
            "frames_found": frames_found,
            "frames_valid": frames_valid,
            "frames_rejected": frames_rejected,
            "bytes_processed": bytes_processed,
            "percent": percent,
            "speed_mb_s": speed_mb_s,
            "message": message,
            "cancel_requested": bool(cancel_requested),
            "thread_id": int(thread_id if thread_id is not None else threading.get_ident()),
            "last_phase": last_phase,
            "batch_status": batch_status,
            "files_examined": files_examined,
            "files_completed": files_completed,
            "files_total": files_total,
        }
        if extra_fields:
            payload.update(extra_fields)
        self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._handle.flush()
        if critical:
            os.fsync(self._handle.fileno())
        return payload

    def update_current_state(self, payload: dict[str, Any]) -> None:
        state = {
            "file": payload.get("problematic_file", ""),
            "phase": payload.get("phase", ""),
            "last_phase": payload.get("last_phase", ""),
            "batch_status": payload.get("batch_status", ""),
            "elapsed": payload.get("monotonic_elapsed", 0.0),
            "offset": payload.get("offset"),
            "previous_offset": payload.get("previous_offset"),
            "next_offset": payload.get("next_offset"),
            "frame_length": payload.get("frame_length"),
            "iterazioni": {
                "outer": payload.get("outer_iteration"),
                "inner": payload.get("inner_iteration"),
            },
            "frame_trovati": payload.get("frames_found"),
            "frame_validi": payload.get("frames_valid"),
            "frame_scartati": payload.get("frames_rejected"),
            "percentuale": payload.get("percent"),
            "velocita_MB_s": payload.get("speed_mb_s"),
            "parse_calls_total": payload.get("parse_calls_total"),
            "offset_unici": payload.get("unique_offsets_total"),
            "cache_hit": payload.get("parse_cache_hits"),
            "cache_miss": payload.get("parse_cache_misses"),
            "files_examined": payload.get("files_examined"),
            "files_completed": payload.get("files_completed"),
            "files_total": payload.get("files_total"),
            "timestamp_ultimo_aggiornamento": payload.get("local_timestamp", ""),
                "session_folder": payload.get("session_folder", ""),
                "result_folder": payload.get("result_folder", ""),
                "recovered_file": payload.get("recovered_file", ""),
                "result_json": payload.get("result_json", ""),
                "final_result": payload.get("final_result", ""),
                "reason": payload.get("reason", ""),
                "final_timestamp": payload.get("final_timestamp", ""),
        }
        temp_path = self.current_state_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.current_state_path)

    def write_summary_csv(self) -> None:
        headers = [
            "nome_file",
            "dimensione",
            "esito",
            "durata_parsing_regioni_escluse_problematico",
            "durata_scansione_mpeg_problematico",
            "durata_sha256_problematico",
            "durata_hash_complessiva_problematico",
            "frame_trovati_problematico",
            "byte_audio_hashati_problematico",
            "velocita_media_problematico",
            "ultimo_offset_problematico",
            "numero_iterazioni_outer_problematico",
            "numero_iterazioni_inner_problematico",
            "parse_calls_totali_problematico",
            "offset_unici_problematico",
            "cache_hit_problematico",
            "cache_miss_problematico",
            "rapporto_inner_frame_validi_problematico",
            "velocita_media_scanner_mb_s_problematico",
            "durata_scanner_secondi_problematico",
            "tempo_ricerca_originale",
            "tempo_hash_problematico",
            "tempo_hash_originale",
            "tempo_recupero",
            "tempo_verifica_finale",
            "tempo_totale_file",
            "causa_errore_o_cancellazione",
        ]
        with self.summary_csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for summary in self._files.values():
                writer.writerow(
                    {
                        "nome_file": summary.file_name,
                        "dimensione": summary.file_size,
                        "esito": summary.outcome,
                        "durata_parsing_regioni_escluse_problematico": f"{summary.problematic_hash.plan_elapsed_seconds:.6f}",
                        "durata_scansione_mpeg_problematico": f"{summary.problematic_hash.scan_elapsed_seconds:.6f}",
                        "durata_sha256_problematico": f"{summary.problematic_hash.sha_elapsed_seconds:.6f}",
                        "durata_hash_complessiva_problematico": f"{summary.problematic_hash.hash_total_elapsed_seconds:.6f}",
                        "frame_trovati_problematico": summary.problematic_hash.frames_found,
                        "byte_audio_hashati_problematico": summary.problematic_hash.audio_bytes_hashed,
                        "velocita_media_problematico": f"{summary.problematic_hash.average_speed_mb_s:.6f}",
                        "ultimo_offset_problematico": summary.problematic_hash.last_offset,
                        "numero_iterazioni_outer_problematico": summary.problematic_hash.outer_iterations,
                        "numero_iterazioni_inner_problematico": summary.problematic_hash.inner_iterations,
                        "parse_calls_totali_problematico": summary.problematic_hash.parse_calls_total,
                        "offset_unici_problematico": summary.problematic_hash.unique_offsets_total,
                        "cache_hit_problematico": summary.problematic_hash.parse_cache_hits,
                        "cache_miss_problematico": summary.problematic_hash.parse_cache_misses,
                        "rapporto_inner_frame_validi_problematico": f"{summary.problematic_hash.inner_per_valid_ratio:.6f}",
                        "velocita_media_scanner_mb_s_problematico": f"{summary.problematic_hash.scanner_average_speed_mb_s:.6f}",
                        "durata_scanner_secondi_problematico": f"{summary.problematic_hash.scanner_duration_seconds:.6f}",
                        "tempo_ricerca_originale": f"{summary.phase_durations.search_original_seconds:.6f}",
                        "tempo_hash_problematico": f"{summary.phase_durations.hash_problematic_seconds:.6f}",
                        "tempo_hash_originale": f"{summary.phase_durations.hash_original_seconds:.6f}",
                        "tempo_recupero": f"{summary.phase_durations.recovery_seconds:.6f}",
                        "tempo_verifica_finale": f"{summary.phase_durations.verification_final_seconds:.6f}",
                        "tempo_totale_file": f"{summary.phase_durations.total_file_seconds:.6f}",
                        "causa_errore_o_cancellazione": summary.cause or summary.problematic_hash.error_or_cancel_reason,
                    }
                )


class HashTelemetrySink:
    def __init__(
        self,
        session: RecoveryTelemetrySession,
        *,
        problematic_path: Path,
        full_path: Path,
        file_size: int,
        role: str,
        gui_log_callback: TelemetryLogCallback | None = None,
    ) -> None:
        self.session = session
        self.problematic_path = problematic_path
        self.full_path = full_path
        self.file_size = int(file_size)
        self.role = role
        self.gui_log_callback = gui_log_callback
        self.last_heartbeat_gui_at = -1.0

    def __call__(self, message: str) -> None:
        if self.gui_log_callback is None:
            return
        if message.startswith("[HASH] Scanner MPEG avviato"):
            self.gui_log_callback(message)
            return
        if message.startswith("[HASH] Scanner terminato"):
            self.gui_log_callback(message)
            return
        if "FRAME_LENGTH_NON_VALIDO" in message or "NEXT_OFFSET_NON_PROGRESSIVO" in message or "OFFSET_NON_AVANZA" in message:
            self.gui_log_callback(message)
            return
        if message.startswith("[HASH] Cancellato") or message.startswith("[HASH] Errore"):
            self.gui_log_callback(message)

    def telemetry_event(self, event_type: str, payload: dict[str, Any], *, critical: bool = False) -> None:
        merged = dict(payload)
        merged.setdefault("problematic_file", self.problematic_path.name)
        merged.setdefault("full_path", str(self.full_path))
        merged.setdefault("file_size", self.file_size)
        merged.setdefault("cancel_requested", False)
        event = self.session.emit_event(event_type, critical=critical, **merged)
        if event_type == "MPEG_SCAN_HEARTBEAT":
            self.session.update_hash_summary(self.problematic_path, self.role, event)
            self.session.update_current_state(event)
            if self.gui_log_callback is not None:
                elapsed = float(event.get("monotonic_elapsed") or 0.0)
                if elapsed - self.last_heartbeat_gui_at >= 1.0:
                    total = max(1, self.file_size)
                    offset = int(event.get("offset") or 0)
                    percent = float(event.get("percent") or 0.0)
                    frames = int(event.get("frames_valid") or 0)
                    speed = float(event.get("speed_mb_s") or 0.0)
                    self.gui_log_callback(
                        f"[HASH] {self._format_elapsed(elapsed)} | offset={offset}/{total} | {percent:.1f}% | frame={frames} | {speed:.2f} MB/s"
                    )
                    self.last_heartbeat_gui_at = elapsed
        elif event_type in {"NON_PROGRESS", "CANCEL_DETECTED", "ERROR"}:
            self.session.update_hash_summary(self.problematic_path, self.role, event)
            self.session.update_current_state(event)
        elif event_type == "MPEG_SCAN_END":
            self.session.update_hash_summary(self.problematic_path, self.role, event)
            self.session.update_current_state(event)
        elif event_type == "HASH_END":
            self.session.update_hash_summary(self.problematic_path, self.role, event)
        elif event_type == "HASH_PLAN_END":
            self.session.update_hash_summary(self.problematic_path, self.role, event)

    @staticmethod
    def _format_elapsed(total_seconds: float) -> str:
        total = max(0, int(total_seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
