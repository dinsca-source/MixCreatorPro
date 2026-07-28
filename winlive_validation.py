# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from winlive_normalizer import extract_significant_text
from winlive_tags import TAG_CHORD_CLOSE, TAG_SYNCT_CLOSE, WinLiveStructureState, parse_winlive_blocks_strict


class AudioHashStatus(str, Enum):
    VALID_AUDIO_STREAM = "VALID_AUDIO_STREAM"
    PARTIAL_AUDIO_STREAM = "PARTIAL_AUDIO_STREAM"
    NO_AUDIO_STREAM = "NO_AUDIO_STREAM"
    AMBIGUOUS_AUDIO_STREAM = "AMBIGUOUS_AUDIO_STREAM"
    CANCELLED = "CANCELLED"


AudioHashDebugLog = Callable[[str], None]


class AudioHashCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class WinLiveNormalizedFileValidationResult:
    valid: bool
    reason_code: str
    reason_message: str
    synct_present_before: bool
    synct_present_after: bool
    chord_present_before: bool
    chord_present_after: bool
    meaningful_text_equal: bool
    chord_unchanged: bool
    terminator_preserved: bool
    initial_value_preserved: bool


@dataclass(slots=True)
class ByteRegion:
    start: int
    end: int


@dataclass(slots=True)
class MpegFrame:
    offset: int
    length: int
    bitrate_kbps: int
    sample_rate_hz: int
    version: str
    layer: str
    padding: int
    has_crc: bool


@dataclass(slots=True)
class MpegScanStats:
    parse_calls: int = 0
    unique_offsets: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    outer_iterations: int = 0
    inner_iterations: int = 0
    frames_found: int = 0
    frames_valid: int = 0
    frames_rejected: int = 0
    scanner_elapsed_seconds: float = 0.0
    average_speed_mb_s: float = 0.0


@dataclass(slots=True)
class AudioHashPlan:
    id3v2_region: ByteRegion | None
    id3v1_region: ByteRegion | None
    ape_region: ByteRegion | None
    winlive_regions: list[ByteRegion]
    mpeg_frames_region: ByteRegion | None
    skipped_regions: list[ByteRegion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AudioHashResult:
    status: AudioHashStatus
    audio_hash_sha256: str | None
    plan: AudioHashPlan
    frames_count: int
    audio_bytes_hashed: int
    first_frame_offset: int | None
    last_frame_end_offset: int | None
    anomalies: list[str] = field(default_factory=list)
    non_audio_gaps: list[ByteRegion] = field(default_factory=list)
    frame_sequence: list[MpegFrame] = field(default_factory=list)


def parse_audio_hash_plan(
    data: bytes,
    *,
    cancel_event: object | None = None,
    debug_callback: AudioHashDebugLog | None = None,
    source_label: str = "",
) -> AudioHashPlan:
    plan_started_at = time.monotonic()
    _emit_debug(debug_callback, f"[HASH] Inizio parsing regioni escluse {source_label}")
    _emit_telemetry_event(
        debug_callback,
        "HASH_PLAN_START",
        {
            "phase": "Parsing regioni escluse",
            "message": f"Inizio parsing regioni escluse {source_label}",
        },
    )
    _check_cancel(cancel_event)
    id3v2 = _detect_id3v2_region(data)
    id3v1 = _detect_id3v1_region(data)
    ape = _detect_apev2_footer_region(data)
    winlive_regions = _detect_winlive_regions(data)
    _check_cancel(cancel_event)

    start = id3v2.end if id3v2 is not None else 0
    end = len(data)
    if ape is not None:
        end = min(end, ape.start)
    if id3v1 is not None:
        end = min(end, id3v1.start)

    chains, _, _ = _scan_mpeg_frames(
        data,
        start,
        end,
        cancel_event=cancel_event,
        debug_callback=debug_callback,
        source_label=source_label,
    )
    frames: list[MpegFrame] = []
    if chains:
        ranked = sorted(chains, key=lambda chain: (len(chain), sum(frame.length for frame in chain)), reverse=True)
        frames = ranked[0]
    frame_region = None
    if frames:
        frame_region = ByteRegion(start=frames[0].offset, end=frames[-1].offset + frames[-1].length)

    skipped: list[ByteRegion] = []
    for region in (id3v2, ape, id3v1):
        if region is not None:
            skipped.append(region)
    skipped.extend(winlive_regions)

    notes: list[str] = []
    if not frames:
        notes.append("Nessun frame MPEG valido trovato nella regione audio candidata.")

    _emit_debug(
        debug_callback,
        f"[HASH] Fine parsing regioni escluse {source_label} | regioni_escluse={len(skipped)}",
    )
    _emit_telemetry_event(
        debug_callback,
        "HASH_PLAN_END",
        {
            "phase": "Parsing regioni escluse",
            "message": f"Fine parsing regioni escluse {source_label}",
            "plan_elapsed_seconds": max(0.0, time.monotonic() - plan_started_at),
        },
    )

    return AudioHashPlan(
        id3v2_region=id3v2,
        id3v1_region=id3v1,
        ape_region=ape,
        winlive_regions=winlive_regions,
        mpeg_frames_region=frame_region,
        skipped_regions=skipped,
        notes=notes,
    )


def compute_mpeg_audio_hash(
    data: bytes,
    min_chain_frames: int = 3,
    *,
    cancel_event: object | None = None,
    debug_callback: AudioHashDebugLog | None = None,
    source_label: str = "",
) -> AudioHashResult:
    started_at = time.monotonic()
    scan_elapsed = 0.0
    hash_elapsed = 0.0
    _emit_debug(debug_callback, f"[HASH] Inizio calcolo {source_label} | dimensione_bytes={len(data)}")
    _emit_telemetry_event(
        debug_callback,
        "HASH_START",
        {
            "phase": "Calcolo hash",
            "message": f"Inizio calcolo hash {source_label}",
            "bytes_processed": 0,
        },
    )
    try:
        plan = parse_audio_hash_plan(
            data,
            cancel_event=cancel_event,
            debug_callback=debug_callback,
            source_label=source_label,
        )
    except AudioHashCancelled:
        _emit_debug(debug_callback, f"[HASH] Cancellato durante parsing regioni {source_label}")
        _emit_telemetry_event(
            debug_callback,
            "CANCEL_DETECTED",
            {
                "phase": "Parsing regioni escluse",
                "message": f"Cancellazione rilevata durante parsing regioni {source_label}",
                "cancel_requested": True,
            },
            critical=True,
        )
        return AudioHashResult(
            status=AudioHashStatus.CANCELLED,
            audio_hash_sha256=None,
            plan=AudioHashPlan(None, None, None, [], None, notes=["HASH_CANCELLED"]),
            frames_count=0,
            audio_bytes_hashed=0,
            first_frame_offset=None,
            last_frame_end_offset=None,
            anomalies=["HASH_CANCELLED"],
            non_audio_gaps=[],
            frame_sequence=[],
        )
    anomalies: list[str] = []

    skip_regions = [region for region in (plan.id3v2_region, plan.ape_region, plan.id3v1_region) if region is not None]
    skip_regions.extend(plan.winlive_regions)
    intervals = _build_candidate_intervals(len(data), skip_regions)
    interval_start = intervals[0].start if intervals else 0
    interval_end = intervals[-1].end if intervals else 0

    chains: list[list[MpegFrame]] = []
    scan_parse_calls = 0
    scan_unique_offsets = 0
    scan_cache_hits = 0
    scan_cache_misses = 0
    scan_outer_iterations = 0
    scan_inner_iterations = 0
    scan_frames_found = 0
    scan_frames_valid = 0
    scan_frames_rejected = 0
    scan_started_at = time.monotonic()
    _emit_debug(debug_callback, f"[TECH] Fase -> Scansione frame MPEG")
    _emit_debug(debug_callback, f"[HASH] Scanner MPEG avviato {source_label}")
    _emit_debug(debug_callback, f"[HASH] Dimensione file {source_label} | bytes={len(data)}")
    _emit_debug(debug_callback, f"[HASH] Offset iniziale {source_label} | offset={interval_start}")
    _emit_debug(debug_callback, f"[HASH] Offset finale {source_label} | offset={interval_end}")
    _emit_debug(debug_callback, f"[HASH] Numero intervalli {source_label} | intervalli={len(intervals)}")
    _emit_debug(
        debug_callback,
        f"[HASH] Inizio scansione frame MPEG {source_label} | intervalli={len(intervals)}",
    )
    _emit_telemetry_event(
        debug_callback,
        "MPEG_SCAN_START",
        {
            "phase": "Scansione frame MPEG",
            "message": f"Scanner MPEG avviato {source_label}",
            "offset": interval_start,
            "previous_offset": interval_start,
            "next_offset": interval_start,
            "frame_length": 0,
            "bytes_processed": 0,
            "percent": 0.0,
            "speed_mb_s": 0.0,
            "outer_iteration": 0,
            "inner_iteration": 0,
            "frames_found": 0,
            "frames_valid": 0,
            "frames_rejected": 0,
        },
        critical=True,
    )
    try:
        for interval in intervals:
            _check_cancel(cancel_event)
            interval_chains, interval_anomalies, interval_stats = _scan_mpeg_frames(
                data,
                interval.start,
                interval.end,
                cancel_event=cancel_event,
                debug_callback=debug_callback,
                source_label=source_label,
            )
            anomalies.extend(interval_anomalies)
            chains.extend(interval_chains)
            scan_parse_calls += interval_stats.parse_calls
            scan_unique_offsets += interval_stats.unique_offsets
            scan_cache_hits += interval_stats.cache_hits
            scan_cache_misses += interval_stats.cache_misses
            scan_outer_iterations += interval_stats.outer_iterations
            scan_inner_iterations += interval_stats.inner_iterations
            scan_frames_found += interval_stats.frames_found
            scan_frames_valid += interval_stats.frames_valid
            scan_frames_rejected += interval_stats.frames_rejected
    except AudioHashCancelled:
        scan_elapsed = max(0.0, time.monotonic() - scan_started_at)
        _emit_debug(debug_callback, f"[HASH] Cancellato durante scansione frame {source_label}")
        _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame=0 | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
        return AudioHashResult(
            status=AudioHashStatus.CANCELLED,
            audio_hash_sha256=None,
            plan=plan,
            frames_count=0,
            audio_bytes_hashed=0,
            first_frame_offset=None,
            last_frame_end_offset=None,
            anomalies=anomalies + ["HASH_CANCELLED"],
            non_audio_gaps=[],
            frame_sequence=[],
        )

    scan_elapsed = max(0.0, time.monotonic() - scan_started_at)
    scan_speed_mb_s = 0.0 if scan_elapsed <= 0 else ((max(0, interval_end - interval_start) / (1024 * 1024)) / scan_elapsed)
    _emit_debug(
        debug_callback,
        f"[HASH] Scanner completato in {scan_elapsed:.3f} s | {scan_speed_mb_s:.3f} MB/s | frame {scan_frames_valid} | parse calls {scan_parse_calls}",
    )
    _emit_debug(debug_callback, f"[HASH] Fine scansione frame MPEG {source_label} | catene={len(chains)}")

    if not chains:
        _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame=0 | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
        return AudioHashResult(
            status=AudioHashStatus.NO_AUDIO_STREAM,
            audio_hash_sha256=None,
            plan=plan,
            frames_count=0,
            audio_bytes_hashed=0,
            first_frame_offset=None,
            last_frame_end_offset=None,
            anomalies=anomalies,
            non_audio_gaps=[],
            frame_sequence=[],
        )

    ranked = sorted(chains, key=lambda chain: (len(chain), sum(frame.length for frame in chain)), reverse=True)
    best = ranked[0]
    ties = [chain for chain in ranked if len(chain) == len(best) and sum(frame.length for frame in chain) == sum(frame.length for frame in best)]

    if len(ties) > 1:
        _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame={len(best)} | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
        return AudioHashResult(
            status=AudioHashStatus.AMBIGUOUS_AUDIO_STREAM,
            audio_hash_sha256=None,
            plan=plan,
            frames_count=len(best),
            audio_bytes_hashed=sum(frame.length for frame in best),
            first_frame_offset=best[0].offset,
            last_frame_end_offset=best[-1].offset + best[-1].length,
            anomalies=anomalies + ["MULTIPLE_CHAINS_WITH_EQUAL_SCORE"],
            non_audio_gaps=_compute_non_audio_gaps(best),
            frame_sequence=best,
        )

    if len(best) < min_chain_frames:
        try:
            hash_started_at = time.monotonic()
            partial_hash = _hash_chain(
                data,
                best,
                cancel_event=cancel_event,
                debug_callback=debug_callback,
                source_label=source_label,
            )
            hash_elapsed = max(0.0, time.monotonic() - hash_started_at)
        except AudioHashCancelled:
            hash_elapsed = max(0.0, time.monotonic() - hash_started_at)
            _emit_debug(debug_callback, f"[HASH] Cancellato durante SHA-256 parziale {source_label}")
            _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame={len(best)} | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
            _emit_telemetry_event(
                debug_callback,
                "CANCEL_DETECTED",
                {
                    "phase": "SHA-256",
                    "message": f"Cancellazione rilevata durante SHA-256 parziale {source_label}",
                    "cancel_requested": True,
                },
                critical=True,
            )
            return AudioHashResult(
                status=AudioHashStatus.CANCELLED,
                audio_hash_sha256=None,
                plan=plan,
                frames_count=len(best),
                audio_bytes_hashed=sum(frame.length for frame in best),
                first_frame_offset=best[0].offset,
                last_frame_end_offset=best[-1].offset + best[-1].length,
                anomalies=anomalies + ["HASH_CANCELLED"],
                non_audio_gaps=_compute_non_audio_gaps(best),
                frame_sequence=best,
            )
        _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame={len(best)} | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
        return AudioHashResult(
            status=AudioHashStatus.PARTIAL_AUDIO_STREAM,
            audio_hash_sha256=partial_hash,
            plan=plan,
            frames_count=len(best),
            audio_bytes_hashed=sum(frame.length for frame in best),
            first_frame_offset=best[0].offset,
            last_frame_end_offset=best[-1].offset + best[-1].length,
            anomalies=anomalies + ["CHAIN_TOO_SHORT"],
            non_audio_gaps=_compute_non_audio_gaps(best),
            frame_sequence=best,
        )

    try:
        hash_started_at = time.monotonic()
        final_hash = _hash_chain(
            data,
            best,
            cancel_event=cancel_event,
            debug_callback=debug_callback,
            source_label=source_label,
        )
        hash_elapsed = max(0.0, time.monotonic() - hash_started_at)
    except AudioHashCancelled:
        hash_elapsed = max(0.0, time.monotonic() - hash_started_at)
        _emit_debug(debug_callback, f"[HASH] Cancellato durante SHA-256 finale {source_label}")
        _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame={len(best)} | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
        _emit_telemetry_event(
            debug_callback,
            "CANCEL_DETECTED",
            {
                "phase": "SHA-256",
                "message": f"Cancellazione rilevata durante SHA-256 finale {source_label}",
                "cancel_requested": True,
            },
            critical=True,
        )
        return AudioHashResult(
            status=AudioHashStatus.CANCELLED,
            audio_hash_sha256=None,
            plan=plan,
            frames_count=len(best),
            audio_bytes_hashed=sum(frame.length for frame in best),
            first_frame_offset=best[0].offset,
            last_frame_end_offset=best[-1].offset + best[-1].length,
            anomalies=anomalies + ["HASH_CANCELLED"],
            non_audio_gaps=_compute_non_audio_gaps(best),
            frame_sequence=best,
        )

    elapsed = max(0.0, time.monotonic() - started_at)
    _emit_debug(debug_callback, f"[HASH] Scanner terminato {source_label} | numero_frame={len(best)} | tempo_scansione_s={scan_elapsed:.3f} | tempo_hash_s={hash_elapsed:.3f}")
    _emit_telemetry_event(
        debug_callback,
        "HASH_END",
        {
            "phase": "Calcolo hash",
            "message": f"Fine calcolo hash {source_label}",
            "frames_found": len(best),
            "frames_valid": len(best),
            "frames_rejected": len(anomalies),
            "audio_bytes_hashed": sum(frame.length for frame in best),
            "hash_total_elapsed_seconds": elapsed,
            "scan_elapsed_seconds": scan_elapsed,
            "sha_elapsed_seconds": hash_elapsed,
            "parse_calls_total": scan_parse_calls,
            "unique_offsets_total": scan_unique_offsets,
            "parse_cache_hits": scan_cache_hits,
            "parse_cache_misses": scan_cache_misses,
            "scanner_outer_iterations": scan_outer_iterations,
            "scanner_inner_iterations": scan_inner_iterations,
            "scanner_average_speed_mb_s": scan_speed_mb_s,
            "bytes_processed": best[-1].offset + best[-1].length if best else 0,
            "offset": best[-1].offset + best[-1].length if best else 0,
            "speed_mb_s": 0.0 if elapsed <= 0 else (sum(frame.length for frame in best) / (1024 * 1024)) / elapsed,
        },
    )
    _emit_debug(
        debug_callback,
        f"[HASH] Fine calcolo {source_label} | frame={len(best)} | byte_audio={sum(frame.length for frame in best)} | durata_s={elapsed:.3f}",
    )
    return AudioHashResult(
        status=AudioHashStatus.VALID_AUDIO_STREAM,
        audio_hash_sha256=final_hash,
        plan=plan,
        frames_count=len(best),
        audio_bytes_hashed=sum(frame.length for frame in best),
        first_frame_offset=best[0].offset,
        last_frame_end_offset=best[-1].offset + best[-1].length,
        anomalies=anomalies,
        non_audio_gaps=_compute_non_audio_gaps(best),
        frame_sequence=best,
    )


def _detect_id3v2_region(data: bytes) -> ByteRegion | None:
    if len(data) < 10:
        return None
    if data[0:3] != b"ID3":
        return None
    # Synchsafe int (4 bytes)
    size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
    total = 10 + size
    total = min(total, len(data))
    return ByteRegion(start=0, end=total)


def _detect_id3v1_region(data: bytes) -> ByteRegion | None:
    if len(data) < 128:
        return None
    if data[-128:-125] != b"TAG":
        return None
    return ByteRegion(start=len(data) - 128, end=len(data))


def _detect_apev2_footer_region(data: bytes) -> ByteRegion | None:
    marker = b"APETAGEX"
    index = data.rfind(marker)
    if index < 0:
        return None
    if index + 16 > len(data):
        return None
    # APE tag size at bytes 12..15 little-endian from header/footer start
    size = int.from_bytes(data[index + 12 : index + 16], byteorder="little", signed=False)
    if size <= 0:
        return None
    start = max(0, index - max(0, size - 32))
    end = min(len(data), index + 32)
    return ByteRegion(start=start, end=end)


def _detect_winlive_regions(data: bytes) -> list[ByteRegion]:
    parsed = parse_winlive_blocks_strict(data)
    regions: list[ByteRegion] = []

    if (
        parsed.synct.state == WinLiveStructureState.VALID
        and parsed.synct.open_offset is not None
        and parsed.synct.close_offset is not None
    ):
        regions.append(
            ByteRegion(
                start=parsed.synct.open_offset,
                end=parsed.synct.close_offset + len(TAG_SYNCT_CLOSE),
            )
        )

    if (
        parsed.chord.state == WinLiveStructureState.VALID
        and parsed.chord.open_offset is not None
        and parsed.chord.close_offset is not None
    ):
        regions.append(
            ByteRegion(
                start=parsed.chord.open_offset,
                end=parsed.chord.close_offset + len(TAG_CHORD_CLOSE),
            )
        )

    return regions


def _scan_mpeg_frames(
    data: bytes,
    start: int,
    end: int,
    *,
    cancel_event: object | None = None,
    debug_callback: AudioHashDebugLog | None = None,
    source_label: str = "",
) -> tuple[list[list[MpegFrame]], list[str], MpegScanStats]:
    chains: dict[tuple[int, int, int], list[MpegFrame]] = {}
    anomalies: list[str] = []
    offset = max(0, start)
    limit = min(len(data), end)
    scan_origin = offset
    scan_started_at = time.monotonic()
    outer_iterations = 0
    max_outer_iterations = max(1, (limit - offset) + 1)
    frames_found = 0
    frames_valid = 0
    frames_discarded = 0
    last_next_offset = offset
    last_frame_length = 0
    previous_offset = offset
    total_inner_iterations = 0
    last_heartbeat_at = scan_started_at - 1.0
    parse_cache: dict[int, tuple[MpegFrame | None, str | None]] = {}
    parse_cache_hits = 0
    parse_cache_misses = 0

    def _parse_with_cache(target_offset: int) -> tuple[MpegFrame | None, str | None]:
        nonlocal parse_cache_hits, parse_cache_misses
        cached = parse_cache.get(target_offset)
        if cached is not None:
            parse_cache_hits += 1
            return cached
        parsed = _parse_frame_at(data, target_offset)
        parse_cache[target_offset] = parsed
        parse_cache_misses += 1
        return parsed

    def _emit_heartbeat(force: bool = False) -> None:
        nonlocal last_heartbeat_at
        now = time.monotonic()
        if not force and (now - last_heartbeat_at) < 1.0:
            return
        elapsed = max(0.0, now - scan_started_at)
        bytes_processed = max(0, offset - scan_origin)
        interval_size = max(1, limit - scan_origin)
        percent = min(100.0, max(0.0, (bytes_processed / float(interval_size)) * 100.0))
        speed_mb_s = 0.0 if elapsed <= 0 else (bytes_processed / (1024 * 1024)) / elapsed
        cancel_requested = bool(cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set())
        _emit_telemetry_event(
            debug_callback,
            "MPEG_SCAN_HEARTBEAT",
            {
                "phase": "Scansione frame MPEG",
                "message": "Heartbeat scanner MPEG",
                "offset": offset,
                "previous_offset": previous_offset,
                "next_offset": last_next_offset,
                "frame_length": last_frame_length,
                "outer_iteration": outer_iterations,
                "inner_iteration": total_inner_iterations,
                "parse_calls_total": parse_cache_misses,
                "unique_offsets_total": len(parse_cache),
                "parse_cache_hits": parse_cache_hits,
                "parse_cache_misses": parse_cache_misses,
                "frames_found": frames_found,
                "frames_valid": frames_valid,
                "frames_rejected": frames_discarded,
                "bytes_processed": bytes_processed,
                "percent": percent,
                "speed_mb_s": speed_mb_s,
                "cancel_requested": cancel_requested,
                "thread_id": threading.get_ident(),
                "monotonic_elapsed": elapsed,
            },
        )
        last_heartbeat_at = now

    while offset + 4 <= limit:
        try:
            _check_cancel(cancel_event)
        except AudioHashCancelled:
            _emit_heartbeat(force=True)
            _emit_telemetry_event(
                debug_callback,
                "CANCEL_DETECTED",
                {
                    "phase": "Interrotto",
                    "last_phase": "Scansione frame MPEG",
                    "message": f"Cancellazione rilevata durante scansione frame {source_label}",
                    "offset": offset,
                    "previous_offset": previous_offset,
                    "next_offset": last_next_offset,
                    "frame_length": last_frame_length,
                    "outer_iteration": outer_iterations,
                    "inner_iteration": total_inner_iterations,
                    "parse_calls_total": parse_cache_misses,
                    "unique_offsets_total": len(parse_cache),
                    "parse_cache_hits": parse_cache_hits,
                    "parse_cache_misses": parse_cache_misses,
                    "frames_found": frames_found,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_discarded,
                    "bytes_processed": max(0, offset - scan_origin),
                    "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                    "speed_mb_s": 0.0 if (time.monotonic() - scan_started_at) <= 0 else ((max(0, offset - scan_origin) / (1024 * 1024)) / max(0.000001, time.monotonic() - scan_started_at)),
                    "cancel_requested": True,
                    "thread_id": threading.get_ident(),
                    "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                },
                critical=True,
            )
            raise
        outer_iterations += 1
        _emit_heartbeat()
        if outer_iterations > max_outer_iterations:
            anomalies.append(f"OUTER_SCAN_GUARD_TRIGGERED_{offset}")
            _emit_debug(debug_callback, f"[HASH] Limite massimo iterazioni raggiunto {source_label} | outer_iteration={outer_iterations} | offset={offset}")
            _emit_telemetry_event(
                debug_callback,
                "ERROR",
                {
                    "phase": "Scansione frame MPEG",
                    "message": "Limite massimo iterazioni raggiunto nel ciclo esterno.",
                    "offset": offset,
                    "previous_offset": previous_offset,
                    "next_offset": last_next_offset,
                    "frame_length": last_frame_length,
                    "outer_iteration": outer_iterations,
                    "inner_iteration": total_inner_iterations,
                    "frames_found": frames_found,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_discarded,
                    "bytes_processed": max(0, offset - scan_origin),
                    "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                    "speed_mb_s": 0.0,
                    "thread_id": threading.get_ident(),
                },
                critical=True,
            )
            break

        frame, parse_anomaly = _parse_with_cache(offset)
        if parse_anomaly is not None:
            anomalies.append(parse_anomaly)
            frames_discarded += 1
        if frame is None:
            previous_offset = offset
            offset += 1
            if offset <= previous_offset:
                _emit_debug(debug_callback, f"[HASH] OFFSET_NON_AVANZA {source_label} | old_offset={previous_offset} | new_offset={offset} | frame_length={last_frame_length} | next_offset={last_next_offset}")
                _emit_telemetry_event(
                    debug_callback,
                    "NON_PROGRESS",
                    {
                        "phase": "Scansione frame MPEG",
                        "message": "OFFSET_NON_AVANZA",
                        "offset": offset,
                        "previous_offset": previous_offset,
                        "next_offset": last_next_offset,
                        "frame_length": last_frame_length,
                        "outer_iteration": outer_iterations,
                        "inner_iteration": total_inner_iterations,
                        "frames_found": frames_found,
                        "frames_valid": frames_valid,
                        "frames_rejected": frames_discarded,
                        "bytes_processed": max(0, offset - scan_origin),
                        "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                        "speed_mb_s": 0.0,
                        "cancel_requested": False,
                        "thread_id": threading.get_ident(),
                        "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                    },
                    critical=True,
                )
            continue

        frames_found += 1
        last_frame_length = frame.length
        if frame.length <= 0:
            anomalies.append(f"NON_PROGRESSIVE_FRAME_LEN_{offset}")
            frames_discarded += 1
            _emit_debug(debug_callback, f"[HASH] Frame non valido {source_label} | offset={offset} | frame_length={frame.length} | motivo=FRAME_LENGTH_NON_VALIDO")
            previous_offset = offset
            offset += 1
            _emit_telemetry_event(
                debug_callback,
                "NON_PROGRESS",
                {
                    "phase": "Scansione frame MPEG",
                    "message": "FRAME_LENGTH_NON_VALIDO",
                    "offset": offset,
                    "previous_offset": previous_offset,
                    "next_offset": last_next_offset,
                    "frame_length": frame.length,
                    "outer_iteration": outer_iterations,
                    "inner_iteration": total_inner_iterations,
                    "frames_found": frames_found,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_discarded,
                    "bytes_processed": max(0, offset - scan_origin),
                    "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                    "speed_mb_s": 0.0,
                    "thread_id": threading.get_ident(),
                    "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                },
                critical=True,
            )
            continue

        chain = [frame]
        next_offset = frame.offset + frame.length
        last_next_offset = next_offset
        if next_offset <= offset:
            anomalies.append(f"NON_PROGRESSIVE_NEXT_OFFSET_{offset}_{next_offset}")
            frames_discarded += 1
            _emit_debug(debug_callback, f"[HASH] Frame non valido {source_label} | offset={offset} | frame_length={frame.length} | motivo=NEXT_OFFSET_NON_PROGRESSIVO")
            _emit_telemetry_event(
                debug_callback,
                "NON_PROGRESS",
                {
                    "phase": "Scansione frame MPEG",
                    "message": "NEXT_OFFSET_NON_PROGRESSIVO",
                    "offset": offset,
                    "previous_offset": previous_offset,
                    "next_offset": next_offset,
                    "frame_length": frame.length,
                    "outer_iteration": outer_iterations,
                    "inner_iteration": total_inner_iterations,
                    "frames_found": frames_found,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_discarded,
                    "bytes_processed": max(0, offset - scan_origin),
                    "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                    "speed_mb_s": 0.0,
                    "thread_id": threading.get_ident(),
                    "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                },
                critical=True,
            )
            previous_offset = offset
            offset += 1
            continue

        inner_iterations = 0
        while next_offset + 4 <= limit:
            try:
                _check_cancel(cancel_event)
            except AudioHashCancelled:
                _emit_heartbeat(force=True)
                _emit_telemetry_event(
                    debug_callback,
                    "CANCEL_DETECTED",
                    {
                        "phase": "Interrotto",
                        "last_phase": "Scansione frame MPEG",
                        "message": f"Cancellazione rilevata durante scansione frame {source_label}",
                        "offset": offset,
                        "previous_offset": previous_offset,
                        "next_offset": next_offset,
                        "frame_length": last_frame_length,
                        "outer_iteration": outer_iterations,
                        "inner_iteration": total_inner_iterations,
                        "parse_calls_total": parse_cache_misses,
                        "unique_offsets_total": len(parse_cache),
                        "parse_cache_hits": parse_cache_hits,
                        "parse_cache_misses": parse_cache_misses,
                        "frames_found": frames_found,
                        "frames_valid": frames_valid,
                        "frames_rejected": frames_discarded,
                        "bytes_processed": max(0, offset - scan_origin),
                        "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                        "speed_mb_s": 0.0 if (time.monotonic() - scan_started_at) <= 0 else ((max(0, offset - scan_origin) / (1024 * 1024)) / max(0.000001, time.monotonic() - scan_started_at)),
                        "cancel_requested": True,
                        "thread_id": threading.get_ident(),
                        "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                    },
                    critical=True,
                )
                raise
            inner_iterations += 1
            total_inner_iterations += 1
            _emit_heartbeat()

            next_frame, next_anomaly = _parse_with_cache(next_offset)
            if next_anomaly is not None:
                anomalies.append(next_anomaly)
                frames_discarded += 1
            if next_frame is None:
                break
            if next_frame.length <= 0:
                anomalies.append(f"NON_PROGRESSIVE_CHAIN_FRAME_LEN_{next_offset}")
                frames_discarded += 1
                _emit_debug(debug_callback, f"[HASH] Frame non valido {source_label} | offset={next_offset} | frame_length={next_frame.length} | motivo=FRAME_LENGTH_NON_VALIDO")
                _emit_telemetry_event(
                    debug_callback,
                    "NON_PROGRESS",
                    {
                        "phase": "Scansione frame MPEG",
                        "message": "FRAME_LENGTH_NON_VALIDO",
                        "offset": offset,
                        "previous_offset": previous_offset,
                        "next_offset": next_offset,
                        "frame_length": next_frame.length,
                        "outer_iteration": outer_iterations,
                        "inner_iteration": total_inner_iterations,
                        "frames_found": frames_found,
                        "frames_valid": frames_valid,
                        "frames_rejected": frames_discarded,
                        "bytes_processed": max(0, offset - scan_origin),
                        "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                        "speed_mb_s": 0.0,
                        "thread_id": threading.get_ident(),
                        "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                    },
                    critical=True,
                )
                break
            if not _is_chain_compatible(chain[-1], next_frame):
                break
            chain.append(next_frame)
            candidate_next_offset = next_frame.offset + next_frame.length
            last_frame_length = next_frame.length
            last_next_offset = candidate_next_offset
            if candidate_next_offset <= next_offset:
                anomalies.append(f"NON_PROGRESSIVE_CHAIN_OFFSET_{next_offset}_{candidate_next_offset}")
                frames_discarded += 1
                _emit_debug(debug_callback, f"[HASH] Frame non valido {source_label} | offset={next_offset} | frame_length={next_frame.length} | motivo=NEXT_OFFSET_NON_PROGRESSIVO")
                _emit_telemetry_event(
                    debug_callback,
                    "NON_PROGRESS",
                    {
                        "phase": "Scansione frame MPEG",
                        "message": "NEXT_OFFSET_NON_PROGRESSIVO",
                        "offset": offset,
                        "previous_offset": previous_offset,
                        "next_offset": candidate_next_offset,
                        "frame_length": next_frame.length,
                        "outer_iteration": outer_iterations,
                        "inner_iteration": total_inner_iterations,
                        "frames_found": frames_found,
                        "frames_valid": frames_valid,
                        "frames_rejected": frames_discarded,
                        "bytes_processed": max(0, offset - scan_origin),
                        "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                        "speed_mb_s": 0.0,
                        "thread_id": threading.get_ident(),
                        "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                    },
                    critical=True,
                )
                break
            next_offset = candidate_next_offset

        key = (chain[0].offset, chain[-1].offset + chain[-1].length, len(chain))
        chains[key] = chain
        frames_valid += len(chain)

        previous_offset = offset
        new_offset = chain[-1].offset + chain[-1].length
        if new_offset <= offset:
            anomalies.append(f"OUTER_OFFSET_NOT_ADVANCING_{offset}_{new_offset}")
            frames_discarded += 1
            _emit_debug(debug_callback, f"[HASH] Frame non valido {source_label} | offset={offset} | frame_length={frame.length} | motivo=OFFSET_NON_AVANZA")
            offset += 1
            _emit_telemetry_event(
                debug_callback,
                "NON_PROGRESS",
                {
                    "phase": "Scansione frame MPEG",
                    "message": "OFFSET_NON_AVANZA",
                    "offset": offset,
                    "previous_offset": previous_offset,
                    "next_offset": next_offset,
                    "frame_length": frame.length,
                    "outer_iteration": outer_iterations,
                    "inner_iteration": total_inner_iterations,
                    "frames_found": frames_found,
                    "frames_valid": frames_valid,
                    "frames_rejected": frames_discarded,
                    "bytes_processed": max(0, offset - scan_origin),
                    "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
                    "speed_mb_s": 0.0,
                    "thread_id": threading.get_ident(),
                    "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
                },
                critical=True,
            )
        else:
            offset = new_offset

    _emit_telemetry_event(
        debug_callback,
        "MPEG_SCAN_END",
        {
            "phase": "Scansione frame MPEG",
            "message": f"Scanner MPEG terminato {source_label}",
            "offset": offset,
            "previous_offset": previous_offset,
            "next_offset": last_next_offset,
            "frame_length": last_frame_length,
            "outer_iteration": outer_iterations,
            "inner_iteration": total_inner_iterations,
            "parse_calls_total": parse_cache_misses,
            "unique_offsets_total": len(parse_cache),
            "parse_cache_hits": parse_cache_hits,
            "parse_cache_misses": parse_cache_misses,
            "frames_found": frames_found,
            "frames_valid": frames_valid,
            "frames_rejected": frames_discarded,
            "bytes_processed": max(0, offset - scan_origin),
            "percent": min(100.0, max(0.0, (max(0, offset - scan_origin) / float(max(1, limit - scan_origin))) * 100.0)),
            "speed_mb_s": 0.0 if (time.monotonic() - scan_started_at) <= 0 else ((max(0, offset - scan_origin) / (1024 * 1024)) / max(0.000001, time.monotonic() - scan_started_at)),
            "thread_id": threading.get_ident(),
            "monotonic_elapsed": max(0.0, time.monotonic() - scan_started_at),
        },
    )

    elapsed = max(0.0, time.monotonic() - scan_started_at)
    stats = MpegScanStats(
        parse_calls=parse_cache_misses,
        unique_offsets=len(parse_cache),
        cache_hits=parse_cache_hits,
        cache_misses=parse_cache_misses,
        outer_iterations=outer_iterations,
        inner_iterations=total_inner_iterations,
        frames_found=frames_found,
        frames_valid=frames_valid,
        frames_rejected=frames_discarded,
        scanner_elapsed_seconds=elapsed,
        average_speed_mb_s=0.0 if elapsed <= 0 else ((max(0, offset - scan_origin) / (1024 * 1024)) / elapsed),
    )

    return list(chains.values()), anomalies, stats


def _build_candidate_intervals(total_len: int, skipped: list[ByteRegion]) -> list[ByteRegion]:
    if total_len <= 0:
        return []

    normalized = _merge_regions(skipped, total_len)
    intervals: list[ByteRegion] = []
    cursor = 0
    for region in normalized:
        if cursor < region.start:
            intervals.append(ByteRegion(start=cursor, end=region.start))
        cursor = max(cursor, region.end)
    if cursor < total_len:
        intervals.append(ByteRegion(start=cursor, end=total_len))
    return [region for region in intervals if region.end - region.start >= 4]


def _merge_regions(regions: list[ByteRegion], total_len: int) -> list[ByteRegion]:
    if not regions:
        return []

    valid = [ByteRegion(start=max(0, region.start), end=min(total_len, region.end)) for region in regions if region.end > region.start]
    if not valid:
        return []

    valid.sort(key=lambda region: (region.start, region.end))
    merged = [valid[0]]
    for region in valid[1:]:
        current = merged[-1]
        if region.start <= current.end:
            current.end = max(current.end, region.end)
        else:
            merged.append(ByteRegion(start=region.start, end=region.end))
    return merged


def _hash_chain(
    data: bytes,
    chain: list[MpegFrame],
    *,
    cancel_event: object | None = None,
    debug_callback: AudioHashDebugLog | None = None,
    source_label: str = "",
) -> str:
    digest = hashlib.sha256()
    processed_bytes = 0
    last_debug_bytes = 0
    for frame in chain:
        _check_cancel(cancel_event)
        digest.update(data[frame.offset : frame.offset + frame.length])
        processed_bytes += frame.length
        if processed_bytes - last_debug_bytes >= 5 * 1024 * 1024:
            _emit_debug(debug_callback, f"[HASH] SHA avanzamento {source_label} | byte_audio={processed_bytes}")
            last_debug_bytes = processed_bytes
    _emit_debug(debug_callback, f"[HASH] Fine SHA-256 {source_label} | byte_audio={processed_bytes}")
    return digest.hexdigest()


def _check_cancel(cancel_event: object | None) -> None:
    if cancel_event is not None and hasattr(cancel_event, "is_set") and bool(cancel_event.is_set()):
        raise AudioHashCancelled("HASH_CANCELLED")


def _emit_debug(callback: AudioHashDebugLog | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _emit_telemetry_event(callback: AudioHashDebugLog | None, event_type: str, payload: dict[str, object], critical: bool = False) -> None:
    if callback is not None and hasattr(callback, "telemetry_event"):
        callback.telemetry_event(event_type, payload, critical=critical)


def _compute_non_audio_gaps(chain: list[MpegFrame]) -> list[ByteRegion]:
    if not chain:
        return []

    gaps: list[ByteRegion] = []
    for index in range(1, len(chain)):
        prev = chain[index - 1]
        cur = chain[index]
        prev_end = prev.offset + prev.length
        if cur.offset > prev_end:
            gaps.append(ByteRegion(start=prev_end, end=cur.offset))
    return gaps


def _is_chain_compatible(previous: MpegFrame, current: MpegFrame) -> bool:
    if previous.offset + previous.length != current.offset:
        return False
    if previous.version != current.version:
        return False
    if previous.layer != current.layer:
        return False
    if previous.sample_rate_hz != current.sample_rate_hz:
        return False
    return True

    return frames


def _parse_frame_at(data: bytes, offset: int) -> tuple[MpegFrame | None, str | None]:
    if offset + 4 > len(data):
        return None, None

    b1, b2, b3 = data[offset], data[offset + 1], data[offset + 2]
    if b1 != 0xFF or (b2 & 0xE0) != 0xE0:
        return None, None

    version_bits = (b2 >> 3) & 0x03
    layer_bits = (b2 >> 1) & 0x03
    bitrate_index = (b3 >> 4) & 0x0F
    sample_index = (b3 >> 2) & 0x03
    padding = (b3 >> 1) & 0x01
    has_crc = (b2 & 0x01) == 0

    if version_bits == 0x01 or layer_bits == 0x00:
        return None, None
    if bitrate_index in (0x00, 0x0F) or sample_index == 0x03:
        return None, None

    bitrate = _lookup_bitrate_kbps(version_bits, layer_bits, bitrate_index)
    sample_rate = _lookup_sample_rate_hz(version_bits, sample_index)
    if bitrate <= 0 or sample_rate <= 0:
        return None, None

    layer_number = _layer_number(layer_bits)
    if layer_number == 1:
        frame_len = int(((12 * bitrate * 1000) // sample_rate + padding) * 4)
    elif layer_number == 2:
        frame_len = int((144 * bitrate * 1000) // sample_rate + padding)
    else:
        if version_bits == 0x03:
            frame_len = int((144 * bitrate * 1000) // sample_rate + padding)
        else:
            frame_len = int((72 * bitrate * 1000) // sample_rate + padding)

    if frame_len <= 0:
        return None, None

    if offset + frame_len > len(data):
        return None, f"TRUNCATED_FRAME_AT_{offset}"

    version_name = _version_name(version_bits)
    layer_name = _layer_name(layer_bits)

    return (
        MpegFrame(
            offset=offset,
            length=frame_len,
            bitrate_kbps=bitrate,
            sample_rate_hz=sample_rate,
            version=version_name,
            layer=layer_name,
            padding=padding,
            has_crc=has_crc,
        ),
        None,
    )


def _layer_number(layer_bits: int) -> int:
    # 11 -> Layer I, 10 -> Layer II, 01 -> Layer III
    if layer_bits == 0x03:
        return 1
    if layer_bits == 0x02:
        return 2
    return 3


def _version_name(version_bits: int) -> str:
    if version_bits == 0x03:
        return "MPEG1"
    if version_bits == 0x02:
        return "MPEG2"
    return "MPEG2.5"


def _layer_name(layer_bits: int) -> str:
    if layer_bits == 0x03:
        return "LayerI"
    if layer_bits == 0x02:
        return "LayerII"
    return "LayerIII"


def _lookup_sample_rate_hz(version_bits: int, sample_index: int) -> int:
    # version_bits: 11 MPEG1, 10 MPEG2, 00 MPEG2.5
    table = {
        0x03: [44100, 48000, 32000],
        0x02: [22050, 24000, 16000],
        0x00: [11025, 12000, 8000],
    }
    values = table.get(version_bits)
    if values is None:
        return 0
    return values[sample_index]


def _lookup_bitrate_kbps(version_bits: int, layer_bits: int, bitrate_index: int) -> int:
    # Tables indexed by bitrate_index (1..14)
    mpeg1_l1 = [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448]
    mpeg1_l2 = [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384]
    mpeg1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    mpeg2_l1 = [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256]
    mpeg2_l23 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]

    if bitrate_index <= 0 or bitrate_index >= len(mpeg1_l1):
        return 0

    if version_bits == 0x03:
        if layer_bits == 0x03:
            return mpeg1_l1[bitrate_index]
        if layer_bits == 0x02:
            return mpeg1_l2[bitrate_index]
        return mpeg1_l3[bitrate_index]

    if layer_bits == 0x03:
        return mpeg2_l1[bitrate_index]
    return mpeg2_l23[bitrate_index]


def validate_normalized_winlive_file(
    *,
    original_data: bytes,
    candidate_data: bytes,
) -> WinLiveNormalizedFileValidationResult:
    parsed_before = parse_winlive_blocks_strict(original_data)
    parsed_after = parse_winlive_blocks_strict(candidate_data)

    before_synct_valid = parsed_before.synct.state == WinLiveStructureState.VALID
    after_synct_valid = parsed_after.synct.state == WinLiveStructureState.VALID
    before_chord_valid = parsed_before.chord.state == WinLiveStructureState.VALID
    after_chord_valid = parsed_after.chord.state == WinLiveStructureState.VALID

    if not after_synct_valid:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="SYNCT_MISSING_OR_INVALID_AFTER",
            reason_message="Blocco WL5SYNCT assente o non valido nel file risultante.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=False,
            initial_value_preserved=False,
        )

    if not after_chord_valid and before_chord_valid:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="CHORD_MISSING_OR_INVALID_AFTER",
            reason_message="Blocco WL5CHORD perso o non valido nel file risultante.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=False,
            initial_value_preserved=False,
        )

    before_synct_raw = parsed_before.synct.content_bytes or b""
    after_synct_raw = parsed_after.synct.content_bytes or b""
    before_chord_raw = parsed_before.chord.content_bytes or b""
    after_chord_raw = parsed_after.chord.content_bytes or b""

    before_text = _decode_lossy_for_validation(before_synct_raw)
    after_text = _decode_lossy_for_validation(after_synct_raw)

    before_initial = _extract_initial_prefix(before_text)
    after_initial = _extract_initial_prefix(after_text)
    initial_value_preserved = before_initial == after_initial if before_initial else True
    if before_initial and not initial_value_preserved:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="SYNCT_INITIAL_VALUE_CHANGED",
            reason_message="Valore iniziale WL5SYNCT alterato.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=False,
            initial_value_preserved=False,
        )

    before_has_terminator = before_text.rstrip().endswith("|0||")
    terminator_preserved = after_text.rstrip().endswith("|0||") if before_has_terminator else True
    if before_has_terminator and not terminator_preserved:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="SYNCT_TERMINATOR_MISSING",
            reason_message="Terminator finale '|0||/<WL5SYNCT>' non preservato nel blocco SYNCT.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=False,
            initial_value_preserved=True,
        )

    meaningful_before = extract_significant_text(before_text)
    meaningful_after = extract_significant_text(after_text)
    meaningful_equal = meaningful_before == meaningful_after
    if meaningful_before and not meaningful_after:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="MEANINGFUL_TEXT_LOST",
            reason_message="Perdita completa del testo significativo nel blocco WL5SYNCT.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=True,
            initial_value_preserved=True,
        )

    if not meaningful_equal:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="MEANINGFUL_TEXT_CHANGED",
            reason_message="Testo significativo WL5SYNCT alterato dalla normalizzazione.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=False,
            chord_unchanged=False,
            terminator_preserved=True,
            initial_value_preserved=True,
        )

    chord_unchanged = before_chord_raw == after_chord_raw
    if not chord_unchanged:
        return WinLiveNormalizedFileValidationResult(
            valid=False,
            reason_code="CHORD_CHANGED",
            reason_message="Blocco WL5CHORD alterato: deve rimanere byte-per-byte invariato.",
            synct_present_before=before_synct_valid,
            synct_present_after=after_synct_valid,
            chord_present_before=before_chord_valid,
            chord_present_after=after_chord_valid,
            meaningful_text_equal=True,
            chord_unchanged=False,
            terminator_preserved=True,
            initial_value_preserved=True,
        )

    return WinLiveNormalizedFileValidationResult(
        valid=True,
        reason_code="OK",
        reason_message="Validazione post-scrittura WinLive superata.",
        synct_present_before=before_synct_valid,
        synct_present_after=after_synct_valid,
        chord_present_before=before_chord_valid,
        chord_present_after=after_chord_valid,
        meaningful_text_equal=True,
        chord_unchanged=True,
        terminator_preserved=True,
        initial_value_preserved=True,
    )


def _decode_lossy_for_validation(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding, errors="strict")
        except UnicodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _extract_initial_prefix(text: str) -> str:
    separator_index = text.find("|")
    if separator_index <= 0:
        return ""
    prefix = text[: separator_index + 1]
    if not prefix[:-1].isdigit():
        return ""
    return prefix
