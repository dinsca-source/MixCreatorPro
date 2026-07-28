# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from winlive_safe_write import decode_text_lossless
from winlive_tags import (
    TAG_CHORD_CLOSE,
    TAG_CHORD_OPEN,
    TAG_SYNCT_CLOSE,
    TAG_SYNCT_OPEN,
    WinLiveStructureState,
    parse_winlive_blocks_strict,
)
from winlive_validation import AudioHashStatus, compute_mpeg_audio_hash, parse_audio_hash_plan


RecoveryLog = Callable[[str], None]


class MP3RecoveryStatus(str, Enum):
    ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS = "ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS"
    UNCHANGED_ORIGINAL_COPY = "UNCHANGED_ORIGINAL_COPY"
    FORCED_COPY_WITH_REPLACED_WINLIVE_TAGS = "FORCED_COPY_WITH_REPLACED_WINLIVE_TAGS"
    FORCED_UNCHANGED_ORIGINAL_COPY = "FORCED_UNCHANGED_ORIGINAL_COPY"
    ORIGINAL_FILE_NOT_COMPATIBLE = "ORIGINAL_FILE_NOT_COMPATIBLE"
    WINLIVE_TAGS_NOT_READABLE_OR_TRANSFERABLE = "WINLIVE_TAGS_NOT_READABLE_OR_TRANSFERABLE"
    READ_ERROR = "READ_ERROR"
    WRITE_ERROR = "WRITE_ERROR"
    FINAL_VERIFICATION_FAILED = "FINAL_VERIFICATION_FAILED"
    CANCELLED = "CANCELLED"


class RecoveryMode(str, Enum):
    NORMAL = "normal"
    FORCED = "forced"

    @classmethod
    def coerce(cls, value: "RecoveryMode | str | None") -> "RecoveryMode":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.NORMAL
        normalized = str(value).strip().casefold()
        for mode in cls:
            if mode.value == normalized:
                return mode
        raise ValueError(f"Modalita recupero non valida: {value}")


@dataclass(slots=True)
class MP3RecoveryResult:
    success: bool
    status: MP3RecoveryStatus
    error: str | None
    notes: list[str]
    recovery_mode: RecoveryMode
    original_path: str
    problematic_path: str
    output_path: str | None
    temporary_path: str | None
    strategy: str | None
    forced_recovery: bool
    audio_comparison_executed: bool
    audio_comparison_reason: str
    destination_renamed: bool
    compatibility_ok: bool
    problematic_winlive_present: bool
    original_winlive_present: bool
    tags_transferred: bool
    verification_ok: bool
    original_sha256_before: str | None
    original_sha256_after: str | None
    problematic_sha256_before: str | None
    problematic_sha256_after: str | None
    original_audio_hash: str | None
    problematic_audio_hash: str | None
    recovered_audio_hash: str | None


def recover_mp3_from_original(
    *,
    problematic_path: str | Path,
    original_path: str | Path,
    output_dir: str | Path,
    output_name: str | None = None,
    log_callback: RecoveryLog | None = None,
    cancel_event: object | None = None,
    recovery_mode: RecoveryMode | str = RecoveryMode.NORMAL,
    precomputed_original_audio_hash: str | None = None,
    precomputed_problematic_audio_hash: str | None = None,
) -> MP3RecoveryResult:
    recovery_mode = RecoveryMode.coerce(recovery_mode)
    _check_cancel(cancel_event)
    original_file = Path(original_path).expanduser()
    problematic_file = Path(problematic_path).expanduser()
    destination_dir = Path(output_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []

    original_sha_before = _sha256(original_file) if original_file.is_file() else None
    problematic_sha_before = _sha256(problematic_file) if problematic_file.is_file() else None
    if original_sha_before is None:
        return _failure(
            status=MP3RecoveryStatus.READ_ERROR,
            message=f"File originale non leggibile: {original_file}",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=False,
            audio_comparison_reason="Originale non leggibile.",
            compatibility_ok=False,
            problematic_winlive_present=False,
            original_winlive_present=False,
            tags_transferred=False,
            verification_ok=False,
            original_sha_before=None,
            original_sha_after=None,
            problematic_sha_before=problematic_sha_before,
            problematic_sha_after=problematic_sha_before,
            original_audio_hash=None,
            problematic_audio_hash=None,
            recovered_audio_hash=None,
        )

    if not problematic_file.is_file():
        return _failure(
            status=MP3RecoveryStatus.READ_ERROR,
            message=f"File problematico non leggibile: {problematic_file}",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=False,
            audio_comparison_reason="Problematico non leggibile.",
            compatibility_ok=False,
            problematic_winlive_present=False,
            original_winlive_present=False,
            tags_transferred=False,
            verification_ok=False,
            original_sha_before=original_sha_before,
            original_sha_after=_sha256(original_file),
            problematic_sha_before=None,
            problematic_sha_after=None,
            original_audio_hash=None,
            problematic_audio_hash=None,
            recovered_audio_hash=None,
        )

    original_data = original_file.read_bytes()
    problematic_data = problematic_file.read_bytes()
    original_sha_after_read = hashlib.sha256(original_data).hexdigest()
    problematic_sha_after_read = hashlib.sha256(problematic_data).hexdigest()
    if original_sha_before != original_sha_after_read:
        return _failure(
            status=MP3RecoveryStatus.READ_ERROR,
            message="Lettura originale incoerente.",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=False,
            audio_comparison_reason="Lettura originale incoerente.",
            compatibility_ok=False,
            problematic_winlive_present=False,
            original_winlive_present=False,
            tags_transferred=False,
            verification_ok=False,
            original_sha_before=original_sha_before,
            original_sha_after=original_sha_after_read,
            problematic_sha_before=problematic_sha_before,
            problematic_sha_after=problematic_sha_after_read,
            original_audio_hash=None,
            problematic_audio_hash=None,
            recovered_audio_hash=None,
        )

    audio_comparison_executed = recovery_mode == RecoveryMode.NORMAL
    audio_comparison_reason = (
        "Disabilitato dall'utente tramite modalita Recupero forzato"
        if recovery_mode == RecoveryMode.FORCED
        else "Confronto eseguito."
    )
    original_audio_hash = None
    problematic_audio_hash = None
    original_audio_status = AudioHashStatus.VALID_AUDIO_STREAM
    problematic_audio_status = AudioHashStatus.VALID_AUDIO_STREAM
    compatibility_ok = True

    if audio_comparison_executed:
        if precomputed_original_audio_hash is not None and precomputed_problematic_audio_hash is not None:
            original_audio_hash = precomputed_original_audio_hash
            problematic_audio_hash = precomputed_problematic_audio_hash
        else:
            original_audio = compute_mpeg_audio_hash(
                original_data,
                cancel_event=cancel_event,
                debug_callback=log_callback,
                source_label=f"originale={original_file.name}",
            )
            problematic_audio = compute_mpeg_audio_hash(
                problematic_data,
                cancel_event=cancel_event,
                debug_callback=log_callback,
                source_label=f"problematico={problematic_file.name}",
            )
            if original_audio.status == AudioHashStatus.CANCELLED or problematic_audio.status == AudioHashStatus.CANCELLED:
                raise RecoveryCancelled()
            original_audio_hash = original_audio.audio_hash_sha256
            problematic_audio_hash = problematic_audio.audio_hash_sha256
            original_audio_status = original_audio.status
            problematic_audio_status = problematic_audio.status

        compatibility_ok = bool(
            original_audio_hash
            and problematic_audio_hash
            and original_audio_hash == problematic_audio_hash
        )
        _log(
            log_callback,
            f"Compatibilita audio: {compatibility_ok} | originale={original_audio_status} | problematico={problematic_audio_status}",
        )
        if not compatibility_ok:
            return _failure(
                status=MP3RecoveryStatus.ORIGINAL_FILE_NOT_COMPATIBLE,
                message="I due file non risultano compatibili per il recupero automatico.",
                notes=notes,
                recovery_mode=recovery_mode,
                original_file=original_file,
                problematic_file=problematic_file,
                output_path=None,
                temporary_path=None,
                forced_recovery=False,
                audio_comparison_executed=True,
                audio_comparison_reason="Confronto audio eseguito.",
                compatibility_ok=False,
                problematic_winlive_present=_contains_winlive_markers(problematic_data),
                original_winlive_present=_contains_winlive_markers(original_data),
                tags_transferred=False,
                verification_ok=False,
                original_sha_before=original_sha_before,
                original_sha_after=original_sha_after_read,
                problematic_sha_before=problematic_sha_before,
                problematic_sha_after=problematic_sha_after_read,
                original_audio_hash=original_audio_hash,
                problematic_audio_hash=problematic_audio_hash,
                recovered_audio_hash=None,
            )
    else:
        _log(log_callback, "Compatibilita audio: non eseguita per recupero forzato.")
        if original_audio_hash is None:
            original_audio = compute_mpeg_audio_hash(
                original_data,
                cancel_event=cancel_event,
                debug_callback=log_callback,
                source_label=f"originale={original_file.name}",
            )
            if original_audio.status == AudioHashStatus.CANCELLED:
                raise RecoveryCancelled()
            original_audio_hash = original_audio.audio_hash_sha256

    original_parsed = parse_winlive_blocks_strict(original_data)
    problematic_parsed = parse_winlive_blocks_strict(problematic_data)
    original_winlive_present = _contains_winlive_markers(original_data)
    problematic_winlive_present = _contains_winlive_markers(problematic_data)

    _log(log_callback, f"WinLive problematico presente: {problematic_winlive_present}")
    _log(log_callback, f"WinLive originale presente: {original_winlive_present}")

    original_valid_blocks = _valid_winlive_blocks(original_parsed)
    problematic_valid_blocks = _valid_winlive_blocks(problematic_parsed)
    if problematic_winlive_present and not problematic_valid_blocks:
        return _failure(
            status=MP3RecoveryStatus.WINLIVE_TAGS_NOT_READABLE_OR_TRANSFERABLE,
            message="I TAG WinLive del file problematico non sono leggibili o trasferibili.",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=audio_comparison_executed,
            audio_comparison_reason=audio_comparison_reason,
            compatibility_ok=True,
            problematic_winlive_present=True,
            original_winlive_present=original_winlive_present,
            tags_transferred=False,
            verification_ok=False,
            original_sha_before=original_sha_before,
            original_sha_after=original_sha_after_read,
            problematic_sha_before=problematic_sha_before,
            problematic_sha_after=problematic_sha_after_read,
            original_audio_hash=original_audio_hash,
            problematic_audio_hash=problematic_audio_hash,
            recovered_audio_hash=None,
        )

    if not problematic_valid_blocks:
        strategy = MP3RecoveryStatus.UNCHANGED_ORIGINAL_COPY
        candidate_bytes = original_data
        tags_transferred = False
    else:
        _log(log_callback, "[TECH] Fase -> Recupero TAG WinLive")
        strategy = MP3RecoveryStatus.ORIGINAL_COPY_WITH_REPLACED_WINLIVE_TAGS
        candidate_bytes = _build_recovered_bytes(
            original_data=original_data,
            problematic_data=problematic_data,
            original_parsed=original_parsed,
            problematic_parsed=problematic_parsed,
            cancel_event=cancel_event,
        )
        tags_transferred = True

    _check_cancel(cancel_event)

    output_path = _resolve_output_path(
        destination_dir=destination_dir,
        original_file=original_file,
        output_name=output_name,
    )
    destination_renamed = output_path.name != _default_output_name(original_file, output_name)

    temp_path = None
    try:
        _log(log_callback, "[TECH] Fase -> Scrittura file")
        temp_path = _write_temp_candidate(candidate_bytes, destination_dir, output_path.stem)
        _check_cancel(cancel_event)
        _log(log_callback, "[TECH] Fase -> Verifica finale")
        verification_ok, verification_notes, recovered_audio_hash = _verify_recovered_candidate(
            original_bytes=original_data,
            problematic_bytes=problematic_data,
            candidate_path=Path(temp_path),
            strategy=strategy,
            original_audio_hash=original_audio_hash,
            problematic_audio_hash=problematic_audio_hash,
            original_valid_blocks=original_valid_blocks,
            problematic_valid_blocks=problematic_valid_blocks,
        )
        notes.extend(verification_notes)

        if not verification_ok:
            _safe_unlink(temp_path)
            return _failure(
                status=MP3RecoveryStatus.FINAL_VERIFICATION_FAILED,
                message="La verifica finale del file recuperato non è stata superata.",
                notes=notes,
                recovery_mode=recovery_mode,
                original_file=original_file,
                problematic_file=problematic_file,
                output_path=None,
                temporary_path=None,
                forced_recovery=recovery_mode == RecoveryMode.FORCED,
                audio_comparison_executed=audio_comparison_executed,
                audio_comparison_reason=audio_comparison_reason,
                compatibility_ok=True,
                problematic_winlive_present=problematic_winlive_present,
                original_winlive_present=original_winlive_present,
                tags_transferred=tags_transferred,
                verification_ok=False,
                original_sha_before=original_sha_before,
                original_sha_after=original_sha_after_read,
                problematic_sha_before=problematic_sha_before,
                problematic_sha_after=problematic_sha_after_read,
                original_audio_hash=original_audio_hash,
                problematic_audio_hash=problematic_audio_hash,
                recovered_audio_hash=recovered_audio_hash,
            )

        _check_cancel(cancel_event)
        final_output_path = _promote_temp_file(temp_path, output_path)

    except RecoveryCancelled:
        _safe_unlink(temp_path)
        return _failure(
            status=MP3RecoveryStatus.CANCELLED,
            message="Operazione annullata.",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=audio_comparison_executed,
            audio_comparison_reason=audio_comparison_reason,
            compatibility_ok=True,
            problematic_winlive_present=problematic_winlive_present,
            original_winlive_present=original_winlive_present,
            tags_transferred=tags_transferred,
            verification_ok=False,
            original_sha_before=original_sha_before,
            original_sha_after=original_sha_after_read,
            problematic_sha_before=problematic_sha_before,
            problematic_sha_after=problematic_sha_after_read,
            original_audio_hash=original_audio_hash,
            problematic_audio_hash=problematic_audio_hash,
            recovered_audio_hash=None,
        )
    except OSError as exc:
        _safe_unlink(temp_path)
        return _failure(
            status=MP3RecoveryStatus.WRITE_ERROR,
            message=f"Errore di scrittura del file recuperato: {exc}",
            notes=notes,
            recovery_mode=recovery_mode,
            original_file=original_file,
            problematic_file=problematic_file,
            output_path=None,
            temporary_path=None,
            forced_recovery=recovery_mode == RecoveryMode.FORCED,
            audio_comparison_executed=audio_comparison_executed,
            audio_comparison_reason=audio_comparison_reason,
            compatibility_ok=True,
            problematic_winlive_present=problematic_winlive_present,
            original_winlive_present=original_winlive_present,
            tags_transferred=tags_transferred,
            verification_ok=False,
            original_sha_before=original_sha_before,
            original_sha_after=original_sha_after_read,
            problematic_sha_before=problematic_sha_before,
            problematic_sha_after=problematic_sha_after_read,
            original_audio_hash=original_audio_hash,
            problematic_audio_hash=problematic_audio_hash,
            recovered_audio_hash=None,
        )

    recovered_audio = compute_mpeg_audio_hash(
        final_output_path.read_bytes(),
        cancel_event=cancel_event,
        debug_callback=log_callback,
        source_label=f"recuperato={final_output_path.name}",
    )
    if recovered_audio.status == AudioHashStatus.CANCELLED:
        raise RecoveryCancelled()
    recovered_audio_hash = recovered_audio.audio_hash_sha256
    final_sha_after = _sha256(original_file)
    final_problematic_sha_after = _sha256(problematic_file)
    original_unchanged = original_sha_before == final_sha_after
    problematic_unchanged = problematic_sha_before == final_problematic_sha_after

    notes.append("Originale confermato invariato.")
    notes.append("Problematico confermato invariato.")
    _log(log_callback, f"Strategia: {strategy.value}")
    _log(log_callback, f"Risultato: {final_output_path}")
    _log(log_callback, f"Verifica finale: audio={recovered_audio.status}, hash={recovered_audio_hash}")
    _log(log_callback, f"Originale invariato: {original_unchanged}")
    _log(log_callback, f"Problematico invariato: {problematic_unchanged}")

    final_status = strategy
    if recovery_mode == RecoveryMode.FORCED:
        final_status = (
            MP3RecoveryStatus.FORCED_COPY_WITH_REPLACED_WINLIVE_TAGS
            if tags_transferred
            else MP3RecoveryStatus.FORCED_UNCHANGED_ORIGINAL_COPY
        )

    return MP3RecoveryResult(
        success=True,
        status=final_status,
        error=None,
        notes=notes,
        recovery_mode=recovery_mode,
        original_path=str(original_file),
        problematic_path=str(problematic_file),
        output_path=str(final_output_path),
        temporary_path=None,
        strategy=final_status.value,
        forced_recovery=recovery_mode == RecoveryMode.FORCED,
        audio_comparison_executed=audio_comparison_executed,
        audio_comparison_reason=audio_comparison_reason,
        destination_renamed=destination_renamed,
        compatibility_ok=True,
        problematic_winlive_present=problematic_winlive_present,
        original_winlive_present=original_winlive_present,
        tags_transferred=tags_transferred,
        verification_ok=True,
        original_sha256_before=original_sha_before,
        original_sha256_after=final_sha_after,
        problematic_sha256_before=problematic_sha_before,
        problematic_sha256_after=final_problematic_sha_after,
        original_audio_hash=original_audio_hash,
        problematic_audio_hash=problematic_audio_hash,
        recovered_audio_hash=recovered_audio_hash,
    )


class RecoveryCancelled(RuntimeError):
    pass


def _check_cancel(cancel_event: object | None) -> None:
    if cancel_event is not None and hasattr(cancel_event, "is_set") and bool(cancel_event.is_set()):
        raise RecoveryCancelled()


def _build_recovered_bytes(
    *,
    original_data: bytes,
    problematic_data: bytes,
    original_parsed,
    problematic_parsed,
    cancel_event: object | None = None,
) -> bytes:
    original_span = _any_winlive_span(original_data)
    insertion_span = _problematic_winlive_span(problematic_data, problematic_parsed)

    if insertion_span is None:
        return original_data

    insert_bytes = problematic_data[insertion_span[0] : insertion_span[1]]
    if original_span is not None:
        return original_data[: original_span[0]] + insert_bytes + original_data[original_span[1] :]

    audio_plan = parse_audio_hash_plan(original_data, cancel_event=cancel_event)
    insert_at = audio_plan.mpeg_frames_region.end if audio_plan.mpeg_frames_region is not None else len(original_data)
    return original_data[:insert_at] + insert_bytes + original_data[insert_at:]

def _valid_winlive_blocks(parsed) -> bool:
    blocks = []
    for block in (parsed.synct, parsed.chord):
        if block.state == WinLiveStructureState.INVALID_STRUCTURE:
            return False
        if block.state == WinLiveStructureState.VALID:
            blocks.append(block)

    if not blocks:
        return False

    synct = parsed.synct
    chord = parsed.chord
    if synct.state == WinLiveStructureState.VALID:
        synct_decoded = decode_text_lossless(synct.content_bytes or b"")
        if synct_decoded.text is None:
            return False
    if chord.state == WinLiveStructureState.VALID:
        chord_decoded = decode_text_lossless(chord.content_bytes or b"", preferred_encoding=synct_decoded.report.used_encoding if synct.state == WinLiveStructureState.VALID else None)
        if chord_decoded.text is None:
            return False

    return True


def _problematic_winlive_span(data: bytes, parsed) -> tuple[int, int] | None:
    offsets: list[int] = []
    end_offsets: list[int] = []

    for block, open_tag, close_tag in (
        (parsed.synct, TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE),
        (parsed.chord, TAG_CHORD_OPEN, TAG_CHORD_CLOSE),
    ):
        if block.state != WinLiveStructureState.VALID:
            continue
        if block.open_offset is None or block.close_offset is None:
            continue
        offsets.append(int(block.open_offset))
        end_offsets.append(int(block.close_offset) + len(close_tag))

    if not offsets:
        return None

    return min(offsets), max(end_offsets)


def _any_winlive_span(data: bytes) -> tuple[int, int] | None:
    positions = [index for index in _find_first_marker(data) if index is not None]
    if not positions:
        return None
    return min(positions), _find_last_marker_end(data)


def _find_first_marker(data: bytes) -> list[int | None]:
    positions: list[int | None] = []
    for marker in (TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE, TAG_CHORD_OPEN, TAG_CHORD_CLOSE):
        index = data.find(marker)
        positions.append(index if index >= 0 else None)
    return positions


def _find_last_marker_end(data: bytes) -> int:
    last = 0
    for marker in (TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE, TAG_CHORD_OPEN, TAG_CHORD_CLOSE):
        index = data.rfind(marker)
        if index >= 0:
            last = max(last, index + len(marker))
    return last


def _write_temp_candidate(candidate_bytes: bytes, destination_dir: Path, stem: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".mp3",
        prefix=f"{stem}_",
        dir=str(destination_dir),
        delete=False,
    )
    try:
        handle.write(candidate_bytes)
        handle.flush()
        if hasattr(os, "fsync"):
            os.fsync(handle.fileno())
    finally:
        handle.close()

    if not Path(handle.name).is_file() or Path(handle.name).stat().st_size <= 0:
        raise OSError("Il file temporaneo non è stato scritto correttamente.")
    return handle.name


def _promote_temp_file(temp_path: str, destination_path: Path) -> Path:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_path, destination_path)
    return destination_path


def _resolve_output_path(*, destination_dir: Path, original_file: Path, output_name: str | None) -> Path:
    desired_name = _default_output_name(original_file, output_name)
    target = destination_dir / desired_name
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix or ".mp3"
    index = 1
    while True:
        candidate = destination_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _default_output_name(original_file: Path, output_name: str | None) -> str:
    if output_name:
        name = str(output_name).strip()
        if name:
            return name if name.lower().endswith(".mp3") else f"{name}.mp3"
    return original_file.name


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _verify_recovered_candidate(
    *,
    original_bytes: bytes,
    problematic_bytes: bytes,
    candidate_path: Path,
    strategy: MP3RecoveryStatus,
    original_audio_hash: str | None,
    problematic_audio_hash: str | None,
    original_valid_blocks,
    problematic_valid_blocks,
) -> tuple[bool, list[str], str | None]:
    notes: list[str] = []
    if not candidate_path.is_file() or candidate_path.stat().st_size <= 0:
        return False, ["Il file temporaneo non esiste o è vuoto."], None

    candidate_bytes = candidate_path.read_bytes()
    candidate_audio = compute_mpeg_audio_hash(candidate_bytes)
    candidate_audio_hash = candidate_audio.audio_hash_sha256
    if candidate_audio_hash is None:
        return False, ["Verifica audio finale non disponibile."], candidate_audio_hash

    if candidate_audio_hash != original_audio_hash:
        return False, ["L'audio del risultato non coincide con l'originale."], candidate_audio_hash

    parsed_candidate = parse_winlive_blocks_strict(candidate_bytes)
    if strategy == MP3RecoveryStatus.UNCHANGED_ORIGINAL_COPY:
        if candidate_bytes != original_bytes:
            return False, ["La copia invariata non coincide con l'originale."], candidate_audio_hash
        return True, ["Copia invariata verificata."], candidate_audio_hash

    candidate_synct = parsed_candidate.synct
    candidate_chord = parsed_candidate.chord
    problem_synct = parse_winlive_blocks_strict(problematic_bytes).synct
    problem_chord = parse_winlive_blocks_strict(problematic_bytes).chord

    if problem_synct.state == WinLiveStructureState.VALID:
        if candidate_synct.state != WinLiveStructureState.VALID or candidate_synct.content_bytes != problem_synct.content_bytes:
            return False, ["SYNCT finale non corrispondente al problematico."], candidate_audio_hash
    if problem_chord.state == WinLiveStructureState.VALID:
        if candidate_chord.state != WinLiveStructureState.VALID or candidate_chord.content_bytes != problem_chord.content_bytes:
            return False, ["CHORD finale non corrispondente al problematico."], candidate_audio_hash

    return True, ["TAG WinLive trasferiti e verificati."], candidate_audio_hash


def _contains_winlive_markers(data: bytes) -> bool:
    return any(marker in data for marker in (TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE, TAG_CHORD_OPEN, TAG_CHORD_CLOSE))


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _log(log_callback: RecoveryLog | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def _failure(
    *,
    status: MP3RecoveryStatus,
    message: str,
    notes: list[str],
    recovery_mode: RecoveryMode,
    original_file: Path,
    problematic_file: Path,
    output_path: str | None,
    temporary_path: str | None,
    forced_recovery: bool,
    audio_comparison_executed: bool,
    audio_comparison_reason: str,
    compatibility_ok: bool,
    problematic_winlive_present: bool,
    original_winlive_present: bool,
    tags_transferred: bool,
    verification_ok: bool,
    original_sha_before: str | None,
    original_sha_after: str | None,
    problematic_sha_before: str | None,
    problematic_sha_after: str | None,
    original_audio_hash: str | None,
    problematic_audio_hash: str | None,
    recovered_audio_hash: str | None,
) -> MP3RecoveryResult:
    notes.append(message)
    return MP3RecoveryResult(
        success=False,
        status=status,
        error=message,
        notes=notes,
        recovery_mode=recovery_mode,
        original_path=str(original_file),
        problematic_path=str(problematic_file),
        output_path=output_path,
        temporary_path=temporary_path,
        strategy=status.value,
        forced_recovery=forced_recovery,
        audio_comparison_executed=audio_comparison_executed,
        audio_comparison_reason=audio_comparison_reason,
        destination_renamed=False,
        compatibility_ok=compatibility_ok,
        problematic_winlive_present=problematic_winlive_present,
        original_winlive_present=original_winlive_present,
        tags_transferred=tags_transferred,
        verification_ok=verification_ok,
        original_sha256_before=original_sha_before,
        original_sha256_after=original_sha_after,
        problematic_sha256_before=problematic_sha_before,
        problematic_sha256_after=problematic_sha_after,
        original_audio_hash=original_audio_hash,
        problematic_audio_hash=problematic_audio_hash,
        recovered_audio_hash=recovered_audio_hash,
    )