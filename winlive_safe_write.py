# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
import hashlib
import time
from dataclasses import dataclass
from enum import Enum

from winlive_classification import (
    PostNormalizationValidationStatus,
    WinLiveClassificationInput,
    WinLiveOutcome,
    classify_winlive,
)
from winlive_normalizer import normalize_synct_content
from winlive_normalizer import build_logical_line_diffs
from winlive_tags import (
    TAG_CHORD_CLOSE,
    TAG_CHORD_OPEN,
    TAG_SYNCT_CLOSE,
    TAG_SYNCT_OPEN,
    WinLiveStructureState,
    parse_winlive_blocks_strict,
)
from winlive_validation import (
    ByteRegion,
    validate_normalized_winlive_file,
)


class WinLiveWriteErrorCode(str, Enum):
    READ_FAILED = "READ_FAILED"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    AMBIGUOUS_STRUCTURE = "AMBIGUOUS_STRUCTURE"
    DECODING_FAILED = "DECODING_FAILED"
    ENCODING_FAILED = "ENCODING_FAILED"
    NORMALIZATION_INVALID = "NORMALIZATION_INVALID"
    WRITE_FAILED = "WRITE_FAILED"
    READBACK_FAILED = "READBACK_FAILED"
    READBACK_INVALID_STRUCTURE = "READBACK_INVALID_STRUCTURE"
    TEXT_MISMATCH = "TEXT_MISMATCH"
    CHORD_MISMATCH = "CHORD_MISMATCH"
    NON_IDEMPOTENT_NORMALIZATION = "NON_IDEMPOTENT_NORMALIZATION"
    CANONICALIZATION_NOT_STABLE = "CANONICALIZATION_NOT_STABLE"
    AUDIO_MISMATCH = "AUDIO_MISMATCH"
    METADATA_MISMATCH = "METADATA_MISMATCH"


@dataclass(slots=True)
class EncodingReport:
    detected_encoding: str | None
    used_encoding: str | None
    converted: bool
    lossless: bool
    error: str | None = None


@dataclass(slots=True)
class DecodedText:
    text: str | None
    report: EncodingReport


@dataclass(slots=True)
class WinLiveWriteValidationResult:
    write_succeeded: bool
    readback_succeeded: bool
    winlive_structure_valid: bool
    text_matches_expected: bool
    chords_match_expected: bool
    normalization_idempotent: bool
    original_audio_hash: str | None
    copy_audio_hash: str | None
    audio_identical: bool
    metadata_preserved: bool
    prefix_preserved: bool
    postfix_preserved: bool
    error_code: WinLiveWriteErrorCode | None
    error: str | None
    notes: list[str]
    temporary_path: str | None
    suggested_outcome: WinLiveOutcome | None
    encoding_detected: str | None
    encoding_used: str | None
    encoding_converted: bool
    encoding_lossless: bool
    rewrite_metrics: dict[str, object]
    canonicalization_iterations: int
    canonicalization_stabilized: bool
    canonicalization_cycle_detected: bool
    canonicalization_cycle_at_iteration: int
    canonicalization_state_hashes: list[str]
    canonicalization_change_log: list[dict[str, object]]
    first_residual_diff: dict[str, object]
    phase_times_ms: dict[str, float]
    diagnostic_counters: dict[str, int]


def detect_text_encoding(raw_bytes: bytes, preferred_encoding: str | None = None) -> EncodingReport:
    candidates: list[str] = []
    if preferred_encoding is not None:
        candidates.append(preferred_encoding)

    for candidate in ("utf-8", "cp1252"):
        if candidate not in candidates:
            candidates.append(candidate)

    last_error: str | None = None
    for encoding in candidates:
        try:
            decoded = raw_bytes.decode(encoding, errors="strict")
            encoded = decoded.encode(encoding, errors="strict")
        except UnicodeError as exc:
            last_error = f"{encoding}: {exc}"
            continue

        if encoded != raw_bytes:
            last_error = f"{encoding}: round-trip non lossless"
            continue

        return EncodingReport(
            detected_encoding=encoding,
            used_encoding=encoding,
            converted=False,
            lossless=True,
            error=None,
        )

    return EncodingReport(
        detected_encoding=None,
        used_encoding=None,
        converted=False,
        lossless=False,
        error=last_error or "Nessun encoding lossless rilevato.",
    )


def decode_text_lossless(raw_bytes: bytes, preferred_encoding: str | None = None) -> DecodedText:
    report = detect_text_encoding(raw_bytes, preferred_encoding=preferred_encoding)
    if report.used_encoding is None:
        return DecodedText(text=None, report=report)

    try:
        decoded = raw_bytes.decode(report.used_encoding, errors="strict")
    except UnicodeError as exc:
        report.lossless = False
        report.error = str(exc)
        return DecodedText(text=None, report=report)

    try:
        encoded = decoded.encode(report.used_encoding, errors="strict")
    except UnicodeError as exc:
        report.lossless = False
        report.error = str(exc)
        return DecodedText(text=None, report=report)

    if encoded != raw_bytes:
        report.lossless = False
        report.error = "Round-trip non lossless"
        return DecodedText(text=None, report=report)

    return DecodedText(text=decoded, report=report)


def encode_text_strict(text: str, encoding: str) -> tuple[bytes | None, str | None]:
    try:
        return text.encode(encoding, errors="strict"), None
    except UnicodeError as exc:
        return None, str(exc)


def write_normalized_winlive_copy(
    source_path: str,
    temp_dir: str,
    preferred_encoding: str | None = None,
    keep_temporary_on_failure: bool = False,
) -> WinLiveWriteValidationResult:
    notes: list[str] = []
    temp_path: str | None = None
    phase_times_ms: dict[str, float] = {}
    counters: dict[str, int] = {
        "read_original": 0,
        "parse_original": 0,
        "normalize": 0,
        "idempotence_normalize": 0,
        "write_temp": 0,
        "read_temp": 0,
        "parse_temp": 0,
        "significant_text_extract": 0,
        "compare_chord": 0,
        "hash_segments": 0,
        "retry_write": 0,
        "retry_read": 0,
        "retry_delete": 0,
        "copy_promote": 0,
    }
    safe_write_start = time.perf_counter()

    try:
        read_start = time.perf_counter()
        with open(source_path, "rb") as source_file:
            original_data = source_file.read()
        counters["read_original"] += 1
        phase_times_ms["lettura_file"] = max(0.0, (time.perf_counter() - read_start) * 1000.0)
    except OSError as exc:
        return _result_error(
            code=WinLiveWriteErrorCode.READ_FAILED,
            message=f"Lettura originale fallita: {exc}",
            notes=notes,
            temporary_path=None,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    parse_start = time.perf_counter()
    parsed = parse_winlive_blocks_strict(original_data)
    counters["parse_original"] += 1
    phase_times_ms["ricerca_blocchi_wl5"] = max(0.0, (time.perf_counter() - parse_start) * 1000.0)
    if parsed.synct.state != WinLiveStructureState.VALID or parsed.chord.state != WinLiveStructureState.VALID:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Struttura WinLive non valida: scrittura temporanea rifiutata.",
            notes=notes,
            temporary_path=None,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    if parsed.synct.open_offset is None or parsed.synct.close_offset is None:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Offset SYNCT mancanti.",
            notes=notes,
            temporary_path=None,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    if parsed.chord.open_offset is None or parsed.chord.close_offset is None:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Offset CHORD mancanti.",
            notes=notes,
            temporary_path=None,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    synct_block_start = int(parsed.synct.open_offset)
    synct_block_end = int(parsed.synct.close_offset) + len(TAG_SYNCT_CLOSE)
    if synct_block_end <= synct_block_start:
        return _result_error(
            code=WinLiveWriteErrorCode.AMBIGUOUS_STRUCTURE,
            message="Offset WL5SYNCT incoerenti.",
            notes=notes,
            temporary_path=None,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    synct_raw = parsed.synct.content_bytes or b""
    chord_raw = parsed.chord.content_bytes or b""

    synct_decoded = decode_text_lossless(synct_raw, preferred_encoding=preferred_encoding)
    if synct_decoded.text is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message=f"Decodifica SYNCT fallita: {synct_decoded.report.error}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
        )

    chord_decoded = decode_text_lossless(chord_raw, preferred_encoding=synct_decoded.report.used_encoding)
    if chord_decoded.text is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message=f"Decodifica CHORD fallita: {chord_decoded.report.error}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
        )

    normalized = normalize_synct_content(synct_decoded.text)
    counters["normalize"] += 1
    canonicalization_change_log = _canonicalization_change_log(normalized)
    first_residual_diff: dict[str, object] = {}
    if not normalized.canonicalization_stabilized or not normalized.temporal_normalization_succeeded:
        return _result_error(
            code=WinLiveWriteErrorCode.CANONICALIZATION_NOT_STABLE,
            message="Canonicalizzazione WinLive non stabile entro il limite interno.",
            notes=notes + normalized.notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
            canonicalization_iterations=normalized.canonicalization_iterations,
            canonicalization_stabilized=normalized.canonicalization_stabilized,
            canonicalization_cycle_detected=normalized.canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=normalized.canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=list(normalized.canonicalization_state_hashes),
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
        )

    if not normalized.text_semantically_valid:
        return _result_error(
            code=WinLiveWriteErrorCode.NORMALIZATION_INVALID,
            message="Normalizzazione SYNCT semanticamente non valida.",
            notes=notes + normalized.notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
            canonicalization_iterations=normalized.canonicalization_iterations,
            canonicalization_stabilized=normalized.canonicalization_stabilized,
            canonicalization_cycle_detected=normalized.canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=normalized.canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=list(normalized.canonicalization_state_hashes),
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
        )

    second_pass = normalize_synct_content(normalized.normalized_text)
    counters["idempotence_normalize"] += 1
    if second_pass.normalized_text != normalized.normalized_text or not second_pass.text_semantically_valid:
        residual_diffs = build_logical_line_diffs(normalized.normalized_text, second_pass.normalized_text, max_items=1)
        if residual_diffs:
            first_residual_diff = residual_diffs[0]
        return _result_error(
            code=WinLiveWriteErrorCode.NON_IDEMPOTENT_NORMALIZATION,
            message="Normalizzazione non idempotente.",
            notes=notes + second_pass.notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
            canonicalization_iterations=normalized.canonicalization_iterations,
            canonicalization_stabilized=normalized.canonicalization_stabilized,
            canonicalization_cycle_detected=normalized.canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=normalized.canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=list(normalized.canonicalization_state_hashes),
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
        )

    if synct_decoded.report.used_encoding is None:
        return _result_error(
            code=WinLiveWriteErrorCode.ENCODING_FAILED,
            message="Encoding utilizzato non disponibile.",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    encoded_synct, encode_error = encode_text_strict(normalized.normalized_text, synct_decoded.report.used_encoding)
    if encoded_synct is None:
        return _result_error(
            code=WinLiveWriteErrorCode.ENCODING_FAILED,
            message=f"Ricodifica SYNCT fallita: {encode_error}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    build_start = time.perf_counter()
    new_synct_block = TAG_SYNCT_OPEN + encoded_synct + TAG_SYNCT_CLOSE
    rebuilt = original_data[:synct_block_start] + new_synct_block + original_data[synct_block_end:]
    phase_times_ms["costruzione_nuovi_bytes"] = max(0.0, (time.perf_counter() - build_start) * 1000.0)

    try:
        write_start = time.perf_counter()
        temp_path = _write_temp_file(rebuilt, temp_dir)
        counters["write_temp"] += 1
        phase_times_ms["scrittura_temporaneo_totale_ms"] = max(0.0, (time.perf_counter() - write_start) * 1000.0)
        phase_times_ms.update(_LAST_TEMP_WRITE_PHASES)
    except OSError as exc:
        if isinstance(exc, PermissionError):
            notes.append(f"WRITE_TEMP PermissionError: {exc}")
        return _result_error(
            code=WinLiveWriteErrorCode.WRITE_FAILED,
            message=f"Scrittura temporanea fallita: {exc}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    try:
        readback_start = time.perf_counter()
        with open(temp_path, "rb") as temp_file:
            copy_data = temp_file.read()
        counters["read_temp"] += 1
        phase_times_ms["rilettura_temporaneo"] = max(0.0, (time.perf_counter() - readback_start) * 1000.0)
    except OSError as exc:
        _cleanup_if_needed(temp_path, keep_temporary_on_failure)
        return _result_error(
            code=WinLiveWriteErrorCode.READBACK_FAILED,
            message=f"Rilettura temporaneo fallita: {exc}",
            notes=notes,
            temporary_path=temp_path if keep_temporary_on_failure else None,
            encoding_report=synct_decoded.report,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    rewrite_metrics = _build_rewrite_metrics(
        original_data=original_data,
        copy_data=copy_data,
        original_synct_start=synct_block_start,
        original_synct_end=synct_block_end,
        new_synct_block=new_synct_block,
    )

    validation_result = _validate_written_copy(
        original_data=original_data,
        copy_data=copy_data,
        expected_synct_text=normalized.normalized_text,
        expected_chord_text=chord_decoded.text,
        original_encoding=synct_decoded.report,
        temporary_path=temp_path,
        text_was_modified=normalized.changed,
        rewrite_metrics=rewrite_metrics,
        canonicalization_iterations=normalized.canonicalization_iterations,
        canonicalization_stabilized=normalized.canonicalization_stabilized,
        canonicalization_cycle_detected=normalized.canonicalization_cycle_detected,
        canonicalization_cycle_at_iteration=normalized.canonicalization_cycle_at_iteration,
        canonicalization_state_hashes=list(normalized.canonicalization_state_hashes),
        canonicalization_change_log=canonicalization_change_log,
        first_residual_diff=first_residual_diff,
        phase_times_ms=phase_times_ms,
        counters=counters,
    )

    if validation_result.error_code is not None and not keep_temporary_on_failure:
        cleanup_temporary_copy(temp_path)
        counters["retry_delete"] += 0
        validation_result.temporary_path = None

    validation_result.phase_times_ms["safe_write_totale_ms"] = (time.perf_counter() - safe_write_start) * 1000.0
    validation_result.phase_times_ms["tempo_non_attribuito_ms"] = _compute_unattributed_time(validation_result.phase_times_ms)

    return validation_result


def cleanup_temporary_copy(path: str | None) -> bool:
    if path is None:
        return False
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


def _contains_winlive_markers(data: bytes) -> bool:
    markers = (TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE, TAG_CHORD_OPEN, TAG_CHORD_CLOSE)
    return any(marker in data for marker in markers)


def _write_temp_file(content: bytes, temp_dir: str) -> str:
    global _LAST_TEMP_WRITE_PHASES
    _LAST_TEMP_WRITE_PHASES = {
        "apertura_temporaneo": 0.0,
        "scrittura_bytes_temporaneo": 0.0,
        "flush_temporaneo": 0.0,
        "fsync_temporaneo": 0.0,
        "chiusura_temporaneo": 0.0,
        "attesa_disponibilita_temporaneo": 0.0,
    }
    open_start = time.perf_counter()
    handle = tempfile.NamedTemporaryFile(mode="wb", suffix=".tmp", prefix="wl5_", dir=temp_dir, delete=False)
    _LAST_TEMP_WRITE_PHASES["apertura_temporaneo"] = (time.perf_counter() - open_start) * 1000.0
    try:
        write_start = time.perf_counter()
        handle.write(content)
        _LAST_TEMP_WRITE_PHASES["scrittura_bytes_temporaneo"] = (time.perf_counter() - write_start) * 1000.0
        flush_start = time.perf_counter()
        handle.flush()
        _LAST_TEMP_WRITE_PHASES["flush_temporaneo"] = (time.perf_counter() - flush_start) * 1000.0
        if hasattr(os, "fsync"):
            fsync_start = time.perf_counter()
            os.fsync(handle.fileno())
            _LAST_TEMP_WRITE_PHASES["fsync_temporaneo"] = (time.perf_counter() - fsync_start) * 1000.0
    finally:
        close_start = time.perf_counter()
        handle.close()
        _LAST_TEMP_WRITE_PHASES["chiusura_temporaneo"] = (time.perf_counter() - close_start) * 1000.0
    return handle.name


def _validate_written_copy(
    original_data: bytes,
    copy_data: bytes,
    expected_synct_text: str,
    expected_chord_text: str,
    original_encoding: EncodingReport,
    temporary_path: str,
    text_was_modified: bool,
    rewrite_metrics: dict[str, object],
    canonicalization_iterations: int,
    canonicalization_stabilized: bool,
    canonicalization_cycle_detected: bool,
    canonicalization_cycle_at_iteration: int,
    canonicalization_state_hashes: list[str],
    canonicalization_change_log: list[dict[str, object]],
    first_residual_diff: dict[str, object],
    phase_times_ms: dict[str, float],
    counters: dict[str, int],
) -> WinLiveWriteValidationResult:
    notes: list[str] = []
    validation_start = time.perf_counter()

    strict_start = time.perf_counter()
    strict_validation = validate_normalized_winlive_file(
        original_data=original_data,
        candidate_data=copy_data,
    )
    phase_times_ms["validazione_strict_struttura"] = (time.perf_counter() - strict_start) * 1000.0
    counters["parse_temp"] += 1
    counters["significant_text_extract"] += 2
    if not strict_validation.valid:
        code = WinLiveWriteErrorCode.READBACK_INVALID_STRUCTURE
        if strict_validation.reason_code in {"MEANINGFUL_TEXT_LOST", "MEANINGFUL_TEXT_CHANGED"}:
            code = WinLiveWriteErrorCode.TEXT_MISMATCH
        elif strict_validation.reason_code == "CHORD_CHANGED":
            code = WinLiveWriteErrorCode.CHORD_MISMATCH
        notes.append(f"{strict_validation.reason_code}: {strict_validation.reason_message}")
        return _result_error(
            code=code,
            message=strict_validation.reason_message,
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=False,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    parse_temp_start = time.perf_counter()
    parsed_original = parse_winlive_blocks_strict(original_data)
    parsed_copy = parse_winlive_blocks_strict(copy_data)
    phase_times_ms["ricerca_blocchi_temporaneo"] = (time.perf_counter() - parse_temp_start) * 1000.0
    counters["parse_temp"] += 2

    structure_ok = (
        parsed_copy.synct.state == WinLiveStructureState.VALID
        and parsed_copy.chord.state == WinLiveStructureState.VALID
        and parsed_copy.synct.open_offsets
        and parsed_copy.synct.close_offsets
        and parsed_copy.chord.open_offsets
        and parsed_copy.chord.close_offsets
        and len(parsed_copy.synct.open_offsets) == 1
        and len(parsed_copy.synct.close_offsets) == 1
        and len(parsed_copy.chord.open_offsets) == 1
        and len(parsed_copy.chord.close_offsets) == 1
    )

    if not structure_ok:
        return _result_error(
            code=WinLiveWriteErrorCode.READBACK_INVALID_STRUCTURE,
            message="Struttura WinLive nel temporaneo non valida.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=False,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    copy_synct_raw = parsed_copy.synct.content_bytes or b""
    copy_chord_raw = parsed_copy.chord.content_bytes or b""

    encoding_used = original_encoding.used_encoding
    if encoding_used is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message="Encoding originario non disponibile per verifica rilettura.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    copy_synct_decoded = _decode_with_encoding(copy_synct_raw, encoding_used)
    if copy_synct_decoded is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message="Decodifica SYNCT del temporaneo fallita.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    copy_chord_decoded = _decode_with_encoding(copy_chord_raw, encoding_used)
    if copy_chord_decoded is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message="Decodifica CHORD del temporaneo fallita.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    text_matches = copy_synct_decoded == expected_synct_text
    if not text_matches:
        return _result_error(
            code=WinLiveWriteErrorCode.TEXT_MISMATCH,
            message="Contenuto SYNCT riscritto non coincidente con l'atteso.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            text_matches_expected=False,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    counters["compare_chord"] += 1
    chords_match = copy_chord_decoded == expected_chord_text
    if not chords_match:
        return _result_error(
            code=WinLiveWriteErrorCode.CHORD_MISMATCH,
            message="Contenuto CHORD alterato in modo non autorizzato.",
            notes=notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            text_matches_expected=True,
            chords_match_expected=False,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    idempotence_start = time.perf_counter()
    idempotence_check = normalize_synct_content(copy_synct_decoded)
    phase_times_ms["idempotenza_seconda_normalizzazione"] = (time.perf_counter() - idempotence_start) * 1000.0
    counters["idempotence_normalize"] += 1
    idempotent = idempotence_check.normalized_text == copy_synct_decoded and idempotence_check.text_semantically_valid
    if not idempotent:
        return _result_error(
            code=WinLiveWriteErrorCode.NON_IDEMPOTENT_NORMALIZATION,
            message="Il testo scritto richiede ulteriori normalizzazioni.",
            notes=notes + idempotence_check.notes,
            temporary_path=temporary_path,
            encoding_report=original_encoding,
            write_succeeded=True,
            readback_succeeded=True,
            winlive_structure_valid=True,
            text_matches_expected=True,
            chords_match_expected=True,
            normalization_idempotent=False,
            rewrite_metrics=rewrite_metrics,
            canonicalization_iterations=canonicalization_iterations,
            canonicalization_stabilized=canonicalization_stabilized,
            canonicalization_cycle_detected=canonicalization_cycle_detected,
            canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
            canonicalization_state_hashes=canonicalization_state_hashes,
            canonicalization_change_log=canonicalization_change_log,
            first_residual_diff=first_residual_diff,
            phase_times_ms=phase_times_ms,
            diagnostic_counters=counters,
        )

    audio_identical = bool(rewrite_metrics.get("prefix_equal")) and bool(rewrite_metrics.get("suffix_equal"))

    metadata_start = time.perf_counter()
    metadata_preserved, prefix_preserved, postfix_preserved, metadata_notes = _compare_non_winlive_regions(
        original_data,
        copy_data,
        parsed_original.prefix_bytes,
        parsed_copy.prefix_bytes,
        parsed_original.trailing_bytes,
        parsed_copy.trailing_bytes,
        parsed_original.between_bytes,
        parsed_copy.between_bytes,
        counters,
    )
    phase_times_ms["confronto_prefisso_suffisso_metadati"] = (time.perf_counter() - metadata_start) * 1000.0
    notes.extend(metadata_notes)

    error_code: WinLiveWriteErrorCode | None = None
    error_message: str | None = None
    if not audio_identical:
        error_code = WinLiveWriteErrorCode.AUDIO_MISMATCH
        error_message = "Hash/sequenza audio MPEG non coincidenti tra originale e copia."

    if not metadata_preserved and error_code is None:
        error_code = WinLiveWriteErrorCode.METADATA_MISMATCH
        error_message = "Regioni non WinLive modificate."

    if not bool(rewrite_metrics.get("length_equation_ok", False)) and error_code is None:
        error_code = WinLiveWriteErrorCode.METADATA_MISMATCH
        error_message = "Lunghezza finale incoerente con il delta del blocco SYNCT."

    post_status = PostNormalizationValidationStatus.OK
    if error_code is not None:
        post_status = PostNormalizationValidationStatus.FAILED

    classification = classify_winlive(
        input_data=WinLiveClassificationInput(
            text_valid=True,
            chord_valid=True,
            chord_unrecognized_count=copy_chord_decoded.count("?"),
            text_was_modified=text_was_modified,
            post_validation_status=post_status,
        )
    )

    phase_times_ms["validazione_totale_ms"] = (time.perf_counter() - validation_start) * 1000.0

    return WinLiveWriteValidationResult(
        write_succeeded=True,
        readback_succeeded=True,
        winlive_structure_valid=True,
        text_matches_expected=True,
        chords_match_expected=True,
        normalization_idempotent=True,
        original_audio_hash=None,
        copy_audio_hash=None,
        audio_identical=audio_identical,
        metadata_preserved=metadata_preserved,
        prefix_preserved=prefix_preserved,
        postfix_preserved=postfix_preserved,
        error_code=error_code,
        error=error_message,
        notes=notes,
        temporary_path=temporary_path,
        suggested_outcome=classification.outcome,
        encoding_detected=original_encoding.detected_encoding,
        encoding_used=original_encoding.used_encoding,
        encoding_converted=original_encoding.converted,
        encoding_lossless=original_encoding.lossless,
        rewrite_metrics=rewrite_metrics,
        canonicalization_iterations=canonicalization_iterations,
        canonicalization_stabilized=canonicalization_stabilized,
        canonicalization_cycle_detected=canonicalization_cycle_detected,
        canonicalization_cycle_at_iteration=canonicalization_cycle_at_iteration,
        canonicalization_state_hashes=canonicalization_state_hashes,
        canonicalization_change_log=canonicalization_change_log,
        first_residual_diff=first_residual_diff,
        phase_times_ms=phase_times_ms,
        diagnostic_counters=counters,
    )


def _compare_non_winlive_regions(
    original_data: bytes,
    copy_data: bytes,
    original_prefix: bytes,
    copy_prefix: bytes,
    original_postfix: bytes,
    copy_postfix: bytes,
    original_between: bytes,
    copy_between: bytes,
    counters: dict[str, int],
) -> tuple[bool, bool, bool, list[str]]:
    notes: list[str] = []

    prefix_ok = original_prefix == copy_prefix
    postfix_ok = original_postfix == copy_postfix
    between_ok = original_between == copy_between

    if not prefix_ok:
        notes.append("Prefisso modificato")
    if not postfix_ok:
        notes.append("Postfix modificato")
    if not between_ok:
        notes.append("Regione intermedia tra SYNCT e CHORD modificata")

    plan_original = _detect_metadata_regions(original_data)
    plan_copy = _detect_metadata_regions(copy_data)

    id3v2_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.id3v2_region, plan_copy.id3v2_region)
    ape_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.ape_region, plan_copy.ape_region)
    id3v1_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.id3v1_region, plan_copy.id3v1_region)
    counters["hash_segments"] += 3

    if not id3v2_ok:
        notes.append("ID3v2 modificato")
    if not ape_ok:
        notes.append("APEv2 modificato")
    if not id3v1_ok:
        notes.append("ID3v1 modificato")

    metadata_ok = prefix_ok and postfix_ok and between_ok and id3v2_ok and ape_ok and id3v1_ok
    return metadata_ok, prefix_ok, postfix_ok, notes


def _compare_optional_region_bytes(
    original_data: bytes,
    copy_data: bytes,
    original_region: ByteRegion | None,
    copy_region: ByteRegion | None,
) -> bool:
    if original_region is None and copy_region is None:
        return True
    if (original_region is None) != (copy_region is None):
        return False
    if original_region is None or copy_region is None:
        return False

    original_slice = original_data[original_region.start : original_region.end]
    copy_slice = copy_data[copy_region.start : copy_region.end]
    return original_slice == copy_slice


def _decode_with_encoding(raw_bytes: bytes, encoding: str) -> str | None:
    try:
        decoded = raw_bytes.decode(encoding, errors="strict")
        encoded = decoded.encode(encoding, errors="strict")
    except UnicodeError:
        return None

    if encoded != raw_bytes:
        return None
    return decoded


def _cleanup_if_needed(path: str | None, keep_temporary_on_failure: bool) -> None:
    if keep_temporary_on_failure:
        return
    cleanup_temporary_copy(path)


def _result_error(
    code: WinLiveWriteErrorCode,
    message: str,
    notes: list[str],
    temporary_path: str | None,
    encoding_report: EncodingReport | None = None,
    write_succeeded: bool = False,
    readback_succeeded: bool = False,
    winlive_structure_valid: bool = False,
    text_matches_expected: bool = False,
    chords_match_expected: bool = False,
    normalization_idempotent: bool = False,
    rewrite_metrics: dict[str, object] | None = None,
    canonicalization_iterations: int = 0,
    canonicalization_stabilized: bool = False,
    canonicalization_cycle_detected: bool = False,
    canonicalization_cycle_at_iteration: int = 0,
    canonicalization_state_hashes: list[str] | None = None,
    canonicalization_change_log: list[dict[str, object]] | None = None,
    first_residual_diff: dict[str, object] | None = None,
    phase_times_ms: dict[str, float] | None = None,
    diagnostic_counters: dict[str, int] | None = None,
) -> WinLiveWriteValidationResult:
    detected = None
    used = None
    converted = False
    lossless = False
    if encoding_report is not None:
        detected = encoding_report.detected_encoding
        used = encoding_report.used_encoding
        converted = encoding_report.converted
        lossless = encoding_report.lossless

    return WinLiveWriteValidationResult(
        write_succeeded=write_succeeded,
        readback_succeeded=readback_succeeded,
        winlive_structure_valid=winlive_structure_valid,
        text_matches_expected=text_matches_expected,
        chords_match_expected=chords_match_expected,
        normalization_idempotent=normalization_idempotent,
        original_audio_hash=None,
        copy_audio_hash=None,
        audio_identical=False,
        metadata_preserved=False,
        prefix_preserved=False,
        postfix_preserved=False,
        error_code=code,
        error=message,
        notes=notes,
        temporary_path=temporary_path,
        suggested_outcome=WinLiveOutcome.MODIFICATION_NOT_INTEGRAL,
        encoding_detected=detected,
        encoding_used=used,
        encoding_converted=converted,
        encoding_lossless=lossless,
        rewrite_metrics=dict(rewrite_metrics or {}),
        canonicalization_iterations=int(canonicalization_iterations),
        canonicalization_stabilized=bool(canonicalization_stabilized),
        canonicalization_cycle_detected=bool(canonicalization_cycle_detected),
        canonicalization_cycle_at_iteration=int(canonicalization_cycle_at_iteration),
        canonicalization_state_hashes=list(canonicalization_state_hashes or []),
        canonicalization_change_log=list(canonicalization_change_log or []),
        first_residual_diff=dict(first_residual_diff or {}),
        phase_times_ms=dict(phase_times_ms or {}),
        diagnostic_counters=dict(diagnostic_counters or {}),
    )


def _canonicalization_change_log(normalized: object) -> list[dict[str, object]]:
    summaries = list(getattr(normalized, "canonicalization_pass_summaries", []) or [])
    out: list[dict[str, object]] = []
    for entry in summaries:
        counters = entry.get("counters")
        out.append(
            {
                "iteration": int(entry.get("iteration", 0)),
                "changed": bool(entry.get("changed", False)),
                "phase": str(entry.get("phase", "")),
                "modification_count": int(entry.get("modification_count", 0)),
                "input_hash": str(entry.get("input_hash", "")),
                "output_hash": str(entry.get("output_hash", "")),
                "adjacent_time_tags_removed": int(getattr(counters, "adjacent_time_tags_removed", 0)),
                "empty_timed_lines_removed": int(getattr(counters, "empty_timed_lines_removed", 0)),
                "previous_row_end_adjustments": int(getattr(counters, "previous_row_end_adjustments", 0)),
                "current_row_start_adjustments": int(getattr(counters, "current_row_start_adjustments", 0)),
            }
        )
    return out


def _build_rewrite_metrics(
    *,
    original_data: bytes,
    copy_data: bytes,
    original_synct_start: int,
    original_synct_end: int,
    new_synct_block: bytes,
) -> dict[str, object]:
    parsed_copy = parse_winlive_blocks_strict(copy_data)
    if parsed_copy.synct.open_offset is None or parsed_copy.synct.close_offset is None:
        return {
            "original_file_len": len(original_data),
            "temporary_file_len": len(copy_data),
            "error": "SYNCT non parsabile nel temporaneo",
        }

    temporary_synct_start = int(parsed_copy.synct.open_offset)
    temporary_synct_end = int(parsed_copy.synct.close_offset) + len(TAG_SYNCT_CLOSE)
    original_block_len = original_synct_end - original_synct_start
    new_block_len = len(new_synct_block)
    delta_len = new_block_len - original_block_len
    expected_len = len(original_data) - original_block_len + new_block_len

    original_prefix = original_data[:original_synct_start]
    temporary_prefix = copy_data[:temporary_synct_start]
    original_suffix = original_data[original_synct_end:]
    temporary_suffix = copy_data[temporary_synct_end:]

    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    metrics: dict[str, object] = {
        "original_file_len": len(original_data),
        "temporary_file_len": len(copy_data),
        "original_synct_start": original_synct_start,
        "original_synct_end": original_synct_end,
        "temporary_synct_start": temporary_synct_start,
        "temporary_synct_end": temporary_synct_end,
        "original_synct_block_len": original_block_len,
        "new_synct_block_len": new_block_len,
        "delta_len": delta_len,
        "expected_file_len": expected_len,
        "length_equation_ok": len(copy_data) == expected_len,
        "prefix_equal": original_prefix == temporary_prefix,
        "suffix_equal": original_suffix == temporary_suffix,
        "prefix_hash_original": _sha256(original_prefix),
        "prefix_hash_temporary": _sha256(temporary_prefix),
        "suffix_hash_original": _sha256(original_suffix),
        "suffix_hash_temporary": _sha256(temporary_suffix),
    }

    parsed_original = parse_winlive_blocks_strict(original_data)
    original_chord = parsed_original.chord.content_bytes or b""
    temporary_chord = parsed_copy.chord.content_bytes or b""
    metrics["chord_equal"] = original_chord == temporary_chord
    metrics["chord_hash_original"] = _sha256(original_chord)
    metrics["chord_hash_temporary"] = _sha256(temporary_chord)
    return metrics


@dataclass(slots=True)
class _MetadataRegions:
    id3v2_region: ByteRegion | None
    id3v1_region: ByteRegion | None
    ape_region: ByteRegion | None


def _detect_metadata_regions(data: bytes) -> _MetadataRegions:
    return _MetadataRegions(
        id3v2_region=_detect_id3v2_region(data),
        id3v1_region=_detect_id3v1_region(data),
        ape_region=_detect_apev2_footer_region(data),
    )


def _detect_id3v2_region(data: bytes) -> ByteRegion | None:
    if len(data) < 10 or data[0:3] != b"ID3":
        return None
    size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
    total = min(10 + size, len(data))
    return ByteRegion(start=0, end=total)


def _detect_id3v1_region(data: bytes) -> ByteRegion | None:
    if len(data) < 128 or data[-128:-125] != b"TAG":
        return None
    return ByteRegion(start=len(data) - 128, end=len(data))


def _detect_apev2_footer_region(data: bytes) -> ByteRegion | None:
    marker = b"APETAGEX"
    index = data.rfind(marker)
    if index < 0 or index + 16 > len(data):
        return None
    size = int.from_bytes(data[index + 12 : index + 16], byteorder="little", signed=False)
    if size <= 0:
        return None
    start = max(0, index - max(0, size - 32))
    end = min(len(data), index + 32)
    return ByteRegion(start=start, end=end)


def _compute_unattributed_time(phase_times_ms: dict[str, float]) -> float:
    total = float(phase_times_ms.get("safe_write_totale_ms", 0.0))
    attributable_keys = (
        "lettura_file",
        "ricerca_blocchi_wl5",
        "costruzione_nuovi_bytes",
        "scrittura_temporaneo_totale_ms",
        "rilettura_temporaneo",
        "validazione_totale_ms",
    )
    subtotal = sum(float(phase_times_ms.get(key, 0.0)) for key in attributable_keys)
    return max(0.0, total - subtotal)


_LAST_TEMP_WRITE_PHASES: dict[str, float] = {}
