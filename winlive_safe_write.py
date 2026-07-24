# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import Enum

from winlive_classification import (
    PostNormalizationValidationStatus,
    WinLiveClassificationInput,
    WinLiveOutcome,
    classify_winlive,
)
from winlive_normalizer import normalize_synct_content
from winlive_tags import (
    TAG_CHORD_CLOSE,
    TAG_CHORD_OPEN,
    TAG_SYNCT_CLOSE,
    TAG_SYNCT_OPEN,
    WinLiveStructureState,
    parse_winlive_blocks_strict,
)
from winlive_validation import AudioHashResult, ByteRegion, compute_mpeg_audio_hash, parse_audio_hash_plan


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

    try:
        with open(source_path, "rb") as source_file:
            original_data = source_file.read()
    except OSError as exc:
        return _result_error(
            code=WinLiveWriteErrorCode.READ_FAILED,
            message=f"Lettura originale fallita: {exc}",
            notes=notes,
            temporary_path=None,
        )

    parsed = parse_winlive_blocks_strict(original_data)
    if parsed.synct.state != WinLiveStructureState.VALID or parsed.chord.state != WinLiveStructureState.VALID:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Struttura WinLive non valida: scrittura temporanea rifiutata.",
            notes=notes,
            temporary_path=None,
        )

    if parsed.synct.open_offset is None or parsed.synct.close_offset is None:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Offset SYNCT mancanti.",
            notes=notes,
            temporary_path=None,
        )

    if parsed.chord.open_offset is None or parsed.chord.close_offset is None:
        return _result_error(
            code=WinLiveWriteErrorCode.INVALID_STRUCTURE,
            message="Offset CHORD mancanti.",
            notes=notes,
            temporary_path=None,
        )

    if not (parsed.synct.open_offset < parsed.synct.close_offset < parsed.chord.open_offset < parsed.chord.close_offset):
        return _result_error(
            code=WinLiveWriteErrorCode.AMBIGUOUS_STRUCTURE,
            message="Offset blocchi WinLive incoerenti o sovrapposti.",
            notes=notes,
            temporary_path=None,
        )

    if _contains_winlive_markers(parsed.trailing_bytes) or _contains_winlive_markers(parsed.between_bytes):
        return _result_error(
            code=WinLiveWriteErrorCode.AMBIGUOUS_STRUCTURE,
            message="Marker WinLive extra rilevati fuori dai blocchi autorizzati.",
            notes=notes,
            temporary_path=None,
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
        )

    chord_decoded = decode_text_lossless(chord_raw, preferred_encoding=synct_decoded.report.used_encoding)
    if chord_decoded.text is None:
        return _result_error(
            code=WinLiveWriteErrorCode.DECODING_FAILED,
            message=f"Decodifica CHORD fallita: {chord_decoded.report.error}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    normalized = normalize_synct_content(synct_decoded.text)
    if not normalized.text_semantically_valid:
        return _result_error(
            code=WinLiveWriteErrorCode.NORMALIZATION_INVALID,
            message="Normalizzazione SYNCT semanticamente non valida.",
            notes=notes + normalized.notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    second_pass = normalize_synct_content(normalized.normalized_text)
    if second_pass.normalized_text != normalized.normalized_text or not second_pass.text_semantically_valid:
        return _result_error(
            code=WinLiveWriteErrorCode.NON_IDEMPOTENT_NORMALIZATION,
            message="Normalizzazione non idempotente.",
            notes=notes + second_pass.notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    if synct_decoded.report.used_encoding is None:
        return _result_error(
            code=WinLiveWriteErrorCode.ENCODING_FAILED,
            message="Encoding utilizzato non disponibile.",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    encoded_synct, encode_error = encode_text_strict(normalized.normalized_text, synct_decoded.report.used_encoding)
    if encoded_synct is None:
        return _result_error(
            code=WinLiveWriteErrorCode.ENCODING_FAILED,
            message=f"Ricodifica SYNCT fallita: {encode_error}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    rebuilt = (
        parsed.prefix_bytes
        + TAG_SYNCT_OPEN
        + encoded_synct
        + TAG_SYNCT_CLOSE
        + parsed.between_bytes
        + TAG_CHORD_OPEN
        + chord_raw
        + TAG_CHORD_CLOSE
        + parsed.trailing_bytes
    )

    try:
        temp_path = _write_temp_file(rebuilt, temp_dir)
    except OSError as exc:
        return _result_error(
            code=WinLiveWriteErrorCode.WRITE_FAILED,
            message=f"Scrittura temporanea fallita: {exc}",
            notes=notes,
            temporary_path=None,
            encoding_report=synct_decoded.report,
        )

    try:
        with open(temp_path, "rb") as temp_file:
            copy_data = temp_file.read()
    except OSError as exc:
        _cleanup_if_needed(temp_path, keep_temporary_on_failure)
        return _result_error(
            code=WinLiveWriteErrorCode.READBACK_FAILED,
            message=f"Rilettura temporaneo fallita: {exc}",
            notes=notes,
            temporary_path=temp_path if keep_temporary_on_failure else None,
            encoding_report=synct_decoded.report,
        )

    validation_result = _validate_written_copy(
        original_data=original_data,
        copy_data=copy_data,
        expected_synct_text=normalized.normalized_text,
        expected_chord_text=chord_decoded.text,
        original_encoding=synct_decoded.report,
        temporary_path=temp_path,
        text_was_modified=normalized.changed,
    )

    if validation_result.error_code is not None and not keep_temporary_on_failure:
        cleanup_temporary_copy(temp_path)
        validation_result.temporary_path = None

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
    handle = tempfile.NamedTemporaryFile(mode="wb", suffix=".tmp", prefix="wl5_", dir=temp_dir, delete=False)
    try:
        handle.write(content)
        handle.flush()
        if hasattr(os, "fsync"):
            os.fsync(handle.fileno())
    finally:
        handle.close()
    return handle.name


def _validate_written_copy(
    original_data: bytes,
    copy_data: bytes,
    expected_synct_text: str,
    expected_chord_text: str,
    original_encoding: EncodingReport,
    temporary_path: str,
    text_was_modified: bool,
) -> WinLiveWriteValidationResult:
    notes: list[str] = []

    parsed_original = parse_winlive_blocks_strict(original_data)
    parsed_copy = parse_winlive_blocks_strict(copy_data)

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
        )

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
        )

    idempotence_check = normalize_synct_content(copy_synct_decoded)
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
        )

    audio_original = compute_mpeg_audio_hash(original_data)
    audio_copy = compute_mpeg_audio_hash(copy_data)
    audio_identical = _audio_results_match(audio_original, audio_copy)

    metadata_preserved, prefix_preserved, postfix_preserved, metadata_notes = _compare_non_winlive_regions(
        original_data,
        copy_data,
        parsed_original.prefix_bytes,
        parsed_copy.prefix_bytes,
        parsed_original.trailing_bytes,
        parsed_copy.trailing_bytes,
        parsed_original.between_bytes,
        parsed_copy.between_bytes,
    )
    notes.extend(metadata_notes)

    error_code: WinLiveWriteErrorCode | None = None
    error_message: str | None = None
    if not audio_identical:
        error_code = WinLiveWriteErrorCode.AUDIO_MISMATCH
        error_message = "Hash/sequenza audio MPEG non coincidenti tra originale e copia."

    if not metadata_preserved and error_code is None:
        error_code = WinLiveWriteErrorCode.METADATA_MISMATCH
        error_message = "Regioni non WinLive modificate."

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

    return WinLiveWriteValidationResult(
        write_succeeded=True,
        readback_succeeded=True,
        winlive_structure_valid=True,
        text_matches_expected=True,
        chords_match_expected=True,
        normalization_idempotent=True,
        original_audio_hash=audio_original.audio_hash_sha256,
        copy_audio_hash=audio_copy.audio_hash_sha256,
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
    )


def _audio_results_match(original: AudioHashResult, copy: AudioHashResult) -> bool:
    if original.status != copy.status:
        return False
    if original.frames_count != copy.frames_count:
        return False
    if original.audio_bytes_hashed != copy.audio_bytes_hashed:
        return False
    if original.audio_hash_sha256 != copy.audio_hash_sha256:
        return False

    original_signature = [(frame.offset, frame.length, frame.version, frame.layer, frame.sample_rate_hz) for frame in original.frame_sequence]
    copy_signature = [(frame.offset, frame.length, frame.version, frame.layer, frame.sample_rate_hz) for frame in copy.frame_sequence]
    return original_signature == copy_signature


def _compare_non_winlive_regions(
    original_data: bytes,
    copy_data: bytes,
    original_prefix: bytes,
    copy_prefix: bytes,
    original_postfix: bytes,
    copy_postfix: bytes,
    original_between: bytes,
    copy_between: bytes,
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

    plan_original = parse_audio_hash_plan(original_data)
    plan_copy = parse_audio_hash_plan(copy_data)

    id3v2_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.id3v2_region, plan_copy.id3v2_region)
    ape_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.ape_region, plan_copy.ape_region)
    id3v1_ok = _compare_optional_region_bytes(original_data, copy_data, plan_original.id3v1_region, plan_copy.id3v1_region)

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
        suggested_outcome=WinLiveOutcome.NORMALIZATION_NOT_VALIDATED,
        encoding_detected=detected,
        encoding_used=used,
        encoding_converted=converted,
        encoding_lossless=lossless,
    )
