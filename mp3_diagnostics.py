# -*- coding: utf-8 -*-
"""
MixCreator PRO
mp3_diagnostics.py

Diagnostica e riparazione MP3 con classificazione a tre stati:
- Integro
- Riparato
- Non recuperabile
"""

from __future__ import annotations

import array
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from zipfile import ZIP_DEFLATED, ZipFile

from ffmpeg_manager import FFmpegManager
from winlive_classification import PostNormalizationValidationStatus, WinLiveClassificationInput, WinLiveOutcome, classify_winlive
from winlive_normalizer import contains_semantic_text, count_unrecognized_chords, normalize_synct_content
from winlive_safe_write import WinLiveWriteValidationResult, decode_text_lossless, write_normalized_winlive_copy
from winlive_tags import WinLiveStructureState, parse_winlive_blocks_strict


STATUS_PERFECT = "Integro"
STATUS_REPAIRED = "Riparato"
STATUS_UNRECOVERABLE = "Non recuperabile"

FINAL_HEALTHY = "healthy"
FINAL_REPAIRED = "repaired"
FINAL_UNRECOVERABLE = "unrecoverable"

OUTPUT_FOLDER_OK = "File già rilevati OK"
OUTPUT_FOLDER_REPAIRED = "File riparati"
OUTPUT_FOLDER_UNRECOVERABLE = "Non recuperabili"
OUTPUT_FOLDER_PROCESSED_ORIGINALS = "Originali dei file elaborati"
OUTPUT_FOLDER_REPORT = "Report"
OUTPUT_FOLDER_INTEGRITY_ROOT = "Esito integrità MP3"
OUTPUT_FOLDER_WINLIVE = "Esito WinLive"
OUTPUT_SESSION_PREFIX = "Diagnostica_MP3_"
OUTPUT_FOLDER_TEMP = "_TMP_DIAGNOSTICS"
WINLIVE_FOLDER_CONFORME = "Conformi"
WINLIVE_FOLDER_NORMALIZZATI = "Normalizzati"
WINLIVE_FOLDER_SENZA_TAG_TESTO = "Senza TAG di Testo"
WINLIVE_FOLDER_SENZA_TAG_ACCORDI = "Senza TAG Accordi"
WINLIVE_FOLDER_SENZA_TAG_TESTO_ACCORDI = "Senza TAG di Testo e di Accordi"
WINLIVE_FOLDER_ACCORDI_NON_RICONOSCIUTI = "Accordi non riconosciuti"
WINLIVE_FOLDER_NON_INTEGRO_DOPO_MODIFICA = "Non integro dopo modifica"
WINLIVE_FOLDER_ERRORI_STRUTTURA = "Errori struttura WinLive"

PLACEMENT_MODE_COPY = "copy"
PLACEMENT_MODE_MOVE = "move"

WINLIVE_COL_VERIFY = "Verifica WinLive"
WINLIVE_COL_STATUS = "Stato WinLive"
WINLIVE_COL_TEXT_PRESENT = "Testo presente"
WINLIVE_COL_CHORDS_PRESENT = "Accordi presenti"
WINLIVE_COL_UNRECOGNIZED = "Accordi non riconosciuti"
WINLIVE_COL_UNRECOGNIZED_TOKENS = "Token accordi non riconosciuti"
WINLIVE_COL_NORMALIZATION_REQUIRED = "Normalizzazione richiesta"
WINLIVE_COL_NORMALIZATION_ATTEMPTED = "Normalizzazione tentata"
WINLIVE_COL_NORMALIZATION_VALIDATED = "Normalizzazione validata"
WINLIVE_COL_POST_VALIDATION_REASON = "Motivo validazione post-modifica"
WINLIVE_COL_NOTES = "Note WinLive"
WINLIVE_COL_ERROR_CODE = "Codice errore WinLive"
WINLIVE_COL_ERROR = "Errore WinLive"
WINLIVE_COL_OUTPUT_FOLDER = "Cartella esito WinLive"
WINLIVE_COL_OUTPUT_PATH = "Percorso esito WinLive"
WINLIVE_COL_OUTPUT_OPERATION = "Operazione esito WinLive"
WINLIVE_COL_CHAINS_DETECTED = "Catene temporali rilevate"
WINLIVE_COL_TAGS_REMOVED = "Tag temporali rimossi"
WINLIVE_COL_EMPTY_TIMED_LINES_DETECTED = "Righe solo temporali rilevate"
WINLIVE_COL_EMPTY_TIMED_LINES_REMOVED = "Righe solo temporali eliminate"
WINLIVE_COL_SYNCT_PRESENT = "Presenza WL5SYNCT"
WINLIVE_COL_CHORD_PRESENT = "Presenza WL5CHORD"
COL_INTEGRITY_EXECUTED = "Controllo integrità MP3 eseguito"

# Legacy folders are excluded from scan for backward compatibility.
LEGACY_OUTPUT_FOLDERS = (
    "Parziali",
    "MP3_RIPARATI",
    "MP3_NON_RIPARABILI",
    "Report",
    "REPORT",
    "_TMP_DIAGNOSTICS",
)

PRECISION_EXACT = "Esatta"
PRECISION_500MS = "Intervallo 500 ms"
PRECISION_2S = "Intervallo 2 s"
PRECISION_10S = "Intervallo 10 s"
PRECISION_UNKNOWN = "Non determinabile"

# Significant-audio bounds configuration.
AUDIO_WINDOW_MS = 100
SILENCE_RMS_THRESHOLD_DB = -55.0
SILENCE_PEAK_THRESHOLD_DB = -45.0
MIN_SIGNIFICANT_AUDIO_DURATION_MS = 300
MIN_SILENCE_RUN_MS = 400
AUDIO_BOUNDS_SAFETY_MARGIN_MS = 250


class MP3DiagnosticsError(RuntimeError):
    """General MP3 diagnostics error."""


class MP3DiagnosticsCancelled(MP3DiagnosticsError):
    """Diagnostics cancelled by user."""


class DiagnosticCategory(str, Enum):
    OK = "ok"
    REPAIRED = "repaired"
    UNRECOVERABLE = "unrecoverable"


class IssuePosition(str, Enum):
    LEADING_SILENCE = "LEADING_SILENCE"
    SIGNIFICANT_AUDIO = "SIGNIFICANT_AUDIO"
    TRAILING_SILENCE = "TRAILING_SILENCE"
    BOUNDARY_OVERLAP = "BOUNDARY_OVERLAP"
    UNKNOWN = "UNKNOWN"


ProgressCallback = Callable[[int, int, str], None]


_ERROR_PATTERNS: dict[str, tuple[str, str]] = {
    "header_missing": ("Header mancante", r"(?:header\s+missing|missing\s+header)"),
    "corrupted_frames": ("Frame corrotto", r"(?:corrupt|damaged)"),
    "crc_errors": ("CRC errato", r"\bcrc\b"),
    "sync_errors": ("Errore di sincronizzazione", r"\bsync\b"),
    "undecodable_frames": ("Frame MP3 non decodificabile", r"(?:error while decoding|decode error|cannot decode)"),
    "invalid_data": ("Dati invalidi", r"invalid data"),
    "xing_issues": ("Problema Xing", r"\bxing\b"),
    "vbr_issues": ("Problema VBR", r"\bvbr\b"),
    "id3_issues": ("Problema ID3", r"\bid3\b"),
}

_BLOCKING_ERROR_FIELDS = (
    "header_missing",
    "corrupted_frames",
    "sync_errors",
    "undecodable_frames",
    "invalid_data",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _format_hhmmss_mmm_from_seconds(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000.0)))
    hours = total_ms // 3_600_000
    rem = total_ms % 3_600_000
    minutes = rem // 60_000
    secs = (rem % 60_000) // 1000
    millis = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


@dataclass(slots=True)
class AudioWindowStats:
    start_ms: int
    end_ms: int
    rms_dbfs: float
    peak_dbfs: float


@dataclass(slots=True)
class AudioBounds:
    file_duration_ms: int
    significant_start_ms: int
    significant_end_ms: int
    leading_silence_ms: int
    trailing_silence_ms: int
    detection_confidence: float
    threshold_rms_db: float
    threshold_peak_db: float


@dataclass(slots=True)
class DiagnosticIssue:
    problem_key: str
    problem_type: str
    start: str = "Tempo non determinabile"
    end: str = ""
    precision: str = PRECISION_UNKNOWN
    intervention: str = "Rilevato"
    detail: str = ""


@dataclass(slots=True)
class EvaluatedIssue:
    issue: DiagnosticIssue
    position: IssuePosition
    ignored_for_classification: bool
    exclusion_reason: str
    zone_label: str
    rms_dbfs: float
    peak_dbfs: float
    impact_label: str
    segment_start_ms: int | None
    segment_end_ms: int | None


@dataclass(slots=True)
class AnalysisResult:
    command: list[str]
    command_text: str
    return_code: int
    decode_log: str
    issues: list[DiagnosticIssue]
    metrics: dict[str, int]
    integrity_index: int
    total_errors: int


@dataclass(slots=True)
class MP3DiagnosticResult:
    file_name: str
    file_path: str
    normalized_path: str
    file_size_bytes: int
    file_mtime_ts: float
    file_hash_sha256: str
    operational_integrity_before: int
    operational_integrity_after: int
    total_errors_before: int
    total_errors_after: int
    repaired: bool
    repair_outcome: str
    final_status: str
    final_category: DiagnosticCategory
    final_folder: str
    classification_reason: str
    blocking_residual: bool
    bounds_before: AudioBounds
    bounds_after: AudioBounds
    evaluated_issues_before: list[EvaluatedIssue] = field(default_factory=list)
    evaluated_issues_after: list[EvaluatedIssue] = field(default_factory=list)
    ignored_anomalies_count: int = 0
    output_repaired_path: str = ""
    output_unrecoverable_path: str = ""
    analysis_command: str = ""
    analysis_return_code: int = 0
    repair_command: str = ""
    repair_return_code: int = 0
    repair_mode_used: str = ""
    raw_decode_log_before: str = ""
    raw_decode_log_after: str = ""
    raw_repair_log: str = ""
    placed_file_path: str = ""
    placed_file_kind: str = ""
    placement_operation: str = ""
    placement_mode_label: str = "Copia"
    original_preserved: bool = False
    preserved_original_path: str = ""
    placement_effective_operation: str = ""
    file_already_present: bool = False
    winlive: WinLiveDiagnosticResult = field(default_factory=lambda: WinLiveDiagnosticResult())

    @staticmethod
    def _si_no(value: bool) -> str:
        return "SI" if value else "NO"

    @staticmethod
    def _join_notes(notes: list[str]) -> str:
        cleaned = [item.strip() for item in notes if item and item.strip()]
        return " | ".join(cleaned)

    def _winlive_summary_fields(self) -> dict[str, Any]:
        status = self.winlive.stato_winlive_finale.value if self.winlive.stato_winlive_finale is not None else ""
        return {
            WINLIVE_COL_VERIFY: self._si_no(self.winlive.verifica_winlive_eseguita),
            WINLIVE_COL_STATUS: status,
            WINLIVE_COL_SYNCT_PRESENT: self._si_no(self.winlive.synct_presente_prima),
            WINLIVE_COL_CHORD_PRESENT: self._si_no(self.winlive.chord_presente_prima),
            WINLIVE_COL_TEXT_PRESENT: self._si_no(self.winlive.testo_presente),
            WINLIVE_COL_CHORDS_PRESENT: self._si_no(self.winlive.accordi_presenti),
            WINLIVE_COL_UNRECOGNIZED: self.winlive.accordi_non_riconosciuti,
            WINLIVE_COL_UNRECOGNIZED_TOKENS: ", ".join(self.winlive.accordi_non_riconosciuti_token),
            WINLIVE_COL_NORMALIZATION_REQUIRED: self._si_no(self.winlive.normalizzazione_necessaria),
            WINLIVE_COL_NORMALIZATION_ATTEMPTED: self._si_no(self.winlive.normalizzazione_tentata),
            WINLIVE_COL_NORMALIZATION_VALIDATED: self._si_no(self.winlive.normalizzazione_validata),
            WINLIVE_COL_POST_VALIDATION_REASON: self.winlive.motivo_validazione_post_modifica,
            WINLIVE_COL_NOTES: self._join_notes(self.winlive.note_winlive),
            WINLIVE_COL_ERROR_CODE: self.winlive.errore_winlive_code,
            WINLIVE_COL_ERROR: self.winlive.errore_winlive,
            WINLIVE_COL_OUTPUT_FOLDER: self.winlive.esito_cartella_winlive,
            WINLIVE_COL_OUTPUT_PATH: self.winlive.esito_percorso_winlive,
            WINLIVE_COL_OUTPUT_OPERATION: self.winlive.esito_operazione_winlive,
            WINLIVE_COL_CHAINS_DETECTED: self.winlive.catene_temporali_rilevate,
            WINLIVE_COL_TAGS_REMOVED: self.winlive.tag_temporali_rimossi,
            WINLIVE_COL_EMPTY_TIMED_LINES_DETECTED: self.winlive.righe_temporali_vuote_rilevate,
            WINLIVE_COL_EMPTY_TIMED_LINES_REMOVED: self.winlive.righe_temporali_vuote_eliminate,
        }

    def to_summary_row(
        self,
        index: int,
        include_winlive: bool = False,
        include_integrity: bool = True,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "Numero": index,
            "File": self.file_name,
            "Percorso originale": self.file_path,
            COL_INTEGRITY_EXECUTED: "SI" if include_integrity else "NO",
        }
        if include_integrity:
            row.update(
                {
                    "Stato finale file": self.repair_outcome,
                    "Categoria finale": self.final_folder,
                    "Integrita operativa iniziale": self.operational_integrity_before,
                    "Integrita operativa finale": self.operational_integrity_after,
                    "Errori iniziali": self.total_errors_before,
                    "Errori finali": self.total_errors_after,
                    "Anomalie tecniche ignorate": self.ignored_anomalies_count,
                    "Inizio audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_start_ms / 1000.0),
                    "Fine audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_end_ms / 1000.0),
                    "Silenzio iniziale (ms)": self.bounds_before.leading_silence_ms,
                    "Silenzio finale (ms)": self.bounds_before.trailing_silence_ms,
                    "File collocato": Path(self.placed_file_path).name if self.placed_file_path else "",
                    "Tipo file collocato": self.placed_file_kind,
                    "Percorso finale": self.placed_file_path,
                    "Operazione eseguita": self.placement_operation,
                    "Modalità collocazione": self.placement_mode_label,
                    "Originale conservato": "SI" if self.original_preserved else "NO",
                    "Percorso originale conservato": self.preserved_original_path,
                    "Operazione effettivamente eseguita": self.placement_effective_operation,
                    "File già presente": "SI" if self.file_already_present else "NO",
                    "Cartella finale": self.final_folder,
                    "Motivo classificazione finale": self.classification_reason,
                    "Hash SHA-256": self.file_hash_sha256,
                    "Errori bloccanti residui": "SI" if self.blocking_residual else "NO",
                    "Comando analisi": self.analysis_command,
                    "Exit code analisi": self.analysis_return_code,
                    "Comando riparazione": self.repair_command,
                    "Exit code riparazione": self.repair_return_code,
                    "Metodo riparazione": self.repair_mode_used,
                    "Output riparato": self.output_repaired_path,
                    "Output non recuperabile": self.output_unrecoverable_path,
                }
            )
        else:
            row.update(
                {
                    "Motivo classificazione finale": self.classification_reason,
                    "Hash SHA-256": self.file_hash_sha256,
                }
            )
        if include_winlive:
            row.update(self._winlive_summary_fields())
        return row

    def _active_evaluated_issues(self) -> list[EvaluatedIssue]:
        if self.repair_outcome == STATUS_UNRECOVERABLE:
            return self.evaluated_issues_after or self.evaluated_issues_before
        return self.evaluated_issues_before or self.evaluated_issues_after

    def to_problem_candidates(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        issues = self._active_evaluated_issues()

        if not issues:
            rows.append(
                {
                    "File": self.file_name,
                    "Percorso": self.file_path,
                    "_Percorso normalizzato": self.normalized_path,
                    "Stato finale file": self.repair_outcome,
                    "Integrita operativa iniziale": self.operational_integrity_before,
                    "Integrita operativa finale": self.operational_integrity_after,
                    "Tipo problema": "Nessuno",
                    "Valutazione": "Nessun problema rilevante",
                    "Impatto ascolto": "Nessuno rilevabile",
                    "Zona": "N/A",
                    "Tempo iniziale": "",
                    "Tempo finale": "",
                    "Precisione temporale": PRECISION_EXACT,
                    "Esito intervento": self.repair_outcome,
                    "Posizione rispetto all'audio significativo": IssuePosition.SIGNIFICANT_AUDIO.value,
                    "Problema ignorato ai fini dello stato": "NO",
                    "Motivo esclusione": "",
                    "Motivo / dettaglio essenziale": self.classification_reason,
                    "RMS segmento (dBFS)": "",
                    "Picco segmento (dBFS)": "",
                    "Distanza da inizio significativo (ms)": "",
                    "Distanza da fine significativo (ms)": "",
                    "Inizio audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_start_ms / 1000.0),
                    "Fine audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_end_ms / 1000.0),
                    "Silenzio iniziale (ms)": self.bounds_before.leading_silence_ms,
                    "Silenzio finale (ms)": self.bounds_before.trailing_silence_ms,
                    "Cartella finale": self.final_folder,
                    "Percorso finale": self.placed_file_path,
                    "Modalità collocazione": self.placement_mode_label,
                    "Operazione effettivamente eseguita": self.placement_effective_operation,
                }
            )
            return rows

        for ev in issues:
            start_ms = ev.segment_start_ms
            end_ms = ev.segment_end_ms if ev.segment_end_ms is not None else start_ms
            if start_ms is None:
                dist_start = ""
                dist_end = ""
            else:
                dist_start = str(max(0, start_ms - self.bounds_before.significant_start_ms))
                dist_end = str(max(0, self.bounds_before.significant_end_ms - start_ms))

            is_ignored = "SI" if ev.ignored_for_classification else "NO"
            problem_type = ev.issue.problem_type
            valuation = "Rilevante"
            if ev.ignored_for_classification:
                problem_type = "Anomalia tecnica in area silenziosa"
                valuation = "Ignorata ai fini della classificazione"

            rows.append(
                {
                    "File": self.file_name,
                    "Percorso": self.file_path,
                    "_Percorso normalizzato": self.normalized_path,
                    "Stato finale file": self.repair_outcome,
                    "Integrita operativa iniziale": self.operational_integrity_before,
                    "Integrita operativa finale": self.operational_integrity_after,
                    "Tipo problema": problem_type,
                    "Valutazione": valuation,
                    "Impatto ascolto": ev.impact_label,
                    "Zona": ev.zone_label,
                    "Tempo iniziale": ev.issue.start,
                    "Tempo finale": ev.issue.end,
                    "Precisione temporale": ev.issue.precision,
                    "Esito intervento": ev.issue.intervention,
                    "Posizione rispetto all'audio significativo": ev.position.value,
                    "Problema ignorato ai fini dello stato": is_ignored,
                    "Motivo esclusione": ev.exclusion_reason,
                    "Motivo / dettaglio essenziale": ev.issue.detail,
                    "RMS segmento (dBFS)": f"{ev.rms_dbfs:.2f}",
                    "Picco segmento (dBFS)": f"{ev.peak_dbfs:.2f}",
                    "Distanza da inizio significativo (ms)": dist_start,
                    "Distanza da fine significativo (ms)": dist_end,
                    "Inizio audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_start_ms / 1000.0),
                    "Fine audio significativo": _format_hhmmss_mmm_from_seconds(self.bounds_before.significant_end_ms / 1000.0),
                    "Silenzio iniziale (ms)": self.bounds_before.leading_silence_ms,
                    "Silenzio finale (ms)": self.bounds_before.trailing_silence_ms,
                    "Cartella finale": self.final_folder,
                    "Percorso finale": self.placed_file_path,
                    "Modalità collocazione": self.placement_mode_label,
                    "Operazione effettivamente eseguita": self.placement_effective_operation,
                }
            )

        return rows

    def to_integrity_record(self) -> dict[str, Any]:
        stable_key = f"{self.normalized_path}|{self.file_size_bytes}|{int(round(self.file_mtime_ts))}"
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "normalized_path": self.normalized_path,
            "stable_key": stable_key,
            "file_size_bytes": self.file_size_bytes,
            "file_mtime_ts": self.file_mtime_ts,
            "integrity_index": self.operational_integrity_after,
            "initial_integrity_index": self.operational_integrity_before,
            "status": self.repair_outcome,
            "final_status": self.final_status,
            "final_folder": self.final_folder,
            "total_errors_before": self.total_errors_before,
            "total_errors_after": self.total_errors_after,
            "repaired": self.repaired,
            "classification_reason": self.classification_reason,
            "hash_sha256": self.file_hash_sha256,
            "ignored_anomalies": self.ignored_anomalies_count,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }


@dataclass(slots=True)
class WinLiveDiagnosticResult:
    verifica_winlive_eseguita: bool = False
    struttura_synct: WinLiveStructureState | None = None
    struttura_chord: WinLiveStructureState | None = None
    testo_presente: bool = False
    accordi_presenti: bool = False
    accordi_non_riconosciuti: int = 0
    accordi_non_riconosciuti_token: list[str] = field(default_factory=list)
    normalizzazione_necessaria: bool = False
    normalizzazione_tentata: bool = False
    normalizzazione_validata: bool = False
    motivo_validazione_post_modifica: str = ""
    synct_presente_prima: bool = False
    synct_presente_dopo: bool = False
    chord_presente_prima: bool = False
    chord_presente_dopo: bool = False
    testo_significativo_uguale: bool | None = None
    chord_invariato: bool | None = None
    terminatore_preservato: bool | None = None
    valore_iniziale_preservato: bool | None = None
    stato_winlive_finale: WinLiveOutcome | None = None
    note_winlive: list[str] = field(default_factory=list)
    errore_winlive: str = ""
    errore_winlive_code: str = ""
    file_temporaneo: str = ""
    audio_hash_preservato: bool | None = None
    metadati_preservati: bool | None = None
    esito_cartella_winlive: str = ""
    esito_percorso_winlive: str = ""
    esito_operazione_winlive: str = ""
    catene_temporali_rilevate: int = 0
    tag_temporali_rimossi: int = 0
    righe_temporali_vuote_rilevate: int = 0
    righe_temporali_vuote_eliminate: int = 0
    valori_righe_temporali_vuote: list[int] = field(default_factory=list)
    allineamenti_temporali: list[dict[str, Any]] = field(default_factory=list)
    metriche_riscrittura: dict[str, Any] = field(default_factory=dict)
    contatori_diagnostici: dict[str, int] = field(default_factory=dict)
    modificato: bool = False
    tempi_fasi_ms: dict[str, float] = field(default_factory=dict)
    fasi_eseguite: dict[str, bool] = field(default_factory=dict)
    tempo_totale_ms: float = 0.0


class MP3DiagnosticsEngine:
    def __init__(self, ffmpeg: FFmpegManager | None = None) -> None:
        self.ffmpeg = ffmpeg or FFmpegManager()

    def run_diagnostics(
        self,
        *,
        input_folder: str,
        include_subfolders: bool,
        output_folder: str,
        repair_mode: bool,
        placement_mode: str = PLACEMENT_MODE_COPY,
        selected_input_files: list[Path] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: Any | None = None,
        verify_mp3_integrity: bool = True,
        verify_winlive: bool = False,
        winlive_autocorrect: bool = True,
    ) -> dict[str, Any]:
        if not verify_mp3_integrity and not verify_winlive:
            raise MP3DiagnosticsError("Almeno un controllo diagnostico deve essere attivo.")

        source_dir = Path(input_folder).expanduser().resolve()
        if selected_input_files is None and not source_dir.is_dir():
            raise MP3DiagnosticsError("Cartella di input non valida.")

        session_timestamp = self._session_timestamp_token()
        base_output = Path(output_folder).expanduser().resolve()
        session_output = self._create_output_session_root(base_output, session_timestamp)
        integrity_root = session_output / OUTPUT_FOLDER_INTEGRITY_ROOT if verify_mp3_integrity else None
        winlive_output_dir = session_output / OUTPUT_FOLDER_WINLIVE if verify_winlive else None
        placement_mode = self._sanitize_placement_mode(placement_mode)
        placement_mode_label = self._placement_mode_label(placement_mode)
        category_dirs: dict[DiagnosticCategory, Path] = {}
        if integrity_root is not None:
            category_dirs = {
                DiagnosticCategory.OK: integrity_root / OUTPUT_FOLDER_OK,
                DiagnosticCategory.REPAIRED: integrity_root / OUTPUT_FOLDER_REPAIRED,
                DiagnosticCategory.UNRECOVERABLE: integrity_root / OUTPUT_FOLDER_UNRECOVERABLE,
            }
        winlive_category_dirs: dict[str, Path] = {}
        if winlive_output_dir is not None:
            winlive_category_dirs = {
                WINLIVE_FOLDER_CONFORME: winlive_output_dir / WINLIVE_FOLDER_CONFORME,
                WINLIVE_FOLDER_NORMALIZZATI: winlive_output_dir / WINLIVE_FOLDER_NORMALIZZATI,
                WINLIVE_FOLDER_SENZA_TAG_TESTO: winlive_output_dir / WINLIVE_FOLDER_SENZA_TAG_TESTO,
                WINLIVE_FOLDER_SENZA_TAG_ACCORDI: winlive_output_dir / WINLIVE_FOLDER_SENZA_TAG_ACCORDI,
                WINLIVE_FOLDER_SENZA_TAG_TESTO_ACCORDI: winlive_output_dir / WINLIVE_FOLDER_SENZA_TAG_TESTO_ACCORDI,
                WINLIVE_FOLDER_ACCORDI_NON_RICONOSCIUTI: winlive_output_dir / WINLIVE_FOLDER_ACCORDI_NON_RICONOSCIUTI,
                WINLIVE_FOLDER_NON_INTEGRO_DOPO_MODIFICA: winlive_output_dir / WINLIVE_FOLDER_NON_INTEGRO_DOPO_MODIFICA,
                WINLIVE_FOLDER_ERRORI_STRUTTURA: winlive_output_dir / WINLIVE_FOLDER_ERRORI_STRUTTURA,
            }
        processed_originals_dir = session_output / OUTPUT_FOLDER_PROCESSED_ORIGINALS
        report_dir = session_output / OUTPUT_FOLDER_REPORT
        temp_dir = session_output / OUTPUT_FOLDER_TEMP

        folders_to_create: list[Path] = [report_dir, temp_dir]
        if verify_mp3_integrity:
            folders_to_create.extend(category_dirs.values())
            folders_to_create.append(processed_originals_dir)
        if verify_winlive and winlive_output_dir is not None:
            folders_to_create.append(winlive_output_dir)
            folders_to_create.extend(winlive_category_dirs.values())

        for folder in folders_to_create:
            folder.mkdir(parents=True, exist_ok=True)

        if verify_mp3_integrity:
            self.ffmpeg.validate()

        if selected_input_files is None:
            files = self._scan_files(
                source_dir=source_dir,
                include_subfolders=include_subfolders,
                excluded_roots=[
                    *self._collect_output_session_roots(base_output),
                    *list(category_dirs.values()),
                    *([processed_originals_dir] if verify_mp3_integrity else []),
                    report_dir,
                    *([winlive_output_dir] if winlive_output_dir is not None else []),
                    temp_dir,
                    *[base_output / name for name in LEGACY_OUTPUT_FOLDERS],
                ],
            )
        else:
            files = self._prepare_selected_input_files(selected_input_files)
            source_dir = self._infer_selected_source_dir(files, fallback=source_dir)

        total = len(files)
        if total == 0:
            if selected_input_files is None:
                raise MP3DiagnosticsError("Nessun file MP3 trovato nella cartella selezionata.")
            raise MP3DiagnosticsError("Nessun file MP3 valido disponibile per la riverifica selettiva.")

        rows: list[MP3DiagnosticResult] = []

        for index, file_path in enumerate(files, start=1):
            self._check_cancel(cancel_event)
            self._notify(progress_callback, index - 1, total, f"Analisi {index}/{total}: {file_path.name}")
            file_start_ts = time.perf_counter()
            phase_times_ms: dict[str, float] = {"enumerazione_file": 0.0}
            phase_executed: dict[str, bool] = {
                "enumerazione_file": True,
                "ffmpeg": False,
                "ffprobe": False,
                "pydub_mutagen_audio": False,
            }

            file_stat = file_path.stat()
            normalized_path = _normalize_key(file_path)
            original_data: bytes | None = None
            if verify_winlive:
                read_start = time.perf_counter()
                original_data = self._read_file_bytes(file_path)
                phase_times_ms["lettura_file"] = (time.perf_counter() - read_start) * 1000.0
                phase_executed["lettura_file"] = True
                file_hash = self._sha256_bytes(original_data)
            else:
                phase_times_ms["lettura_file"] = 0.0
                phase_executed["lettura_file"] = False
                file_hash = self._sha256(file_path)

            if verify_mp3_integrity:
                phase_executed["ffmpeg"] = True
                phase_executed["ffprobe"] = True
                before_analysis = self._analyze_mp3(
                    file_path=file_path,
                    cancel_event=cancel_event,
                    segment_ss=None,
                    segment_t=None,
                )
                duration_sec = self._safe_duration_seconds(file_path)
                before_issues = self._localize_issues_if_needed(
                    file_path=file_path,
                    duration_seconds=duration_sec,
                    issues=before_analysis.issues,
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                    index=index,
                    total=total,
                )
                bounds_before = self.detect_significant_audio_bounds(file_path)
                before_segment_metrics = self._analyze_significant_segment(
                    file_path=file_path,
                    bounds=bounds_before,
                    cancel_event=cancel_event,
                )
                evaluated_before = self._evaluate_issues(
                    file_path=file_path,
                    issues=before_issues,
                    bounds=bounds_before,
                    significant_segment_metrics=before_segment_metrics,
                    duration_seconds=duration_sec,
                )
                relevant_before = [it for it in evaluated_before if not it.ignored_for_classification]
                ignored_before = [it for it in evaluated_before if it.ignored_for_classification]
            else:
                before_analysis = self._build_skipped_analysis(file_path)
                before_issues = []
                evaluated_before = []
                relevant_before = []
                ignored_before = []
                bounds_before = self._build_skipped_audio_bounds()
            winlive_result = WinLiveDiagnosticResult()
            if verify_winlive and original_data is not None:
                winlive_result = self._diagnose_winlive(
                    file_path=file_path,
                    original_data=original_data,
                    repair_mode=repair_mode,
                    winlive_autocorrect=winlive_autocorrect,
                    temp_dir=temp_dir,
                    phase_times_ms=phase_times_ms,
                    phase_executed=phase_executed,
                )

            after_analysis = before_analysis
            after_issues = before_issues
            evaluated_after = evaluated_before
            bounds_after = bounds_before
            repaired = False
            repair_command = ""
            repair_rc = 0
            repair_mode_used = ""
            output_repaired_path = ""
            output_unrecoverable_path = ""
            placed_file_path = ""
            placed_file_kind = ""
            placement_operation = ""
            placement_effective_operation = ""
            original_preserved = False
            preserved_original_path = ""
            file_already_present = False

            final_status = FINAL_HEALTHY
            final_category = DiagnosticCategory.OK
            final_folder = OUTPUT_FOLDER_OK
            final_outcome = STATUS_PERFECT
            if verify_mp3_integrity:
                classification_reason = "Integro: nessun errore rilevante nell'audio significativo"
            else:
                classification_reason = "Controllo integrità MP3 eseguito: No"

            if verify_mp3_integrity and repair_mode and relevant_before:
                repaired_result = self._attempt_repair(source=file_path, temp_dir=temp_dir, cancel_event=cancel_event)
                repair_command = repaired_result.get("command", "")
                repair_rc = int(repaired_result.get("return_code", 0))
                repair_mode_used = str(repaired_result.get("mode", ""))
                temp_output = repaired_result.get("output_path")

                if repaired_result.get("ok") and isinstance(temp_output, str):
                    repaired_candidate = Path(temp_output)
                    repaired = True
                    after_analysis = self._analyze_mp3(
                        file_path=repaired_candidate,
                        cancel_event=cancel_event,
                        segment_ss=None,
                        segment_t=None,
                    )
                    after_issues = self._localize_issues_if_needed(
                        file_path=repaired_candidate,
                        duration_seconds=self._safe_duration_seconds(repaired_candidate),
                        issues=after_analysis.issues,
                        cancel_event=cancel_event,
                        progress_callback=progress_callback,
                        index=index,
                        total=total,
                    )
                    bounds_after = self.detect_significant_audio_bounds(repaired_candidate)
                    after_segment_metrics = self._analyze_significant_segment(
                        file_path=repaired_candidate,
                        bounds=bounds_after,
                        cancel_event=cancel_event,
                    )
                    evaluated_after = self._evaluate_issues(
                        file_path=repaired_candidate,
                        issues=after_issues,
                        bounds=bounds_after,
                        significant_segment_metrics=after_segment_metrics,
                        duration_seconds=self._safe_duration_seconds(repaired_candidate),
                    )
                    relevant_after = [it for it in evaluated_after if not it.ignored_for_classification]

                    if not relevant_after and not self._has_blocking_errors(after_segment_metrics):
                        final_status = FINAL_REPAIRED
                        final_category = DiagnosticCategory.REPAIRED
                        final_folder = OUTPUT_FOLDER_REPAIRED
                        final_outcome = STATUS_REPAIRED
                        classification_reason = "Riparato: problemi rilevanti eliminati nell'audio significativo"
                    else:
                        final_status = FINAL_UNRECOVERABLE
                        final_category = DiagnosticCategory.UNRECOVERABLE
                        final_folder = OUTPUT_FOLDER_UNRECOVERABLE
                        final_outcome = STATUS_UNRECOVERABLE
                        classification_reason = "Non recuperabile: persistono problemi rilevanti nell'audio significativo"

                    placement = self._place_file_for_category(
                        source_dir=source_dir,
                        category_dirs=category_dirs,
                        original_file=file_path,
                        candidate_file=repaired_candidate,
                        category=final_category,
                        include_subfolders=include_subfolders,
                        prefer_candidate=final_category == DiagnosticCategory.REPAIRED,
                        allow_move_candidate=True,
                        placement_mode=placement_mode,
                        originals_safety_dir=processed_originals_dir,
                        preserve_original_in_safety=final_category == DiagnosticCategory.REPAIRED,
                        placed_kind="Output riparato",
                        fallback_kind="Originale non modificato",
                    )
                    placed_file_path = placement["final_path"]
                    placed_file_kind = placement["placed_kind"]
                    placement_operation = placement["operation"]
                    placement_effective_operation = placement["effective_operation"]
                    original_preserved = bool(placement["original_preserved"])
                    preserved_original_path = placement["preserved_original_path"]
                    file_already_present = bool(placement["already_present"])

                    if final_category == DiagnosticCategory.REPAIRED:
                        output_repaired_path = placed_file_path
                    if final_category == DiagnosticCategory.UNRECOVERABLE:
                        output_unrecoverable_path = placed_file_path
                else:
                    final_status = FINAL_UNRECOVERABLE
                    final_category = DiagnosticCategory.UNRECOVERABLE
                    final_folder = OUTPUT_FOLDER_UNRECOVERABLE
                    final_outcome = STATUS_UNRECOVERABLE
                    classification_reason = "Non recuperabile: riparazione non riuscita su problema rilevante"

                    placement = self._place_file_for_category(
                        source_dir=source_dir,
                        category_dirs=category_dirs,
                        original_file=file_path,
                        candidate_file=None,
                        category=final_category,
                        include_subfolders=include_subfolders,
                        prefer_candidate=False,
                        allow_move_candidate=False,
                        placement_mode=placement_mode,
                        originals_safety_dir=processed_originals_dir,
                        preserve_original_in_safety=False,
                        placed_kind="Originale non modificato",
                        fallback_kind="Originale non modificato",
                    )
                    placed_file_path = placement["final_path"]
                    placed_file_kind = placement["placed_kind"]
                    placement_operation = placement["operation"]
                    placement_effective_operation = placement["effective_operation"]
                    original_preserved = bool(placement["original_preserved"])
                    preserved_original_path = placement["preserved_original_path"]
                    file_already_present = bool(placement["already_present"])
                    output_unrecoverable_path = placed_file_path

            elif verify_mp3_integrity and relevant_before:
                final_status = FINAL_UNRECOVERABLE
                final_category = DiagnosticCategory.UNRECOVERABLE
                final_folder = OUTPUT_FOLDER_UNRECOVERABLE
                final_outcome = STATUS_UNRECOVERABLE
                classification_reason = "Non recuperabile: rilevati problemi nell'audio significativo"
            elif verify_mp3_integrity:
                final_status = FINAL_HEALTHY
                final_category = DiagnosticCategory.OK
                final_folder = OUTPUT_FOLDER_OK
                final_outcome = STATUS_PERFECT
                if ignored_before:
                    classification_reason = (
                        f"Integro: {len(ignored_before)} anomalie tecniche rilevate solo in aree silenziose"
                    )

            if verify_mp3_integrity and not placed_file_path:
                placement = self._place_file_for_category(
                    source_dir=source_dir,
                    category_dirs=category_dirs,
                    original_file=file_path,
                    candidate_file=None,
                    category=final_category,
                    include_subfolders=include_subfolders,
                    prefer_candidate=False,
                    allow_move_candidate=False,
                    placement_mode=placement_mode,
                    originals_safety_dir=processed_originals_dir,
                    preserve_original_in_safety=False,
                    placed_kind="Originale non modificato",
                    fallback_kind="Originale non modificato",
                )
                placed_file_path = placement["final_path"]
                placed_file_kind = placement["placed_kind"]
                placement_operation = placement["operation"]
                placement_effective_operation = placement["effective_operation"]
                original_preserved = bool(placement["original_preserved"])
                preserved_original_path = placement["preserved_original_path"]
                file_already_present = bool(placement["already_present"])

            if verify_winlive:
                copy_start = time.perf_counter()
                winlive_folder_name = self._winlive_folder_from_outcome(winlive_result.stato_winlive_finale)
                winlive_root = winlive_category_dirs.get(winlive_folder_name)
                if winlive_root is not None:
                    winlive_source = file_path
                    if (
                        winlive_result.stato_winlive_finale == WinLiveOutcome.FILE_NORMALIZED
                        and winlive_result.file_temporaneo
                    ):
                        candidate = Path(winlive_result.file_temporaneo)
                        if candidate.exists():
                            winlive_source = candidate

                    winlive_placement = self._place_file_in_folder(
                        source_dir=source_dir,
                        destination_root=winlive_root,
                        original_file=file_path,
                        source_to_place=winlive_source,
                        include_subfolders=include_subfolders,
                    )
                    winlive_result.esito_cartella_winlive = winlive_folder_name
                    winlive_result.esito_percorso_winlive = winlive_placement["final_path"]
                    winlive_result.esito_operazione_winlive = winlive_placement["operation"]
                    winlive_result.modificato = winlive_source != file_path
                phase_times_ms["copia_file"] = (time.perf_counter() - copy_start) * 1000.0
                phase_executed["copia_file"] = winlive_root is not None
            else:
                phase_times_ms["copia_file"] = 0.0
                phase_executed["copia_file"] = False

            total_ms = (time.perf_counter() - file_start_ts) * 1000.0
            winlive_result.tempi_fasi_ms.update(phase_times_ms)
            winlive_result.fasi_eseguite.update(phase_executed)
            winlive_result.tempo_totale_ms = total_ms

            if final_outcome == STATUS_REPAIRED:
                for ev in evaluated_before:
                    if not ev.ignored_for_classification:
                        ev.issue.intervention = "Riparato"
            elif final_outcome == STATUS_UNRECOVERABLE:
                for ev in evaluated_after:
                    if not ev.ignored_for_classification:
                        ev.issue.intervention = "Non recuperato"

            result = MP3DiagnosticResult(
                file_name=file_path.name,
                file_path=str(file_path),
                normalized_path=normalized_path,
                file_size_bytes=int(file_stat.st_size),
                file_mtime_ts=float(file_stat.st_mtime),
                file_hash_sha256=file_hash,
                operational_integrity_before=(self._operational_integrity(evaluated_before) if verify_mp3_integrity else 0),
                operational_integrity_after=(self._operational_integrity(evaluated_after) if verify_mp3_integrity else 0),
                total_errors_before=(before_analysis.total_errors if verify_mp3_integrity else 0),
                total_errors_after=(after_analysis.total_errors if verify_mp3_integrity else 0),
                repaired=repaired,
                repair_outcome=(final_outcome if verify_mp3_integrity else ""),
                final_status=(final_status if verify_mp3_integrity else ""),
                final_category=final_category,
                final_folder=(final_folder if verify_mp3_integrity else ""),
                classification_reason=classification_reason,
                blocking_residual=(self._has_blocking_errors(after_analysis.metrics) if verify_mp3_integrity else False),
                bounds_before=bounds_before,
                bounds_after=bounds_after,
                evaluated_issues_before=evaluated_before,
                evaluated_issues_after=evaluated_after,
                ignored_anomalies_count=(len([it for it in evaluated_before if it.ignored_for_classification]) if verify_mp3_integrity else 0),
                output_repaired_path=output_repaired_path,
                output_unrecoverable_path=output_unrecoverable_path,
                analysis_command=(before_analysis.command_text if verify_mp3_integrity else ""),
                analysis_return_code=(before_analysis.return_code if verify_mp3_integrity else 0),
                repair_command=repair_command,
                repair_return_code=repair_rc,
                repair_mode_used=repair_mode_used,
                raw_decode_log_before=before_analysis.decode_log,
                raw_decode_log_after=after_analysis.decode_log,
                raw_repair_log=str(repaired_result.get("error", "")) if repair_mode and relevant_before else "",
                placed_file_path=placed_file_path,
                placed_file_kind=placed_file_kind,
                placement_operation=placement_operation,
                placement_mode_label=placement_mode_label,
                original_preserved=original_preserved,
                preserved_original_path=preserved_original_path,
                placement_effective_operation=placement_effective_operation or placement_operation,
                file_already_present=file_already_present,
                winlive=winlive_result,
            )
            rows.append(result)

        self._notify(progress_callback, total, total, "Generazione report...")
        report_paths = self._write_reports(rows, report_dir, session_timestamp, include_integrity=verify_mp3_integrity)
        summary = self._build_summary(
            rows,
            session_output,
            repair_mode,
            placement_mode,
            verify_mp3_integrity=verify_mp3_integrity,
            verify_winlive=verify_winlive,
        )
        self._write_log(report_dir / "Log.txt", rows)
        if verify_mp3_integrity:
            self._write_integrity_index(report_dir / "IntegrityIndex.json", rows)
        report_paths["timing"] = self._write_phase_timing_report(report_dir / "TimingFasi.csv", rows)
        self._cleanup_temp_dir(temp_dir)
        self._notify(progress_callback, total, total, "Diagnostica completata.")

        return {
            "summary": summary,
            "report_paths": report_paths,
            "diagnostic_results": rows,
        }

    def _diagnose_winlive(
        self,
        *,
        file_path: Path,
        original_data: bytes,
        repair_mode: bool,
        winlive_autocorrect: bool,
        temp_dir: Path,
        phase_times_ms: dict[str, float],
        phase_executed: dict[str, bool],
    ) -> WinLiveDiagnosticResult:
        result = WinLiveDiagnosticResult(verifica_winlive_eseguita=True)

        try:
            parse_start = time.perf_counter()
            parsed = parse_winlive_blocks_strict(original_data)
            phase_times_ms["ricerca_blocchi_wl5"] = (time.perf_counter() - parse_start) * 1000.0
            phase_executed["ricerca_blocchi_wl5"] = True
            result.struttura_synct = parsed.synct.state
            result.struttura_chord = parsed.chord.state
            result.synct_presente_prima = parsed.synct.state == WinLiveStructureState.VALID
            result.chord_presente_prima = parsed.chord.state == WinLiveStructureState.VALID

            if parsed.anomalies:
                result.note_winlive.extend(anomaly.message for anomaly in parsed.anomalies)

            synct_text: str | None = None
            chord_text: str | None = None
            synct_normalization = None

            if parsed.synct.state == WinLiveStructureState.VALID:
                synct_start = time.perf_counter()
                decoded_synct = decode_text_lossless(parsed.synct.content_bytes or b"")
                if decoded_synct.text is None:
                    result.errore_winlive_code = "DECODING_FAILED"
                    result.errore_winlive = decoded_synct.report.error or "Decodifica SYNCT fallita"
                    result.stato_winlive_finale = WinLiveOutcome.STRUCTURE_ERROR
                    return result
                synct_text = decoded_synct.text
                phase_times_ms["parsing_wl5synct"] = (time.perf_counter() - synct_start) * 1000.0
                phase_executed["parsing_wl5synct"] = True

                normalize_start = time.perf_counter()
                synct_normalization = normalize_synct_content(synct_text)
                phase_times_ms["normalizzazione"] = (time.perf_counter() - normalize_start) * 1000.0
                phase_executed["normalizzazione"] = True
                result.testo_presente = contains_semantic_text(synct_text)
                result.normalizzazione_necessaria = synct_normalization.changed
                result.catene_temporali_rilevate = int(synct_normalization.counters.adjacent_time_chains_reduced)
                result.tag_temporali_rimossi = int(synct_normalization.counters.adjacent_time_tags_removed)
                result.righe_temporali_vuote_rilevate = int(synct_normalization.counters.empty_timed_lines_detected)
                result.righe_temporali_vuote_eliminate = int(synct_normalization.counters.empty_timed_lines_removed)
                result.valori_righe_temporali_vuote = list(synct_normalization.empty_timed_line_values)
                result.allineamenti_temporali = list(synct_normalization.alignment_events)
                result.note_winlive.extend(synct_normalization.notes)
            else:
                phase_times_ms["parsing_wl5synct"] = 0.0
                phase_executed["parsing_wl5synct"] = False
                phase_times_ms["normalizzazione"] = 0.0
                phase_executed["normalizzazione"] = False

            if parsed.chord.state == WinLiveStructureState.VALID:
                chord_start = time.perf_counter()
                decoded_chord = decode_text_lossless(parsed.chord.content_bytes or b"")
                if decoded_chord.text is None:
                    result.errore_winlive_code = "DECODING_FAILED"
                    result.errore_winlive = decoded_chord.report.error or "Decodifica CHORD fallita"
                    result.stato_winlive_finale = WinLiveOutcome.STRUCTURE_ERROR
                    return result
                chord_text = decoded_chord.text
                phase_times_ms["parsing_wl5chord"] = (time.perf_counter() - chord_start) * 1000.0
                phase_executed["parsing_wl5chord"] = True
                result.accordi_presenti = True
                result.accordi_non_riconosciuti = count_unrecognized_chords(chord_text)
                result.accordi_non_riconosciuti_token = self._collect_unrecognized_chord_tokens(chord_text)
            else:
                phase_times_ms["parsing_wl5chord"] = 0.0
                phase_executed["parsing_wl5chord"] = False

            text_valid = parsed.synct.state == WinLiveStructureState.VALID and bool(
                synct_normalization and synct_normalization.text_semantically_valid
            )
            chord_valid = parsed.chord.state == WinLiveStructureState.VALID and chord_text is not None
            structure_valid = (
                parsed.synct.state != WinLiveStructureState.INVALID_STRUCTURE
                and parsed.chord.state != WinLiveStructureState.INVALID_STRUCTURE
                and not parsed.anomalies
            )

            if not structure_valid:
                result.errore_winlive_code = result.errore_winlive_code or "STRUCTURE_ERROR"
                result.errore_winlive = result.errore_winlive or "Struttura WinLive non valida o ambigua"

            post_validation_status = PostNormalizationValidationStatus.NOT_NECESSARY
            should_attempt_normalization = (
                repair_mode
                and winlive_autocorrect
                and structure_valid
                and text_valid
                and chord_valid
                and result.normalizzazione_necessaria
                and result.accordi_non_riconosciuti == 0
            )

            if should_attempt_normalization:
                result.normalizzazione_tentata = True
                write_result = write_normalized_winlive_copy(
                    source_path=str(file_path),
                    temp_dir=str(temp_dir),
                    keep_temporary_on_failure=False,
                )
                phase_times_ms["scrittura_temporanea"] = float(
                    write_result.phase_times_ms.get("scrittura_temporaneo_totale_ms", 0.0)
                )
                phase_executed["scrittura_temporanea"] = True
                phase_times_ms["validazione_post_scrittura"] = float(
                    write_result.phase_times_ms.get("validazione_totale_ms", 0.0)
                )
                phase_executed["validazione_post_scrittura"] = True
                self._merge_winlive_write_result(result, write_result)
                for phase_name, phase_value in write_result.phase_times_ms.items():
                    phase_times_ms[phase_name] = float(phase_value)
                    phase_executed[phase_name] = float(phase_value) > 0.0
                post_validation_status = (
                    PostNormalizationValidationStatus.OK
                    if write_result.error_code is None and write_result.write_succeeded and write_result.readback_succeeded
                    else PostNormalizationValidationStatus.FAILED
                )
                result.modificato = bool(result.file_temporaneo)
            else:
                phase_times_ms["scrittura_temporanea"] = 0.0
                phase_executed["scrittura_temporanea"] = False
                phase_times_ms["validazione_post_scrittura"] = 0.0
                phase_executed["validazione_post_scrittura"] = False

            classify_start = time.perf_counter()
            classification = classify_winlive(
                WinLiveClassificationInput(
                    text_valid=text_valid,
                    chord_valid=chord_valid,
                    chord_unrecognized_count=result.accordi_non_riconosciuti,
                    text_was_modified=result.modificato,
                    post_validation_status=post_validation_status,
                    structure_valid=structure_valid,
                    synct_present=parsed.synct.state == WinLiveStructureState.VALID,
                    chord_present=parsed.chord.state == WinLiveStructureState.VALID,
                    normalization_required=bool(result.normalizzazione_necessaria),
                    normalization_attempted=result.normalizzazione_tentata,
                )
            )
            phase_times_ms["classificazione"] = (time.perf_counter() - classify_start) * 1000.0
            phase_executed["classificazione"] = True
            result.stato_winlive_finale = classification.outcome
            result.note_winlive.append(classification.reason)
            if post_validation_status == PostNormalizationValidationStatus.OK:
                result.normalizzazione_validata = True
                result.synct_presente_dopo = True
                result.chord_presente_dopo = True
                result.testo_significativo_uguale = True
                result.chord_invariato = True
                result.terminatore_preservato = True
                result.valore_iniziale_preservato = True
                result.motivo_validazione_post_modifica = "OK"
            elif result.normalizzazione_tentata:
                result.motivo_validazione_post_modifica = result.errore_winlive or result.errore_winlive_code or "FALLITA"

            return result
        except Exception as exc:
            result.errore_winlive_code = type(exc).__name__
            result.errore_winlive = f"{type(exc).__name__}: {exc}"
            result.note_winlive.append(traceback.format_exc().strip())
            result.stato_winlive_finale = WinLiveOutcome.STRUCTURE_ERROR
            return result

    @staticmethod
    def _collect_unrecognized_chord_tokens(chord_text: str) -> list[str]:
        tokens = [token.strip() for token in chord_text.split("|")]
        return [token for token in tokens if token and "?" in token]

    @staticmethod
    def _merge_winlive_write_result(
        result: WinLiveDiagnosticResult,
        write_result: WinLiveWriteValidationResult,
    ) -> None:
        result.file_temporaneo = write_result.temporary_path or ""
        result.audio_hash_preservato = write_result.audio_identical
        result.metadati_preservati = write_result.metadata_preserved
        result.metriche_riscrittura = dict(write_result.rewrite_metrics)
        result.contatori_diagnostici = dict(write_result.diagnostic_counters)
        result.normalizzazione_validata = write_result.error_code is None and write_result.write_succeeded and write_result.readback_succeeded
        result.note_winlive.extend(write_result.notes)
        if write_result.error_code is not None:
            result.errore_winlive_code = write_result.error_code.value
            result.errore_winlive = write_result.error or write_result.error_code.value
            result.synct_presente_dopo = False
            result.chord_presente_dopo = False
            result.testo_significativo_uguale = False
            result.chord_invariato = False
            result.terminatore_preservato = False
            result.valore_iniziale_preservato = False
            result.motivo_validazione_post_modifica = result.errore_winlive

    def detect_significant_audio_bounds(self, file_path: Path) -> AudioBounds:
        duration_ms = max(0, int(round(self._safe_duration_seconds(file_path) * 1000.0)))
        if duration_ms <= 0:
            return AudioBounds(
                file_duration_ms=0,
                significant_start_ms=0,
                significant_end_ms=0,
                leading_silence_ms=0,
                trailing_silence_ms=0,
                detection_confidence=0.0,
                threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
                threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
            )

        windows = self._extract_audio_windows(file_path=file_path, duration_ms=duration_ms)
        return self._detect_significant_bounds_from_windows(windows=windows, duration_ms=duration_ms)

    def _extract_audio_windows(self, *, file_path: Path, duration_ms: int) -> list[AudioWindowStats]:
        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            str(file_path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ]
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                creationflags=self.ffmpeg._creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            return []

        pcm = process.stdout or b""
        if len(pcm) < 2:
            return []

        samples = array.array("h")
        usable = pcm[: len(pcm) - (len(pcm) % 2)]
        samples.frombytes(usable)
        if not samples:
            return []

        sample_rate = 8000
        samples_per_window = max(1, int(round((AUDIO_WINDOW_MS / 1000.0) * sample_rate)))
        windows: list[AudioWindowStats] = []
        cursor = 0
        idx = 0
        while cursor < len(samples):
            chunk = samples[cursor : cursor + samples_per_window]
            if not chunk:
                break
            start_ms = idx * AUDIO_WINDOW_MS
            end_ms = min(duration_ms, start_ms + AUDIO_WINDOW_MS)
            peak = max(abs(v) for v in chunk)
            energy = sum(float(v) * float(v) for v in chunk) / float(len(chunk))
            rms = math.sqrt(max(energy, 1.0))
            rms_dbfs = 20.0 * math.log10(max(rms / 32768.0, 1e-6))
            peak_dbfs = 20.0 * math.log10(max(peak / 32768.0, 1e-6))
            windows.append(
                AudioWindowStats(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    rms_dbfs=rms_dbfs,
                    peak_dbfs=peak_dbfs,
                )
            )
            cursor += samples_per_window
            idx += 1

        return windows

    def _detect_significant_bounds_from_windows(self, *, windows: list[AudioWindowStats], duration_ms: int) -> AudioBounds:
        if not windows:
            return AudioBounds(
                file_duration_ms=duration_ms,
                significant_start_ms=0,
                significant_end_ms=duration_ms,
                leading_silence_ms=0,
                trailing_silence_ms=0,
                detection_confidence=0.0,
                threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
                threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
            )

        min_significant_windows = max(1, int(math.ceil(MIN_SIGNIFICANT_AUDIO_DURATION_MS / AUDIO_WINDOW_MS)))
        min_silence_windows = max(1, int(math.ceil(MIN_SILENCE_RUN_MS / AUDIO_WINDOW_MS)))

        audible = [
            (w.rms_dbfs > SILENCE_RMS_THRESHOLD_DB) or (w.peak_dbfs > SILENCE_PEAK_THRESHOLD_DB)
            for w in windows
        ]

        start_idx = 0
        stable_count = 0
        found_start = False
        for i, is_audible in enumerate(audible):
            stable_count = stable_count + 1 if is_audible else 0
            if stable_count >= min_significant_windows:
                start_idx = i - stable_count + 1
                found_start = True
                break

        if not found_start:
            start_idx = 0

        end_idx = len(windows) - 1
        stable_count = 0
        found_end = False
        for i in range(len(audible) - 1, -1, -1):
            is_audible = audible[i]
            stable_count = stable_count + 1 if is_audible else 0
            if stable_count >= min_significant_windows:
                end_idx = i + stable_count - 1
                if end_idx >= len(windows):
                    end_idx = len(windows) - 1
                found_end = True
                break

        if not found_end:
            end_idx = len(windows) - 1

        detected_start_ms = windows[start_idx].start_ms
        detected_end_ms = windows[end_idx].end_ms

        effective_start = max(0, detected_start_ms - AUDIO_BOUNDS_SAFETY_MARGIN_MS)
        effective_end = min(duration_ms, detected_end_ms + AUDIO_BOUNDS_SAFETY_MARGIN_MS)
        if effective_end <= effective_start:
            effective_start = 0
            effective_end = duration_ms

        leading = max(0, effective_start)
        trailing = max(0, duration_ms - effective_end)
        coverage = max(1, duration_ms)
        confidence = max(0.0, min(1.0, 1.0 - ((leading + trailing) / float(coverage))))

        return AudioBounds(
            file_duration_ms=duration_ms,
            significant_start_ms=effective_start,
            significant_end_ms=effective_end,
            leading_silence_ms=leading,
            trailing_silence_ms=trailing,
            detection_confidence=confidence,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )

    def _analyze_significant_segment(
        self,
        *,
        file_path: Path,
        bounds: AudioBounds,
        cancel_event: Any | None,
    ) -> dict[str, int]:
        start_sec = bounds.significant_start_ms / 1000.0
        length_sec = max(0.1, (bounds.significant_end_ms - bounds.significant_start_ms) / 1000.0)
        analysis = self._analyze_mp3(
            file_path=file_path,
            cancel_event=cancel_event,
            segment_ss=start_sec,
            segment_t=length_sec,
        )
        return analysis.metrics

    def _evaluate_issues(
        self,
        *,
        file_path: Path,
        issues: list[DiagnosticIssue],
        bounds: AudioBounds,
        significant_segment_metrics: dict[str, int],
        duration_seconds: float,
    ) -> list[EvaluatedIssue]:
        evaluated: list[EvaluatedIssue] = []
        significant_has_blocking = self._has_blocking_errors(significant_segment_metrics)

        for issue in issues:
            start_ms, end_ms = self._issue_segment_ms(issue)
            position = self._issue_position(bounds=bounds, start_ms=start_ms, end_ms=end_ms)
            stats = self._measure_issue_audio_stats(
                file_path=file_path,
                issue=issue,
                duration_seconds=duration_seconds,
            )
            is_silent_segment = (
                stats["rms_dbfs"] <= SILENCE_RMS_THRESHOLD_DB
                and stats["peak_dbfs"] <= SILENCE_PEAK_THRESHOLD_DB
            )

            ignored = False
            exclusion_reason = ""
            zone = "Audio significativo"
            impact_label = "Da verificare"

            if position == IssuePosition.LEADING_SILENCE:
                zone = "Silenzio iniziale"
            elif position == IssuePosition.TRAILING_SILENCE:
                zone = "Silenzio finale"
            elif position == IssuePosition.BOUNDARY_OVERLAP:
                zone = "Sovrapposizione bordo audio"
            elif position == IssuePosition.UNKNOWN:
                zone = "Posizione non determinabile"

            if position in (IssuePosition.LEADING_SILENCE, IssuePosition.TRAILING_SILENCE):
                if is_silent_segment and not significant_has_blocking:
                    ignored = True
                    exclusion_reason = "Confinato in area silenziosa, senza impatto sull'audio significativo"
                    impact_label = "Nessuno rilevabile"
                elif not is_silent_segment:
                    exclusion_reason = "Area non sufficientemente silenziosa"
                    impact_label = "Potenziale impatto"
                elif significant_has_blocking:
                    exclusion_reason = "Decodifica area significativa non pulita"
                    impact_label = "Potenziale impatto"
            elif position == IssuePosition.BOUNDARY_OVERLAP:
                exclusion_reason = "Attraversa il confine dell'audio significativo"
                impact_label = "Potenziale impatto"
            elif position == IssuePosition.SIGNIFICANT_AUDIO:
                exclusion_reason = "Nell'audio significativo"
                impact_label = "Rilevante"
            else:
                exclusion_reason = "Posizione non determinabile"
                impact_label = "Potenziale impatto"

            evaluated.append(
                EvaluatedIssue(
                    issue=issue,
                    position=position,
                    ignored_for_classification=ignored,
                    exclusion_reason=exclusion_reason,
                    zone_label=zone,
                    rms_dbfs=float(stats["rms_dbfs"]),
                    peak_dbfs=float(stats["peak_dbfs"]),
                    impact_label=impact_label,
                    segment_start_ms=start_ms,
                    segment_end_ms=end_ms,
                )
            )

        return evaluated

    def _issue_segment_ms(self, issue: DiagnosticIssue) -> tuple[int | None, int | None]:
        start_sec = self._parse_issue_time(issue.start)
        end_sec = self._parse_issue_time(issue.end)
        if start_sec is None:
            return None, None
        start_ms = int(round(start_sec * 1000.0))
        if end_sec is None:
            end_ms = int(round(start_sec * 1000.0 + self._precision_to_window_seconds(issue.precision) * 1000.0))
        else:
            end_ms = int(round(end_sec * 1000.0))
        if end_ms < start_ms:
            end_ms = start_ms
        return start_ms, end_ms

    def _issue_position(self, *, bounds: AudioBounds, start_ms: int | None, end_ms: int | None) -> IssuePosition:
        if start_ms is None:
            return IssuePosition.UNKNOWN

        actual_end = end_ms if end_ms is not None else start_ms
        sig_start = bounds.significant_start_ms
        sig_end = bounds.significant_end_ms

        if actual_end <= sig_start:
            return IssuePosition.LEADING_SILENCE
        if start_ms >= sig_end:
            return IssuePosition.TRAILING_SILENCE

        overlaps_start = start_ms < sig_start < actual_end
        overlaps_end = start_ms < sig_end < actual_end
        if overlaps_start or overlaps_end:
            return IssuePosition.BOUNDARY_OVERLAP

        if start_ms >= sig_start and actual_end <= sig_end:
            return IssuePosition.SIGNIFICANT_AUDIO

        return IssuePosition.UNKNOWN

    @staticmethod
    def _operational_integrity(evaluated_issues: list[EvaluatedIssue]) -> int:
        # Operational integrity only penalizes relevant issues.
        relevant = [it for it in evaluated_issues if not it.ignored_for_classification]
        if not relevant:
            return 100
        # Linear penalty with hard floor.
        return max(0, 100 - len(relevant) * 20)

    def _scan_files(
        self,
        *,
        source_dir: Path,
        include_subfolders: bool,
        excluded_roots: list[Path],
    ) -> list[Path]:
        pattern = "**/*.mp3" if include_subfolders else "*.mp3"
        files: list[Path] = []
        for path in source_dir.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if any(_is_relative_to(resolved, root.resolve()) for root in excluded_roots):
                continue
            files.append(resolved)

        files.sort(key=lambda p: p.as_posix().lower())
        return files

    def _prepare_selected_input_files(self, selected_input_files: list[Path]) -> list[Path]:
        normalized: list[Path] = []
        seen: set[str] = set()

        for item in selected_input_files:
            path = Path(item).expanduser().resolve()
            key = _normalize_key(path)
            if key in seen:
                continue
            seen.add(key)

            if not path.is_file():
                continue
            if path.suffix.lower() != ".mp3":
                continue

            normalized.append(path)

        normalized.sort(key=lambda p: p.as_posix().lower())
        return normalized

    @staticmethod
    def _session_timestamp_token() -> str:
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def _session_folder_name(self, timestamp_token: str) -> str:
        return f"{OUTPUT_SESSION_PREFIX}{timestamp_token}"

    def _create_output_session_root(self, base_output: Path, timestamp_token: str) -> Path:
        base_output.mkdir(parents=True, exist_ok=True)
        session_name = self._session_folder_name(timestamp_token)
        session_root = base_output / session_name
        if not session_root.exists():
            return session_root

        counter = 2
        while True:
            candidate = base_output / f"{session_name}_{counter}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _collect_output_session_roots(base_output: Path) -> list[Path]:
        if not base_output.exists():
            return []

        roots: list[Path] = []
        for item in base_output.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith(OUTPUT_SESSION_PREFIX):
                roots.append(item)
        return roots

    @staticmethod
    def _infer_selected_source_dir(files: list[Path], fallback: Path) -> Path:
        if fallback.is_dir():
            return fallback
        if not files:
            return Path.cwd()

        parent_paths = [str(file_path.parent) for file_path in files]
        try:
            common_parent = Path(os.path.commonpath(parent_paths)).resolve()
            if common_parent.is_dir():
                return common_parent
        except Exception:
            pass

        return files[0].parent.resolve()

    @staticmethod
    def _sanitize_placement_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized == PLACEMENT_MODE_MOVE:
            return PLACEMENT_MODE_MOVE
        return PLACEMENT_MODE_COPY

    @staticmethod
    def _placement_mode_label(mode: str) -> str:
        return "Spostamento" if mode == PLACEMENT_MODE_MOVE else "Copia"

    @staticmethod
    def _notify(callback: ProgressCallback | None, current: int, total: int, message: str) -> None:
        if callback is not None:
            callback(current, total, message)

    @staticmethod
    def _check_cancel(cancel_event: Any | None) -> None:
        if cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set():
            raise MP3DiagnosticsCancelled("Diagnostica MP3 interrotta dall'utente.")

    def _safe_duration_seconds(self, file_path: Path) -> float:
        try:
            return float(self.ffmpeg.get_duration(file_path))
        except Exception:
            return 0.0

    @staticmethod
    def _read_file_bytes(file_path: Path) -> bytes:
        return file_path.read_bytes()

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _sha256(self, file_path: Path, block_size: int = 1024 * 1024) -> str:
        hasher = hashlib.sha256()
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(block_size)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _analyze_mp3(
        self,
        *,
        file_path: Path,
        cancel_event: Any | None,
        segment_ss: float | None,
        segment_t: float | None,
    ) -> AnalysisResult:
        self._check_cancel(cancel_event)
        command = self._build_decode_command(file_path=file_path, segment_ss=segment_ss, segment_t=segment_t)
        result = self._run_command(command)
        issues, metrics = self._parse_decode_log(result["log"])
        total_errors = sum(metrics.values())
        integrity = self._calculate_integrity_index(metrics)

        return AnalysisResult(
            command=command,
            command_text=self._command_to_text(command),
            return_code=int(result["return_code"]),
            decode_log=result["log"],
            issues=issues,
            metrics=metrics,
            integrity_index=integrity,
            total_errors=total_errors,
        )

    def _build_decode_command(
        self,
        *,
        file_path: Path,
        segment_ss: float | None,
        segment_t: float | None,
    ) -> list[str]:
        null_target = "NUL" if os.name == "nt" else "/dev/null"
        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-nostats",
            "-v",
            "warning",
        ]
        if segment_ss is not None and segment_ss > 0:
            command.extend(["-ss", f"{segment_ss:.3f}"])
        command.extend(["-i", str(file_path)])
        if segment_t is not None and segment_t > 0:
            command.extend(["-t", f"{segment_t:.3f}"])
        command.extend(["-map", "0:a:0", "-f", "null", null_target])
        return command

    def _run_command(self, command: list[str]) -> dict[str, Any]:
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self.ffmpeg._creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {
                "return_code": 1,
                "log": str(error),
            }

        log = ((process.stderr or "") + "\n" + (process.stdout or "")).strip()
        return {
            "return_code": int(process.returncode),
            "log": log,
        }

    def _parse_decode_log(self, decode_log: str) -> tuple[list[DiagnosticIssue], dict[str, int]]:
        metrics = {key: 0 for key in _ERROR_PATTERNS.keys()}
        issues: list[DiagnosticIssue] = []
        seen_keys: set[tuple[str, str, str, str]] = set()

        if not decode_log:
            return issues, metrics

        for raw_line in decode_log.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line_lower = line.lower()

            matched_key = ""
            for key, (_label, pattern) in _ERROR_PATTERNS.items():
                if re.search(pattern, line_lower, flags=re.IGNORECASE):
                    matched_key = key
                    break

            if not matched_key:
                continue

            label = _ERROR_PATTERNS[matched_key][0]
            start, end, precision = self._extract_time_from_line(line)
            dedup_key = (matched_key, start, end, line_lower)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            metrics[matched_key] += 1
            issues.append(
                DiagnosticIssue(
                    problem_key=matched_key,
                    problem_type=label,
                    start=start,
                    end=end,
                    precision=precision,
                    intervention="Rilevato",
                    detail=line,
                )
            )

        issues.sort(key=self._issue_sort_key)
        return issues, metrics

    @staticmethod
    def _issue_sort_key(issue: DiagnosticIssue) -> tuple[int, str, str]:
        if issue.start == "Tempo non determinabile":
            return (2_147_483_647, issue.problem_key, issue.detail)
        try:
            parts = issue.start.split(":")
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            ms = int(seconds * 1000)
        except Exception:
            ms = 2_147_483_647
        return (ms, issue.problem_key, issue.detail)

    def _extract_time_from_line(self, line: str) -> tuple[str, str, str]:
        time_match = re.search(r"time\s*=\s*(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)", line, flags=re.IGNORECASE)
        if time_match:
            ts = self._normalize_hhmmss(time_match.group(1))
            return ts, ts, PRECISION_EXACT

        pts_match = re.search(
            r"(?:pkt_pts_time|best_effort_timestamp_time|timestamp|pts)\s*[=:]\s*(\d+(?:\.\d+)?)",
            line,
            flags=re.IGNORECASE,
        )
        if pts_match:
            sec = float(pts_match.group(1))
            ts = _format_hhmmss_mmm_from_seconds(sec)
            return ts, ts, PRECISION_EXACT

        hh_match = re.search(r"(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)", line)
        if hh_match:
            ts = self._normalize_hhmmss(hh_match.group(1))
            return ts, ts, PRECISION_EXACT

        return "Tempo non determinabile", "", PRECISION_UNKNOWN

    @staticmethod
    def _normalize_hhmmss(value: str) -> str:
        raw = value.strip()
        if "." not in raw:
            return raw + ".000"
        head, frac = raw.split(".", 1)
        frac = (frac + "000")[:3]
        return f"{head}.{frac}"

    def _localize_issues_if_needed(
        self,
        *,
        file_path: Path,
        duration_seconds: float,
        issues: list[DiagnosticIssue],
        cancel_event: Any | None,
        progress_callback: ProgressCallback | None,
        index: int,
        total: int,
    ) -> list[DiagnosticIssue]:
        missing_types = sorted({issue.problem_key for issue in issues if issue.precision == PRECISION_UNKNOWN})
        if not missing_types:
            return issues
        if duration_seconds <= 0.0:
            return issues

        self._notify(
            progress_callback,
            index - 1,
            total,
            f"Analisi segmentata {index}/{total}: {file_path.name}",
        )

        localized_map: dict[str, tuple[str, str, str]] = {}
        for problem_key in missing_types:
            self._check_cancel(cancel_event)
            interval_10 = self._find_problem_interval(
                file_path=file_path,
                duration_seconds=duration_seconds,
                problem_key=problem_key,
                window_seconds=10.0,
                cancel_event=cancel_event,
            )
            if interval_10 is None:
                continue

            interval_2 = self._refine_interval(
                file_path=file_path,
                base_interval=interval_10,
                problem_key=problem_key,
                window_seconds=2.0,
                cancel_event=cancel_event,
            )
            interval_05 = self._refine_interval(
                file_path=file_path,
                base_interval=interval_2,
                problem_key=problem_key,
                window_seconds=0.5,
                cancel_event=cancel_event,
            )

            final_interval = interval_05 or interval_2 or interval_10
            if final_interval is None:
                continue

            start_sec, end_sec = final_interval
            precision = self._precision_for_window(end_sec - start_sec)
            localized_map[problem_key] = (
                _format_hhmmss_mmm_from_seconds(start_sec),
                _format_hhmmss_mmm_from_seconds(end_sec),
                precision,
            )

        localized_issues: list[DiagnosticIssue] = []
        for issue in issues:
            if issue.precision != PRECISION_UNKNOWN:
                localized_issues.append(issue)
                continue
            replacement = localized_map.get(issue.problem_key)
            if replacement is None:
                localized_issues.append(issue)
                continue
            localized_issues.append(
                DiagnosticIssue(
                    problem_key=issue.problem_key,
                    problem_type=issue.problem_type,
                    start=replacement[0],
                    end=replacement[1],
                    precision=replacement[2],
                    intervention=issue.intervention,
                    detail=issue.detail,
                )
            )

        localized_issues.sort(key=self._issue_sort_key)
        return localized_issues

    def _find_problem_interval(
        self,
        *,
        file_path: Path,
        duration_seconds: float,
        problem_key: str,
        window_seconds: float,
        cancel_event: Any | None,
    ) -> tuple[float, float] | None:
        start = 0.0
        while start < duration_seconds:
            self._check_cancel(cancel_event)
            end = min(duration_seconds, start + window_seconds)
            if self._segment_has_problem(file_path, start, end - start, problem_key, cancel_event):
                return (start, end)
            start += window_seconds
        return None

    def _refine_interval(
        self,
        *,
        file_path: Path,
        base_interval: tuple[float, float] | None,
        problem_key: str,
        window_seconds: float,
        cancel_event: Any | None,
    ) -> tuple[float, float] | None:
        if base_interval is None:
            return None
        start, end = base_interval
        cursor = start
        while cursor < end:
            self._check_cancel(cancel_event)
            candidate_end = min(end, cursor + window_seconds)
            if self._segment_has_problem(file_path, cursor, candidate_end - cursor, problem_key, cancel_event):
                return (cursor, candidate_end)
            cursor += window_seconds
        return base_interval

    def _segment_has_problem(
        self,
        file_path: Path,
        start_seconds: float,
        length_seconds: float,
        problem_key: str,
        cancel_event: Any | None,
    ) -> bool:
        analysis = self._analyze_mp3(
            file_path=file_path,
            cancel_event=cancel_event,
            segment_ss=start_seconds,
            segment_t=max(0.1, length_seconds),
        )
        return any(issue.problem_key == problem_key for issue in analysis.issues)

    @staticmethod
    def _folder_name_for_category(category: DiagnosticCategory) -> str:
        mapping = {
            DiagnosticCategory.OK: OUTPUT_FOLDER_OK,
            DiagnosticCategory.REPAIRED: OUTPUT_FOLDER_REPAIRED,
            DiagnosticCategory.UNRECOVERABLE: OUTPUT_FOLDER_UNRECOVERABLE,
        }
        return mapping[category]

    @staticmethod
    def _winlive_folder_from_outcome(outcome: WinLiveOutcome | None) -> str:
        if outcome == WinLiveOutcome.FILE_ALREADY_OK:
            return WINLIVE_FOLDER_CONFORME
        if outcome == WinLiveOutcome.FILE_NORMALIZED:
            return WINLIVE_FOLDER_NORMALIZZATI
        if outcome == WinLiveOutcome.REQUIRES_NORMALIZATION:
            return WINLIVE_FOLDER_CONFORME
        if outcome == WinLiveOutcome.MISSING_TEXT_ONLY:
            return WINLIVE_FOLDER_SENZA_TAG_TESTO
        if outcome == WinLiveOutcome.MISSING_CHORDS_ONLY:
            return WINLIVE_FOLDER_SENZA_TAG_ACCORDI
        if outcome == WinLiveOutcome.MISSING_TEXT_AND_CHORDS:
            return WINLIVE_FOLDER_SENZA_TAG_TESTO_ACCORDI
        if outcome in (WinLiveOutcome.UNRECOGNIZED_CHORDS, WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS):
            return WINLIVE_FOLDER_ACCORDI_NON_RICONOSCIUTI
        if outcome == WinLiveOutcome.MODIFICATION_NOT_INTEGRAL:
            return WINLIVE_FOLDER_NON_INTEGRO_DOPO_MODIFICA
        if outcome == WinLiveOutcome.STRUCTURE_ERROR:
            return WINLIVE_FOLDER_ERRORI_STRUTTURA
        return WINLIVE_FOLDER_ERRORI_STRUTTURA

    @staticmethod
    def _zero_metrics() -> dict[str, int]:
        return {
            "header_missing": 0,
            "corrupted_frames": 0,
            "crc_errors": 0,
            "sync_errors": 0,
            "undecodable_frames": 0,
            "invalid_data": 0,
            "xing_issues": 0,
            "vbr_issues": 0,
            "id3_issues": 0,
        }

    def _build_skipped_analysis(self, file_path: Path) -> AnalysisResult:
        return AnalysisResult(
            command=[],
            command_text=f"Verifica integrità MP3 disattivata per {file_path.name}",
            return_code=0,
            decode_log="Verifica integrità MP3 non eseguita.",
            issues=[],
            metrics=self._zero_metrics(),
            integrity_index=100,
            total_errors=0,
        )

    def _build_skipped_audio_bounds(self) -> AudioBounds:
        return AudioBounds(
            file_duration_ms=0,
            significant_start_ms=0,
            significant_end_ms=0,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=0.0,
            threshold_rms_db=SILENCE_RMS_THRESHOLD_DB,
            threshold_peak_db=SILENCE_PEAK_THRESHOLD_DB,
        )

    def _place_file_in_folder(
        self,
        *,
        source_dir: Path,
        destination_root: Path,
        original_file: Path,
        source_to_place: Path,
        include_subfolders: bool,
    ) -> dict[str, str]:
        target = self._build_category_target(
            source_dir=source_dir,
            category_root=destination_root,
            original_file=original_file,
            include_subfolders=include_subfolders,
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and self._same_file_content(source_to_place, target):
            operation = "File già presente nella destinazione"
            if source_to_place != original_file:
                self._safe_remove(source_to_place)
            return {"final_path": str(target), "operation": operation}

        if target.exists():
            target = self._next_available_target(target)

        self._transfer_file(source=source_to_place, destination=target, move=False)
        if source_to_place != original_file:
            self._safe_remove(source_to_place)
            operation = "Copiato output WinLive normalizzato"
        else:
            operation = "Copiato originale"
        return {"final_path": str(target), "operation": operation}

    def _place_file_for_category(
        self,
        *,
        source_dir: Path,
        category_dirs: dict[DiagnosticCategory, Path],
        original_file: Path,
        candidate_file: Path | None,
        category: DiagnosticCategory,
        include_subfolders: bool,
        prefer_candidate: bool,
        allow_move_candidate: bool,
        placement_mode: str,
        originals_safety_dir: Path | None,
        preserve_original_in_safety: bool,
        placed_kind: str,
        fallback_kind: str,
    ) -> dict[str, Any]:
        source_to_place = candidate_file if prefer_candidate and candidate_file is not None and candidate_file.exists() else original_file
        effective_kind = placed_kind if source_to_place is candidate_file else fallback_kind
        effective_mode = self._sanitize_placement_mode(placement_mode)

        original_preserved = False
        preserved_original_path = ""
        preserve_note = ""
        if preserve_original_in_safety and originals_safety_dir is not None:
            safety_target = self._build_category_target(
                source_dir=source_dir,
                category_root=originals_safety_dir,
                original_file=original_file,
                include_subfolders=include_subfolders,
            )
            safety_target.parent.mkdir(parents=True, exist_ok=True)
            if safety_target.exists() and self._same_file_content(original_file, safety_target):
                original_preserved = True
                preserved_original_path = str(safety_target)
                preserve_note = "Originale già presente in sicurezza"
            else:
                if safety_target.exists():
                    safety_target = self._next_available_target(safety_target)
                self._transfer_file(source=original_file, destination=safety_target, move=False)
                original_preserved = True
                preserved_original_path = str(safety_target)
                preserve_note = "Originale conservato in sicurezza"
        elif effective_mode == PLACEMENT_MODE_COPY:
            original_preserved = original_file.exists()
            preserved_original_path = str(original_file)

        category_root = category_dirs[category]
        final_target = self._build_category_target(
            source_dir=source_dir,
            category_root=category_root,
            original_file=original_file,
            include_subfolders=include_subfolders,
        )
        final_target.parent.mkdir(parents=True, exist_ok=True)

        if final_target.exists():
            same_content = self._same_file_content(source_to_place, final_target)
            if same_content:
                if source_to_place is candidate_file and source_to_place is not None and source_to_place.exists():
                    self._safe_remove(source_to_place)
                return {
                    "final_path": str(final_target),
                    "placed_kind": effective_kind,
                    "operation": "File già presente nella destinazione",
                    "effective_operation": "File già presente nella destinazione",
                    "original_preserved": original_preserved,
                    "preserved_original_path": preserved_original_path,
                    "already_present": True,
                }
            final_target = self._next_available_target(final_target)

        move_original = effective_mode == PLACEMENT_MODE_MOVE and source_to_place == original_file
        move_candidate = (
            effective_mode == PLACEMENT_MODE_MOVE
            and source_to_place is candidate_file
            and allow_move_candidate
            and source_to_place is not None
        )

        if move_original:
            self._transfer_file(source=source_to_place, destination=final_target, move=True)
            operation = "Spostato originale"
        elif move_candidate:
            self._transfer_file(source=source_to_place, destination=final_target, move=True)
            operation = "Spostato output generato"
        else:
            self._transfer_file(source=source_to_place, destination=final_target, move=False)
            operation = "Copiato originale" if source_to_place == original_file else "Copiato output generato"

        effective_operation = operation
        if preserve_note:
            effective_operation = f"{operation}; {preserve_note}"

        return {
            "final_path": str(final_target),
            "placed_kind": effective_kind,
            "operation": operation,
            "effective_operation": effective_operation,
            "original_preserved": original_preserved,
            "preserved_original_path": preserved_original_path,
            "already_present": False,
        }

    @staticmethod
    def _transfer_file(*, source: Path, destination: Path, move: bool) -> None:
        if move:
            shutil.move(str(source), str(destination))
            return
        shutil.copy2(source, destination)

    def _build_category_target(
        self,
        *,
        source_dir: Path,
        category_root: Path,
        original_file: Path,
        include_subfolders: bool,
    ) -> Path:
        if include_subfolders:
            try:
                relative = original_file.resolve().relative_to(source_dir.resolve())
            except ValueError:
                relative = Path(original_file.name)
            return category_root / relative
        return category_root / original_file.name

    def _same_file_content(self, source: Path, destination: Path) -> bool:
        try:
            source_stat = source.stat()
            dest_stat = destination.stat()
        except OSError:
            return False

        if int(source_stat.st_size) != int(dest_stat.st_size):
            return False

        return self._sha256(source) == self._sha256(destination)

    @staticmethod
    def _next_available_target(path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem} ({counter}){suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _measure_issue_audio_stats(
        self,
        *,
        file_path: Path,
        issue: DiagnosticIssue,
        duration_seconds: float,
    ) -> dict[str, float]:
        start_seconds = self._parse_issue_time(issue.start)
        end_seconds = self._parse_issue_time(issue.end)

        if start_seconds is None:
            start_seconds = max(0.0, duration_seconds - 1.0) if duration_seconds > 0 else 0.0
        if end_seconds is None or end_seconds <= start_seconds:
            fallback_window = self._precision_to_window_seconds(issue.precision)
            end_seconds = start_seconds + fallback_window

        if duration_seconds > 0:
            end_seconds = min(duration_seconds, end_seconds)
        segment_duration = max(0.1, end_seconds - start_seconds)

        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(file_path),
            "-t",
            f"{segment_duration:.3f}",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ]

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                creationflags=self.ffmpeg._creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            return {
                "rms_dbfs": -60.0,
                "peak_dbfs": -60.0,
                "segment_duration": segment_duration,
            }

        pcm = process.stdout or b""
        if len(pcm) < 2:
            return {
                "rms_dbfs": -60.0,
                "peak_dbfs": -60.0,
                "segment_duration": segment_duration,
            }

        samples = array.array("h")
        usable = pcm[: len(pcm) - (len(pcm) % 2)]
        samples.frombytes(usable)
        if not samples:
            return {
                "rms_dbfs": -60.0,
                "peak_dbfs": -60.0,
                "segment_duration": segment_duration,
            }

        peak = max(abs(value) for value in samples)
        energy = sum(float(value) * float(value) for value in samples) / float(len(samples))
        rms = math.sqrt(max(energy, 1.0))
        return {
            "rms_dbfs": 20.0 * math.log10(max(rms / 32768.0, 1e-6)),
            "peak_dbfs": 20.0 * math.log10(max(peak / 32768.0, 1e-6)),
            "segment_duration": segment_duration,
        }

    @staticmethod
    def _parse_issue_time(value: str) -> float | None:
        text = (value or "").strip()
        if not text or text == "Tempo non determinabile":
            return None
        try:
            hours_text, minutes_text, seconds_text = text.split(":")
            return int(hours_text) * 3600.0 + int(minutes_text) * 60.0 + float(seconds_text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _precision_to_window_seconds(precision: str) -> float:
        mapping = {
            PRECISION_EXACT: 0.25,
            PRECISION_500MS: 0.5,
            PRECISION_2S: 2.0,
            PRECISION_10S: 10.0,
            PRECISION_UNKNOWN: 1.0,
        }
        return mapping.get(precision, 1.0)

    @staticmethod
    def _precision_for_window(window_seconds: float) -> str:
        if window_seconds <= 0.001:
            return PRECISION_EXACT
        if window_seconds <= 0.5 + 1e-9:
            return PRECISION_500MS
        if window_seconds <= 2.0 + 1e-9:
            return PRECISION_2S
        if window_seconds <= 10.0 + 1e-9:
            return PRECISION_10S
        return PRECISION_UNKNOWN

    def _attempt_repair(self, *, source: Path, temp_dir: Path, cancel_event: Any | None) -> dict[str, Any]:
        self._check_cancel(cancel_event)

        temp_dir.mkdir(parents=True, exist_ok=True)
        stream_dest = temp_dir / f"{source.stem}_{uuid.uuid4().hex}_stream{source.suffix}"
        stream_result = self._repair_stream_copy(source, stream_dest)
        if stream_result.get("ok"):
            return stream_result

        self._safe_remove(stream_dest)

        self._check_cancel(cancel_event)
        reencode_dest = temp_dir / f"{source.stem}_{uuid.uuid4().hex}_reenc{source.suffix}"
        reencode_result = self._repair_safe_reencode(source, reencode_dest)
        if reencode_result.get("ok"):
            return reencode_result

        self._safe_remove(reencode_dest)

        return {
            "ok": False,
            "output_path": None,
            "command": str(reencode_result.get("command", "")),
            "return_code": int(reencode_result.get("return_code", 1)),
            "mode": str(reencode_result.get("mode", "")),
            "error": str(reencode_result.get("error", stream_result.get("error", "file di output non creato"))),
        }

    def _repair_stream_copy(self, source: Path, destination: Path) -> dict[str, Any]:
        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-nostats",
            "-y",
            "-v",
            "warning",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            str(destination),
        ]
        return self._run_repair_command(command, destination, "stream-copy")

    def _repair_safe_reencode(self, source: Path, destination: Path) -> dict[str, Any]:
        command = [
            str(self.ffmpeg.ffmpeg_path),
            "-hide_banner",
            "-nostats",
            "-y",
            "-v",
            "warning",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(destination),
        ]
        return self._run_repair_command(command, destination, "safe-reencode")

    def _run_repair_command(self, command: list[str], destination: Path, mode: str) -> dict[str, Any]:
        result = self._run_command(command)
        ok = result["return_code"] == 0 and destination.exists() and destination.stat().st_size > 0
        if not ok:
            self._safe_remove(destination)

        return {
            "ok": ok,
            "output_path": str(destination) if ok else None,
            "command": self._command_to_text(command),
            "return_code": int(result["return_code"]),
            "mode": mode,
            "error": "" if ok else (result["log"] or "file di output non creato"),
        }

    @staticmethod
    def _safe_remove(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _calculate_integrity_index(self, metrics: dict[str, int]) -> int:
        penalties = {
            "header_missing": 18,
            "corrupted_frames": 7,
            "crc_errors": 3,
            "sync_errors": 10,
            "undecodable_frames": 8,
            "invalid_data": 10,
            "xing_issues": 2,
            "vbr_issues": 2,
            "id3_issues": 1,
        }

        score = 100
        for key, penalty in penalties.items():
            score -= _safe_int(metrics.get(key), 0) * penalty
        return max(0, min(100, score))

    def _has_blocking_errors(self, metrics: dict[str, int]) -> bool:
        return any(_safe_int(metrics.get(field), 0) > 0 for field in _BLOCKING_ERROR_FIELDS)

    def _write_reports(
        self,
        rows: list[MP3DiagnosticResult],
        report_dir: Path,
        session_timestamp: str,
        *,
        include_integrity: bool,
    ) -> dict[str, str]:
        include_winlive = any(row.winlive.verifica_winlive_eseguita for row in rows)
        summary_rows = [
            row.to_summary_row(i + 1, include_winlive=include_winlive, include_integrity=include_integrity)
            for i, row in enumerate(rows)
        ]
        problem_rows: list[dict[str, Any]] = []
        if include_integrity:
            problem_rows.extend(self._build_grouped_problem_rows(rows))
        if include_winlive:
            problem_rows.extend(self._build_winlive_problem_rows(rows, start_number=len(problem_rows) + 1))
        winlive_rows = self._build_winlive_rows(rows) if include_winlive else []

        summary_csv = report_dir / f"report_diagnostica_{session_timestamp}.csv"
        problems_csv = report_dir / "Dettaglio_Problemi.csv"
        html_path = report_dir / "report_diagnostica.html"
        xlsx_path = report_dir / f"report_diagnostica_{session_timestamp}.xlsx"

        self._write_csv(summary_csv, summary_rows)
        self._write_csv(problems_csv, problem_rows)
        self._write_html(html_path, summary_rows, problem_rows, winlive_rows)
        self._write_xlsx(xlsx_path, summary_rows, problem_rows)

        return {
            "csv": str(problems_csv),
            "csv_summary": str(summary_csv),
            "csv_problems": str(problems_csv),
            "xlsx": str(xlsx_path),
            "html": str(html_path),
            "log": str(report_dir / "Log.txt"),
            "integrity_index": str(report_dir / "IntegrityIndex.json") if include_integrity else "",
        }

    def _write_phase_timing_report(self, file_path: Path, rows: list[MP3DiagnosticResult]) -> str:
        phases = [
            "enumerazione_file",
            "lettura_file",
            "ricerca_blocchi_wl5",
            "parsing_wl5synct",
            "parsing_wl5chord",
            "normalizzazione",
            "costruzione_nuovi_bytes",
            "scrittura_temporanea",
            "validazione_post_scrittura",
            "safe_write_totale_ms",
            "validazione_totale_ms",
            "tempo_non_attribuito_ms",
            "apertura_temporaneo",
            "scrittura_bytes_temporaneo",
            "flush_temporaneo",
            "fsync_temporaneo",
            "chiusura_temporaneo",
            "attesa_disponibilita_temporaneo",
            "scrittura_temporaneo",
            "scrittura_temporaneo_totale_ms",
            "rilettura_temporaneo",
            "ricerca_blocchi_temporaneo",
            "validazione_strict_struttura",
            "idempotenza_seconda_normalizzazione",
            "confronto_prefisso_suffisso_metadati",
            "classificazione",
            "copia_file",
            "ffmpeg",
            "ffprobe",
            "pydub_mutagen_audio",
            "totale_file",
        ]
        timing_rows: list[dict[str, Any]] = []
        for row in rows:
            times = row.winlive.tempi_fasi_ms if row.winlive.tempi_fasi_ms else {}
            executed = row.winlive.fasi_eseguite if row.winlive.fasi_eseguite else {}
            for phase in phases:
                duration_ms = float(row.winlive.tempo_totale_ms) if phase == "totale_file" else float(times.get(phase, 0.0))
                timing_rows.append(
                    {
                        "File": row.file_name,
                        "FASE": phase,
                        "DURATA MS": f"{duration_ms:.3f}",
                        "ESEGUITA SI/NO": "SI" if bool(executed.get(phase, duration_ms > 0.0)) else "NO",
                    }
                )
        self._write_csv(file_path, timing_rows)
        return str(file_path)

    def _build_grouped_problem_rows(self, rows: list[MP3DiagnosticResult]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidates.extend(row.to_problem_candidates())

        grouped: dict[tuple[str, str, str, str, str, str, str], dict[str, Any]] = {}
        for item in candidates:
            key = (
                str(item.get("_Percorso normalizzato", "")),
                str(item.get("Tempo iniziale", "")),
                str(item.get("Tempo finale", "")),
                str(item.get("Precisione temporale", "")),
                str(item.get("Stato finale file", "")),
                str(item.get("Esito intervento", "")),
                str(item.get("Cartella finale", "")),
            )

            if key not in grouped:
                grouped[key] = {
                    "File": item.get("File", ""),
                    "Percorso": item.get("Percorso", ""),
                    "_Percorso normalizzato": item.get("_Percorso normalizzato", ""),
                    "Stato finale file": item.get("Stato finale file", ""),
                    "Integrita operativa iniziale": item.get("Integrita operativa iniziale", ""),
                    "Integrita operativa finale": item.get("Integrita operativa finale", ""),
                    "Tempo iniziale": item.get("Tempo iniziale", ""),
                    "Tempo finale": item.get("Tempo finale", ""),
                    "Precisione temporale": item.get("Precisione temporale", ""),
                    "Esito intervento": item.get("Esito intervento", ""),
                    "Valutazione": item.get("Valutazione", ""),
                    "Impatto ascolto": item.get("Impatto ascolto", ""),
                    "Zona": item.get("Zona", ""),
                    "Posizione rispetto all'audio significativo": item.get("Posizione rispetto all'audio significativo", ""),
                    "Problema ignorato ai fini dello stato": item.get("Problema ignorato ai fini dello stato", ""),
                    "Motivo esclusione": item.get("Motivo esclusione", ""),
                    "RMS segmento (dBFS)": item.get("RMS segmento (dBFS)", ""),
                    "Picco segmento (dBFS)": item.get("Picco segmento (dBFS)", ""),
                    "Distanza da inizio significativo (ms)": item.get("Distanza da inizio significativo (ms)", ""),
                    "Distanza da fine significativo (ms)": item.get("Distanza da fine significativo (ms)", ""),
                    "Inizio audio significativo": item.get("Inizio audio significativo", ""),
                    "Fine audio significativo": item.get("Fine audio significativo", ""),
                    "Silenzio iniziale (ms)": item.get("Silenzio iniziale (ms)", ""),
                    "Silenzio finale (ms)": item.get("Silenzio finale (ms)", ""),
                    "Cartella finale": item.get("Cartella finale", ""),
                    "Percorso finale": item.get("Percorso finale", ""),
                    "Modalità collocazione": item.get("Modalità collocazione", ""),
                    "Operazione effettivamente eseguita": item.get("Operazione effettivamente eseguita", ""),
                    "_tipi": [],
                    "_dettagli": [],
                    "_tipi_seen": set(),
                    "_dettagli_seen": set(),
                }

            bucket = grouped[key]
            problem_type = str(item.get("Tipo problema", "")).strip()
            if problem_type and problem_type not in bucket["_tipi_seen"]:
                bucket["_tipi_seen"].add(problem_type)
                bucket["_tipi"].append(problem_type)

            detail = self._normalize_report_detail(str(item.get("Motivo / dettaglio essenziale", "")).strip())
            if detail and detail not in bucket["_dettagli_seen"]:
                bucket["_dettagli_seen"].add(detail)
                bucket["_dettagli"].append(detail)

        merged_rows: list[dict[str, Any]] = []
        number = 1
        for bucket in grouped.values():
            ordered_types = self._order_problem_types(bucket["_tipi"])
            merged_rows.append(
                {
                    "Numero": number,
                    "File": bucket["File"],
                    "Percorso": bucket["Percorso"],
                    "Stato finale file": bucket["Stato finale file"],
                    "Integrita operativa iniziale": bucket["Integrita operativa iniziale"],
                    "Integrita operativa finale": bucket["Integrita operativa finale"],
                    "Tipo problema": " / ".join(ordered_types) if ordered_types else "Nessuno",
                    "Valutazione": bucket["Valutazione"],
                    "Impatto ascolto": bucket["Impatto ascolto"],
                    "Zona": bucket["Zona"],
                    "Tempo iniziale": bucket["Tempo iniziale"],
                    "Tempo finale": bucket["Tempo finale"],
                    "Precisione temporale": bucket["Precisione temporale"],
                    "Esito intervento": bucket["Esito intervento"],
                    "Posizione rispetto all'audio significativo": bucket["Posizione rispetto all'audio significativo"],
                    "Problema ignorato ai fini dello stato": bucket["Problema ignorato ai fini dello stato"],
                    "Motivo esclusione": bucket["Motivo esclusione"],
                    "Motivo / dettaglio essenziale": " / ".join(bucket["_dettagli"]),
                    "RMS segmento (dBFS)": bucket["RMS segmento (dBFS)"],
                    "Picco segmento (dBFS)": bucket["Picco segmento (dBFS)"],
                    "Distanza da inizio significativo (ms)": bucket["Distanza da inizio significativo (ms)"],
                    "Distanza da fine significativo (ms)": bucket["Distanza da fine significativo (ms)"],
                    "Inizio audio significativo": bucket["Inizio audio significativo"],
                    "Fine audio significativo": bucket["Fine audio significativo"],
                    "Silenzio iniziale (ms)": bucket["Silenzio iniziale (ms)"],
                    "Silenzio finale (ms)": bucket["Silenzio finale (ms)"],
                    "Cartella finale": bucket["Cartella finale"],
                    "Percorso finale": bucket.get("Percorso finale", ""),
                    "Modalità collocazione": bucket.get("Modalità collocazione", ""),
                    "Operazione effettivamente eseguita": bucket.get("Operazione effettivamente eseguita", ""),
                }
            )
            number += 1

        return merged_rows

    def _build_winlive_problem_rows(self, rows: list[MP3DiagnosticResult], start_number: int = 1) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        number = start_number

        for row in rows:
            if not row.winlive.verifica_winlive_eseguita:
                continue

            if row.winlive.righe_temporali_vuote_eliminate > 0:
                out.append(
                    {
                        "Numero": number,
                        "File": row.file_name,
                        "Percorso originale": row.file_path,
                        "Tipo controllo": "WinLive",
                        "Fase": "normalizzazione",
                        "Categoria": "RIGA_SOLO_TEMPORALE",
                        "Codice": "EMPTY_TIMED_LINE_REMOVED",
                        "Descrizione": "Rimosse righe composte solo da timestamp nel blocco WL5SYNCT.",
                        "Timestamp iniziale ms": "",
                        "Timestamp finale ms": "",
                        "Tempo iniziale formattato": "",
                        "Tempo finale formattato": "",
                        "Offset byte iniziale": "",
                        "Offset byte finale": "",
                        "Riga logica": "",
                        "Riga successiva": "",
                        "Valore originale": ",".join(str(v) for v in row.winlive.valori_righe_temporali_vuote),
                        "Valore collegato": "",
                        "Valore risultante": "RIGA ELIMINATA",
                        "Frammento originale": "|<ms>|",
                        "Frammento risultante": "",
                        "Azione": "Rimozione riga solo temporale",
                        "Validazione": "OK" if row.winlive.normalizzazione_validata else row.winlive.motivo_validazione_post_modifica,
                        "Impatto uditivo": "Nessuno atteso",
                        "Decisione finale": row.winlive.stato_winlive_finale.value if row.winlive.stato_winlive_finale else "",
                        "Note": MP3DiagnosticResult._join_notes(row.winlive.note_winlive),
                    }
                )
                number += 1

            for event in row.winlive.allineamenti_temporali:
                out.append(
                    {
                        "Numero": number,
                        "File": row.file_name,
                        "Percorso originale": row.file_path,
                        "Tipo controllo": "WinLive",
                        "Fase": "allineamento_righe",
                        "Categoria": "ALLINEAMENTO_TIMESTAMP",
                        "Codice": "ROW_LINK_ALIGNMENT",
                        "Descrizione": "Allineato timestamp finale della riga corrente al timestamp iniziale della riga successiva.",
                        "Timestamp iniziale ms": str(event.get("original_final_ms", "")),
                        "Timestamp finale ms": str(event.get("linked_initial_ms", "")),
                        "Tempo iniziale formattato": "",
                        "Tempo finale formattato": "",
                        "Offset byte iniziale": str(event.get("offset", "")),
                        "Offset byte finale": "",
                        "Riga logica": str(event.get("line_current", "")),
                        "Riga successiva": str(event.get("line_next", "")),
                        "Valore originale": str(event.get("original_final_ms", "")),
                        "Valore collegato": str(event.get("linked_initial_ms", "")),
                        "Valore risultante": str(event.get("result_final_ms", "")),
                        "Frammento originale": str(event.get("fragment_before", "")),
                        "Frammento risultante": str(event.get("fragment_after", "")),
                        "Azione": "Allineamento marker di collegamento",
                        "Validazione": "OK" if row.winlive.normalizzazione_validata else row.winlive.motivo_validazione_post_modifica,
                        "Impatto uditivo": "Migliora sincronizzazione testo",
                        "Decisione finale": row.winlive.stato_winlive_finale.value if row.winlive.stato_winlive_finale else "",
                        "Note": "",
                    }
                )
                number += 1

            if row.winlive.errore_winlive_code or row.winlive.errore_winlive:
                metrics = row.winlive.metriche_riscrittura
                out.append(
                    {
                        "Numero": number,
                        "File": row.file_name,
                        "Percorso originale": row.file_path,
                        "Tipo controllo": "WinLive",
                        "Fase": "validazione_post_modifica",
                        "Categoria": "ERRORE_WINLIVE",
                        "Codice": row.winlive.errore_winlive_code,
                        "Descrizione": row.winlive.errore_winlive,
                        "Timestamp iniziale ms": "",
                        "Timestamp finale ms": "",
                        "Tempo iniziale formattato": "",
                        "Tempo finale formattato": "",
                        "Offset byte iniziale": str(metrics.get("original_synct_start", "")),
                        "Offset byte finale": str(metrics.get("original_synct_end", "")),
                        "Riga logica": "",
                        "Riga successiva": "",
                        "Valore originale": str(metrics.get("original_synct_block_len", "")),
                        "Valore collegato": str(metrics.get("new_synct_block_len", "")),
                        "Valore risultante": str(metrics.get("delta_len", "")),
                        "Frammento originale": "",
                        "Frammento risultante": "",
                        "Azione": "Promozione a Normalizzato bloccata",
                        "Validazione": row.winlive.motivo_validazione_post_modifica,
                        "Impatto uditivo": "N/D",
                        "Decisione finale": row.winlive.stato_winlive_finale.value if row.winlive.stato_winlive_finale else "",
                        "Note": MP3DiagnosticResult._join_notes(row.winlive.note_winlive),
                    }
                )
                number += 1

        return out

    @staticmethod
    def _order_problem_types(types: list[str]) -> list[str]:
        priority = {
            "Anomalia tecnica in area silenziosa": 1,
            "Header mancante": 2,
            "Errore di sincronizzazione": 3,
            "Frame MP3 non decodificabile": 4,
            "Dati invalidi": 5,
        }
        ordered = list(types)
        ordered.sort(key=lambda value: (priority.get(value, 1000), types.index(value)))
        return ordered

    @staticmethod
    def _normalize_report_detail(detail: str) -> str:
        if not detail:
            return ""

        text = detail.strip()
        text = re.sub(r"\[[^\]]*@\s*[0-9a-fA-FxX]+\]\s*", "", text)
        text = re.sub(r"@\s*[0-9a-fA-F]{8,}", "", text)
        text = re.sub(r"\s+", " ", text).strip(" -:\t")
        return text

    def _write_csv(self, file_path: Path, rows: list[dict[str, Any]]) -> None:
        headers = self._collect_headers(rows)
        with file_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    @staticmethod
    def _collect_headers(rows: list[dict[str, Any]]) -> list[str]:
        headers: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                if key.startswith("_") or key in seen:
                    continue
                headers.append(key)
                seen.add(key)
        return headers

    def _build_winlive_rows(self, rows: list[MP3DiagnosticResult]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for row in rows:
            if not row.winlive.verifica_winlive_eseguita:
                continue
            status = row.winlive.stato_winlive_finale.value if row.winlive.stato_winlive_finale is not None else ""
            note_text = MP3DiagnosticResult._join_notes(row.winlive.note_winlive)
            out.append(
                {
                    "File": row.file_name,
                    "Stato MP3": row.repair_outcome,
                    "Stato WinLive": status,
                    "Presenza WL5SYNCT": "SI" if row.winlive.synct_presente_prima else "NO",
                    "Presenza WL5CHORD": "SI" if row.winlive.chord_presente_prima else "NO",
                    "Testo presente": "SI" if row.winlive.testo_presente else "NO",
                    "Accordi presenti": "SI" if row.winlive.accordi_presenti else "NO",
                    "Accordi non riconosciuti": str(row.winlive.accordi_non_riconosciuti),
                    "Token accordi non riconosciuti": ", ".join(row.winlive.accordi_non_riconosciuti_token),
                    "Normalizzazione richiesta": "SI" if row.winlive.normalizzazione_necessaria else "NO",
                    "Normalizzazione tentata": "SI" if row.winlive.normalizzazione_tentata else "NO",
                    "Normalizzazione validata": "SI" if row.winlive.normalizzazione_validata else "NO",
                    "Motivo validazione post-modifica": row.winlive.motivo_validazione_post_modifica,
                    "Modificato": "SI" if row.winlive.modificato else "NO",
                    "Catene temporali rilevate": str(row.winlive.catene_temporali_rilevate),
                    "Tag temporali rimossi": str(row.winlive.tag_temporali_rimossi),
                    "Righe solo temporali rilevate": str(row.winlive.righe_temporali_vuote_rilevate),
                    "Righe solo temporali eliminate": str(row.winlive.righe_temporali_vuote_eliminate),
                    "Cartella esito WinLive": row.winlive.esito_cartella_winlive,
                    "Percorso esito WinLive": row.winlive.esito_percorso_winlive,
                    "Operazione esito WinLive": row.winlive.esito_operazione_winlive,
                    "Codice errore WinLive": row.winlive.errore_winlive_code,
                    "Errore WinLive": row.winlive.errore_winlive,
                    "_note_winlive": f"{row.file_name}: {note_text}" if note_text else "",
                }
            )
        return out

    def _write_html(
        self,
        file_path: Path,
        summary_rows: list[dict[str, Any]],
        problem_rows: list[dict[str, Any]],
        winlive_rows: list[dict[str, str]],
    ) -> None:
        summary_headers = list(summary_rows[0].keys()) if summary_rows else []
        problem_headers = list(problem_rows[0].keys()) if problem_rows else []
        winlive_headers = [key for key in winlive_rows[0].keys() if not key.startswith("_")] if winlive_rows else []

        lines = [
            "<!doctype html>",
            "<html lang='it'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <title>Report Diagnostica MP3</title>",
            "  <style>",
            "    body { font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #1f2937; }",
            "    h1 { margin: 0 0 8px 0; font-size: 22px; }",
            "    h2 { margin-top: 20px; }",
            "    p { margin: 0 0 16px 0; color: #4b5563; }",
            "    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 16px; }",
            "    th, td { border: 1px solid #d1d5db; padding: 6px; vertical-align: top; white-space: pre-wrap; }",
            "    th { background: #f3f4f6; text-align: left; }",
            "    tr.group-start td { border-top: 3px solid #93c5fd; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <h1>Diagnostica e Riparazione MP3</h1>",
            f"  <p>Generato: {datetime.now().isoformat(timespec='seconds')}</p>",
            "  <h2>Riepilogo File</h2>",
        ]

        lines.extend(self._html_table(summary_headers, summary_rows))
        lines.append("  <h2>Dettaglio Problemi</h2>")
        lines.extend(self._html_problem_table(problem_headers, problem_rows))

        if winlive_rows:
            lines.append("  <h2>Sezione WinLive</h2>")
            lines.extend(self._html_table(winlive_headers, winlive_rows))

            note_items = [str(item.get("_note_winlive", "")).strip() for item in winlive_rows]
            note_items = [item for item in note_items if item]

            if note_items:
                lines.append("  <h3>Note WinLive</h3>")
                lines.append("  <ul>")
                for item in note_items:
                    lines.append(f"    <li>{_xml_escape(item)}</li>")
                lines.append("  </ul>")

        lines.extend(["</body>", "</html>"])
        file_path.write_text("\n".join(lines), encoding="utf-8")

    def _html_table(self, headers: list[str], rows: list[dict[str, Any]]) -> list[str]:
        if not headers:
            return ["<p>Nessun dato.</p>"]

        out = ["<table>"]
        out.append("<thead><tr>" + "".join(f"<th>{_xml_escape(h)}</th>" for h in headers) + "</tr></thead>")
        out.append("<tbody>")
        for row in rows:
            out.append("<tr>" + "".join(f"<td>{_xml_escape(str(row.get(h, '')))}</td>" for h in headers) + "</tr>")
        out.append("</tbody></table>")
        return out

    def _html_problem_table(self, headers: list[str], rows: list[dict[str, Any]]) -> list[str]:
        if not headers:
            return ["<p>Nessun dato.</p>"]

        out = ["<table>"]
        out.append("<thead><tr>" + "".join(f"<th>{_xml_escape(h)}</th>" for h in headers) + "</tr></thead>")
        out.append("<tbody>")

        last_file = ""
        for row in rows:
            file_name = str(row.get("File", ""))
            cls = " class='group-start'" if file_name != last_file else ""
            out.append(
                f"<tr{cls}>" + "".join(f"<td>{_xml_escape(str(row.get(h, '')))}</td>" for h in headers) + "</tr>"
            )
            last_file = file_name

        out.append("</tbody></table>")
        return out

    def _write_xlsx(self, file_path: Path, summary_rows: list[dict[str, Any]], problem_rows: list[dict[str, Any]]) -> None:
        summary_headers = self._collect_headers(summary_rows)
        problem_headers = self._collect_headers(problem_rows)

        sheet1 = self._build_sheet_xml(summary_headers, summary_rows)
        sheet2 = self._build_sheet_xml(problem_headers, problem_rows)

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
            "<sheet name='Riepilogo' sheetId='1' r:id='rId1'/>"
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

        with ZipFile(file_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("xl/workbook.xml", workbook)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet1)
            archive.writestr("xl/worksheets/sheet2.xml", sheet2)

    def _build_sheet_xml(self, headers: list[str], rows: list[dict[str, Any]]) -> str:
        lines = [
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>",
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>",
        ]

        if headers:
            lines.append("<row r='1'>")
            for idx, header in enumerate(headers, start=1):
                col = self._xlsx_col(idx)
                lines.append(f"<c r='{col}1' t='inlineStr'><is><t>{_xml_escape(header)}</t></is></c>")
            lines.append("</row>")

        for row_idx, row in enumerate(rows, start=2):
            lines.append(f"<row r='{row_idx}'>")
            for col_idx, header in enumerate(headers, start=1):
                col = self._xlsx_col(col_idx)
                value = _xml_escape(str(row.get(header, "")))
                lines.append(f"<c r='{col}{row_idx}' t='inlineStr'><is><t>{value}</t></is></c>")
            lines.append("</row>")

        lines.append("</sheetData></worksheet>")
        return "".join(lines)

    @staticmethod
    def _xlsx_col(index: int) -> str:
        col = ""
        value = index
        while value > 0:
            value, rem = divmod(value - 1, 26)
            col = chr(65 + rem) + col
        return col

    def _write_log(self, file_path: Path, rows: list[MP3DiagnosticResult]) -> None:
        lines = [
            "Diagnostica e Riparazione MP3",
            f"Generato: {datetime.now().isoformat(timespec='seconds')}",
            "",
        ]

        for row in rows:
            lines.append(f"File: {row.file_name}")
            lines.append(f"Percorso normalizzato: {row.normalized_path}")
            lines.append(f"Dimensione: {row.file_size_bytes}")
            lines.append(f"Modifica: {row.file_mtime_ts}")
            lines.append(f"Hash SHA-256: {row.file_hash_sha256}")
            lines.append(f"Comando analisi: {row.analysis_command}")
            lines.append(f"Exit analisi: {row.analysis_return_code}")
            if row.classification_reason == "Controllo integrità MP3 eseguito: No":
                lines.append("Integrità MP3: DISATTIVA - fase non eseguita")
            lines.append(f"Comando riparazione: {row.repair_command}")
            lines.append(f"Exit riparazione: {row.repair_return_code}")
            lines.append(f"Errori prima: {row.total_errors_before}")
            lines.append(f"Errori dopo: {row.total_errors_after}")
            lines.append(f"Integrita operativa iniziale: {row.operational_integrity_before}")
            lines.append(f"Integrita operativa finale: {row.operational_integrity_after}")
            lines.append(f"Anomalie ignorate: {row.ignored_anomalies_count}")
            lines.append(f"Inizio audio significativo: {row.bounds_before.significant_start_ms} ms")
            lines.append(f"Fine audio significativo: {row.bounds_before.significant_end_ms} ms")
            lines.append(f"Silenzio iniziale: {row.bounds_before.leading_silence_ms} ms")
            lines.append(f"Silenzio finale: {row.bounds_before.trailing_silence_ms} ms")
            lines.append(f"Motivo classificazione finale: {row.classification_reason}")
            lines.append(f"Esito finale: {row.repair_outcome}")
            lines.append(f"Cartella finale: {row.final_folder}")
            if row.winlive.verifica_winlive_eseguita:
                lines.append("WinLive: ATTIVO")
                status = row.winlive.stato_winlive_finale.value if row.winlive.stato_winlive_finale is not None else ""
                lines.append("-- Esito WinLive --")
                lines.append(f"Stato WinLive: {status}")
                lines.append(f"Presenza WL5SYNCT: {'SI' if row.winlive.synct_presente_prima else 'NO'}")
                lines.append(f"Presenza WL5CHORD: {'SI' if row.winlive.chord_presente_prima else 'NO'}")
                lines.append(f"Normalizzazione richiesta: {'SI' if row.winlive.normalizzazione_necessaria else 'NO'}")
                lines.append(f"Normalizzazione tentata: {'SI' if row.winlive.normalizzazione_tentata else 'NO'}")
                lines.append(f"Normalizzazione validata: {'SI' if row.winlive.normalizzazione_validata else 'NO'}")
                if row.winlive.motivo_validazione_post_modifica:
                    lines.append(f"Motivo validazione post-modifica: {row.winlive.motivo_validazione_post_modifica}")
                lines.append(f"Modificato: {'SI' if row.winlive.modificato else 'NO'}")
                lines.append(f"Catene temporali rilevate: {row.winlive.catene_temporali_rilevate}")
                lines.append(f"Tag temporali rimossi: {row.winlive.tag_temporali_rimossi}")
                lines.append(f"Righe solo temporali rilevate: {row.winlive.righe_temporali_vuote_rilevate}")
                lines.append(f"Righe solo temporali eliminate: {row.winlive.righe_temporali_vuote_eliminate}")
                if row.winlive.accordi_non_riconosciuti_token:
                    lines.append(
                        "Token accordi non riconosciuti: " + ", ".join(row.winlive.accordi_non_riconosciuti_token)
                    )
                lines.append(f"Cartella esito WinLive: {row.winlive.esito_cartella_winlive}")
                lines.append(f"Percorso esito WinLive: {row.winlive.esito_percorso_winlive}")
                lines.append(f"Operazione esito WinLive: {row.winlive.esito_operazione_winlive}")
                lines.append(f"Tempo lettura file (ms): {row.winlive.tempi_fasi_ms.get('lettura_file', 0.0):.3f}")
                lines.append(f"Tempo parsing WL5 (ms): {row.winlive.tempi_fasi_ms.get('ricerca_blocchi_wl5', 0.0):.3f}")
                lines.append(f"Tempo normalizzazione (ms): {row.winlive.tempi_fasi_ms.get('normalizzazione', 0.0):.3f}")
                lines.append(f"Tempo scrittura temporanea (ms): {row.winlive.tempi_fasi_ms.get('scrittura_temporanea', 0.0):.3f}")
                lines.append(f"Tempo validazione (ms): {row.winlive.tempi_fasi_ms.get('validazione_post_scrittura', 0.0):.3f}")
                lines.append(f"Safe-write totale (ms): {row.winlive.tempi_fasi_ms.get('safe_write_totale_ms', 0.0):.3f}")
                lines.append(f"Validazione totale (ms): {row.winlive.tempi_fasi_ms.get('validazione_totale_ms', 0.0):.3f}")
                lines.append(f"Tempo non attribuito (ms): {row.winlive.tempi_fasi_ms.get('tempo_non_attribuito_ms', 0.0):.3f}")
                lines.append(f"Tempo copia (ms): {row.winlive.tempi_fasi_ms.get('copia_file', 0.0):.3f}")
                lines.append(f"Tempo totale file (ms): {row.winlive.tempo_totale_ms:.3f}")
                if row.winlive.contatori_diagnostici:
                    lines.append("Contatori diagnostici WinLive: " + json.dumps(row.winlive.contatori_diagnostici, ensure_ascii=False))
                if row.winlive.note_winlive:
                    lines.append(f"Note WinLive: {MP3DiagnosticResult._join_notes(row.winlive.note_winlive)}")
                if row.winlive.errore_winlive_code or row.winlive.errore_winlive:
                    lines.append(f"Errore WinLive: {row.winlive.errore_winlive_code} {row.winlive.errore_winlive}".strip())
            lines.append("-- Log analisi originale (prima) --")
            lines.append(row.raw_decode_log_before or "")
            lines.append("-- Log analisi originale (dopo) --")
            lines.append(row.raw_decode_log_after or "")
            lines.append("-- Log riparazione originale --")
            lines.append(row.raw_repair_log or "")
            lines.append("")

        file_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_integrity_index(self, file_path: Path, rows: list[MP3DiagnosticResult]) -> None:
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "items": [row.to_integrity_record() for row in rows],
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_summary(
        self,
        rows: list[MP3DiagnosticResult],
        output_folder: Path,
        repair_mode: bool,
        placement_mode: str,
        verify_mp3_integrity: bool,
        verify_winlive: bool,
    ) -> dict[str, Any]:
        total = len(rows)
        perfect = sum(1 for row in rows if row.repair_outcome == STATUS_PERFECT) if verify_mp3_integrity else 0
        repaired = sum(1 for row in rows if row.repair_outcome == STATUS_REPAIRED) if verify_mp3_integrity else 0
        unrecoverable = sum(1 for row in rows if row.repair_outcome == STATUS_UNRECOVERABLE) if verify_mp3_integrity else 0
        category_ok = sum(1 for row in rows if row.final_category == DiagnosticCategory.OK) if verify_mp3_integrity else 0
        category_repaired = sum(1 for row in rows if row.final_category == DiagnosticCategory.REPAIRED) if verify_mp3_integrity else 0
        category_unrecoverable = (
            sum(1 for row in rows if row.final_category == DiagnosticCategory.UNRECOVERABLE) if verify_mp3_integrity else 0
        )
        ignored_anomalies = sum(row.ignored_anomalies_count for row in rows) if verify_mp3_integrity else 0

        return {
            "analyzed_files": total,
            "perfect_files": perfect,
            "repaired_files": repaired,
            "unrecoverable_files": unrecoverable,
            "category_ok_files": category_ok,
            "category_repaired_files": category_repaired,
            "category_unrecoverable_files": category_unrecoverable,
            "ignored_silent_anomalies": ignored_anomalies,
            "output_folder": str(output_folder),
            "repair_mode": bool(repair_mode),
            "placement_mode": self._sanitize_placement_mode(placement_mode),
            "placement_mode_label": self._placement_mode_label(placement_mode),
            "verify_mp3_integrity": bool(verify_mp3_integrity),
            "verify_winlive": bool(verify_winlive),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _cleanup_temp_dir(self, temp_dir: Path) -> None:
        if not temp_dir.exists():
            return
        for item in temp_dir.glob("*"):
            try:
                if item.is_file():
                    item.unlink()
            except OSError:
                pass

    @staticmethod
    def _command_to_text(command: list[str]) -> str:
        return " ".join(f'"{part}"' if " " in part else part for part in command)
