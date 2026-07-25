# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

from winlive_normalizer import extract_significant_text
from winlive_tags import TAG_CHORD_CLOSE, TAG_SYNCT_CLOSE, WinLiveStructureState, parse_winlive_blocks_strict


class AudioHashStatus(str, Enum):
    VALID_AUDIO_STREAM = "VALID_AUDIO_STREAM"
    PARTIAL_AUDIO_STREAM = "PARTIAL_AUDIO_STREAM"
    NO_AUDIO_STREAM = "NO_AUDIO_STREAM"
    AMBIGUOUS_AUDIO_STREAM = "AMBIGUOUS_AUDIO_STREAM"


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


def parse_audio_hash_plan(data: bytes) -> AudioHashPlan:
    id3v2 = _detect_id3v2_region(data)
    id3v1 = _detect_id3v1_region(data)
    ape = _detect_apev2_footer_region(data)
    winlive_regions = _detect_winlive_regions(data)

    start = id3v2.end if id3v2 is not None else 0
    end = len(data)
    if ape is not None:
        end = min(end, ape.start)
    if id3v1 is not None:
        end = min(end, id3v1.start)

    chains, _ = _scan_mpeg_frames(data, start, end)
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

    return AudioHashPlan(
        id3v2_region=id3v2,
        id3v1_region=id3v1,
        ape_region=ape,
        winlive_regions=winlive_regions,
        mpeg_frames_region=frame_region,
        skipped_regions=skipped,
        notes=notes,
    )


def compute_mpeg_audio_hash(data: bytes, min_chain_frames: int = 3) -> AudioHashResult:
    plan = parse_audio_hash_plan(data)
    anomalies: list[str] = []

    skip_regions = [region for region in (plan.id3v2_region, plan.ape_region, plan.id3v1_region) if region is not None]
    skip_regions.extend(plan.winlive_regions)
    intervals = _build_candidate_intervals(len(data), skip_regions)

    chains: list[list[MpegFrame]] = []
    for interval in intervals:
        interval_chains, interval_anomalies = _scan_mpeg_frames(data, interval.start, interval.end)
        anomalies.extend(interval_anomalies)
        chains.extend(interval_chains)

    if not chains:
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
        partial_hash = _hash_chain(data, best)
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

    return AudioHashResult(
        status=AudioHashStatus.VALID_AUDIO_STREAM,
        audio_hash_sha256=_hash_chain(data, best),
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


def _scan_mpeg_frames(data: bytes, start: int, end: int) -> tuple[list[list[MpegFrame]], list[str]]:
    chains: dict[tuple[int, int, int], list[MpegFrame]] = {}
    anomalies: list[str] = []
    offset = max(0, start)
    limit = min(len(data), end)

    while offset + 4 <= limit:
        frame, parse_anomaly = _parse_frame_at(data, offset)
        if parse_anomaly is not None:
            anomalies.append(parse_anomaly)
        if frame is None:
            offset += 1
            continue

        chain = [frame]
        next_offset = frame.offset + frame.length
        while next_offset + 4 <= limit:
            next_frame, next_anomaly = _parse_frame_at(data, next_offset)
            if next_anomaly is not None:
                anomalies.append(next_anomaly)
            if next_frame is None:
                break
            if not _is_chain_compatible(chain[-1], next_frame):
                break
            chain.append(next_frame)
            next_offset = next_frame.offset + next_frame.length

        key = (chain[0].offset, chain[-1].offset + chain[-1].length, len(chain))
        chains[key] = chain
        offset += 1

    return list(chains.values()), anomalies


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


def _hash_chain(data: bytes, chain: list[MpegFrame]) -> str:
    digest = hashlib.sha256()
    for frame in chain:
        digest.update(data[frame.offset : frame.offset + frame.length])
    return digest.hexdigest()


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
