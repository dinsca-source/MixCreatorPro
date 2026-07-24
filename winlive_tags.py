# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


TAG_SYNCT_OPEN = b"<WL5SYNCT>"
TAG_SYNCT_CLOSE = b"/<WL5SYNCT>"
TAG_CHORD_OPEN = b"<WL5CHORD>"
TAG_CHORD_CLOSE = b"/<WL5CHORD>"


class WinLiveTagKind(str, Enum):
    SYNCT = "SYNCT"
    CHORD = "CHORD"


class WinLiveStructureState(str, Enum):
    ABSENT = "ABSENT"
    VALID = "VALID"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"


class WinLiveAnomalyCode(str, Enum):
    MULTIPLE_OPENINGS = "MULTIPLE_OPENINGS"
    MULTIPLE_CLOSINGS = "MULTIPLE_CLOSINGS"
    MISSING_OPENING = "MISSING_OPENING"
    MISSING_CLOSING = "MISSING_CLOSING"
    INVALID_ORDER = "INVALID_ORDER"
    OVERLAP = "OVERLAP"
    NESTED = "NESTED"
    AMBIGUOUS_STRUCTURE = "AMBIGUOUS_STRUCTURE"


class StructuralRepairability(str, Enum):
    NOT_NEEDED = "NOT_NEEDED"
    REPAIRABLE = "REPAIRABLE"
    NOT_REPAIRABLE = "NOT_REPAIRABLE"


@dataclass(slots=True)
class WinLiveAnomaly:
    code: WinLiveAnomalyCode
    message: str
    tag_kind: WinLiveTagKind
    offsets: tuple[int, ...] = ()


@dataclass(slots=True)
class WinLiveTagBlock:
    kind: WinLiveTagKind
    open_offsets: list[int]
    close_offsets: list[int]
    state: WinLiveStructureState
    anomalies: list[WinLiveAnomaly] = field(default_factory=list)
    open_offset: int | None = None
    close_offset: int | None = None
    content_start: int | None = None
    content_end: int | None = None
    content_bytes: bytes | None = None


@dataclass(slots=True)
class WinLiveBinaryParseResult:
    synct: WinLiveTagBlock
    chord: WinLiveTagBlock
    anomalies: list[WinLiveAnomaly]
    prefix_bytes: bytes
    between_bytes: bytes
    trailing_bytes: bytes
    final_close_end_offset: int | None


@dataclass(slots=True)
class RepairCandidate:
    kind: WinLiveTagKind
    insert_at: int
    insert_bytes: bytes
    message: str


@dataclass(slots=True)
class StructuralRepairAssessment:
    kind: WinLiveTagKind
    status: StructuralRepairability
    candidates: list[RepairCandidate]
    note: str


def parse_winlive_blocks_strict(data: bytes) -> WinLiveBinaryParseResult:
    synct = _parse_single_block(data, WinLiveTagKind.SYNCT, TAG_SYNCT_OPEN, TAG_SYNCT_CLOSE)
    chord = _parse_single_block(data, WinLiveTagKind.CHORD, TAG_CHORD_OPEN, TAG_CHORD_CLOSE)

    anomalies: list[WinLiveAnomaly] = []
    anomalies.extend(synct.anomalies)
    anomalies.extend(chord.anomalies)

    if synct.state == WinLiveStructureState.VALID and chord.state == WinLiveStructureState.VALID:
        syn_o = int(synct.open_offset or 0)
        syn_c = int(synct.close_offset or 0)
        cho_o = int(chord.open_offset or 0)
        cho_c = int(chord.close_offset or 0)
        if not (syn_c < cho_o or cho_c < syn_o):
            anomaly = WinLiveAnomaly(
                code=WinLiveAnomalyCode.OVERLAP,
                message="I blocchi WL5SYNCT e WL5CHORD risultano sovrapposti.",
                tag_kind=WinLiveTagKind.SYNCT,
                offsets=(syn_o, syn_c, cho_o, cho_c),
            )
            anomalies.append(anomaly)
            synct.state = WinLiveStructureState.INVALID_STRUCTURE
            chord.state = WinLiveStructureState.INVALID_STRUCTURE
            synct.anomalies.append(anomaly)
            chord.anomalies.append(anomaly)
            synct.content_bytes = None
            chord.content_bytes = None

    final_close_end = _final_close_end_offset(synct, chord)
    prefix = b""
    between = b""
    trailing = b""

    first_open = _first_open_offset(synct, chord)
    if first_open is not None:
        prefix = data[:first_open]

    if synct.state == WinLiveStructureState.VALID and chord.state == WinLiveStructureState.VALID:
        if synct.close_offset is not None and chord.open_offset is not None and synct.close_offset < chord.open_offset:
            between_start = synct.close_offset + len(TAG_SYNCT_CLOSE)
            between_end = chord.open_offset
            between = data[between_start:between_end]

    if final_close_end is not None:
        trailing = data[final_close_end:]

    return WinLiveBinaryParseResult(
        synct=synct,
        chord=chord,
        anomalies=anomalies,
        prefix_bytes=prefix,
        between_bytes=between,
        trailing_bytes=trailing,
        final_close_end_offset=final_close_end,
    )


def assess_structural_repairability(data: bytes, parsed: WinLiveBinaryParseResult) -> list[StructuralRepairAssessment]:
    return [
        _assess_block_repairability(data, parsed.synct, WinLiveTagKind.SYNCT, TAG_SYNCT_CLOSE[:-1]),
        _assess_block_repairability(data, parsed.chord, WinLiveTagKind.CHORD, TAG_CHORD_CLOSE[:-1]),
    ]


def _parse_single_block(data: bytes, kind: WinLiveTagKind, open_tag: bytes, close_tag: bytes) -> WinLiveTagBlock:
    open_offsets = _find_open_offsets(data, open_tag)
    close_offsets = _find_all_offsets(data, close_tag)
    anomalies: list[WinLiveAnomaly] = []

    if not open_offsets and not close_offsets:
        return WinLiveTagBlock(
            kind=kind,
            open_offsets=open_offsets,
            close_offsets=close_offsets,
            state=WinLiveStructureState.ABSENT,
            anomalies=anomalies,
        )

    state = WinLiveStructureState.VALID

    if len(open_offsets) == 0:
        state = WinLiveStructureState.INVALID_STRUCTURE
        anomalies.append(
            WinLiveAnomaly(
                code=WinLiveAnomalyCode.MISSING_OPENING,
                message=f"Tag di apertura {kind.value} mancante.",
                tag_kind=kind,
                offsets=tuple(close_offsets),
            )
        )
    if len(close_offsets) == 0:
        state = WinLiveStructureState.INVALID_STRUCTURE
        anomalies.append(
            WinLiveAnomaly(
                code=WinLiveAnomalyCode.MISSING_CLOSING,
                message=f"Tag di chiusura {kind.value} mancante.",
                tag_kind=kind,
                offsets=tuple(open_offsets),
            )
        )

    if len(open_offsets) > 1:
        state = WinLiveStructureState.INVALID_STRUCTURE
        anomalies.append(
            WinLiveAnomaly(
                code=WinLiveAnomalyCode.MULTIPLE_OPENINGS,
                message=f"Trovate aperture multiple per {kind.value}.",
                tag_kind=kind,
                offsets=tuple(open_offsets),
            )
        )

    if len(close_offsets) > 1:
        state = WinLiveStructureState.INVALID_STRUCTURE
        anomalies.append(
            WinLiveAnomaly(
                code=WinLiveAnomalyCode.MULTIPLE_CLOSINGS,
                message=f"Trovate chiusure multiple per {kind.value}.",
                tag_kind=kind,
                offsets=tuple(close_offsets),
            )
        )

    open_offset: int | None = None
    close_offset: int | None = None
    content_start: int | None = None
    content_end: int | None = None
    content_bytes: bytes | None = None

    if state == WinLiveStructureState.VALID:
        open_offset = open_offsets[0]
        close_offset = close_offsets[0]
        if close_offset <= open_offset:
            state = WinLiveStructureState.INVALID_STRUCTURE
            anomalies.append(
                WinLiveAnomaly(
                    code=WinLiveAnomalyCode.INVALID_ORDER,
                    message=f"Ordine non valido dei delimitatori {kind.value}.",
                    tag_kind=kind,
                    offsets=(open_offset, close_offset),
                )
            )
        else:
            content_start = open_offset + len(open_tag)
            content_end = close_offset
            content_bytes = data[content_start:content_end]

    if state == WinLiveStructureState.INVALID_STRUCTURE and not anomalies:
        anomalies.append(
            WinLiveAnomaly(
                code=WinLiveAnomalyCode.AMBIGUOUS_STRUCTURE,
                message=f"Struttura {kind.value} ambigua.",
                tag_kind=kind,
            )
        )

    return WinLiveTagBlock(
        kind=kind,
        open_offsets=open_offsets,
        close_offsets=close_offsets,
        state=state,
        anomalies=anomalies,
        open_offset=open_offset,
        close_offset=close_offset,
        content_start=content_start,
        content_end=content_end,
        content_bytes=content_bytes,
    )


def _find_all_offsets(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            break
        offsets.append(index)
        start = index + 1
    return offsets


def _find_open_offsets(data: bytes, open_tag: bytes) -> list[int]:
    all_offsets = _find_all_offsets(data, open_tag)
    result: list[int] = []
    for offset in all_offsets:
        if offset > 0 and data[offset - 1] == ord("/"):
            continue
        result.append(offset)
    return result


def _first_open_offset(synct: WinLiveTagBlock, chord: WinLiveTagBlock) -> int | None:
    candidates: list[int] = []
    if synct.open_offsets:
        candidates.append(min(synct.open_offsets))
    if chord.open_offsets:
        candidates.append(min(chord.open_offsets))
    if not candidates:
        return None
    return min(candidates)


def _final_close_end_offset(synct: WinLiveTagBlock, chord: WinLiveTagBlock) -> int | None:
    candidates: list[int] = []
    if synct.close_offset is not None:
        candidates.append(synct.close_offset + len(TAG_SYNCT_CLOSE))
    if chord.close_offset is not None:
        candidates.append(chord.close_offset + len(TAG_CHORD_CLOSE))
    if not candidates:
        return None
    return max(candidates)


def _assess_block_repairability(
    data: bytes,
    block: WinLiveTagBlock,
    kind: WinLiveTagKind,
    almost_close: bytes,
) -> StructuralRepairAssessment:
    if block.state == WinLiveStructureState.VALID:
        return StructuralRepairAssessment(
            kind=kind,
            status=StructuralRepairability.NOT_NEEDED,
            candidates=[],
            note="Struttura già valida.",
        )

    if block.state == WinLiveStructureState.ABSENT:
        return StructuralRepairAssessment(
            kind=kind,
            status=StructuralRepairability.NOT_REPAIRABLE,
            candidates=[],
            note="Tag assente: nessuna autoriparazione sintattica possibile.",
        )

    if len(block.open_offsets) != 1 or len(block.close_offsets) > 1:
        return StructuralRepairAssessment(
            kind=kind,
            status=StructuralRepairability.NOT_REPAIRABLE,
            candidates=[],
            note="Struttura ambigua o duplicata.",
        )

    near_offsets = _find_all_offsets(data, almost_close)
    exact_close = TAG_SYNCT_CLOSE if kind == WinLiveTagKind.SYNCT else TAG_CHORD_CLOSE
    clean_near_offsets: list[int] = []
    for offset in near_offsets:
        if data[offset : offset + len(exact_close)] == exact_close:
            continue
        clean_near_offsets.append(offset)

    if len(clean_near_offsets) != 1:
        return StructuralRepairAssessment(
            kind=kind,
            status=StructuralRepairability.NOT_REPAIRABLE,
            candidates=[],
            note="Chiusura incompleta non univoca.",
        )

    offset = clean_near_offsets[0]
    if offset <= block.open_offsets[0]:
        return StructuralRepairAssessment(
            kind=kind,
            status=StructuralRepairability.NOT_REPAIRABLE,
            candidates=[],
            note="Ordine delimitatori non coerente.",
        )

    return StructuralRepairAssessment(
        kind=kind,
        status=StructuralRepairability.REPAIRABLE,
        candidates=[
            RepairCandidate(
                kind=kind,
                insert_at=offset + len(almost_close),
                insert_bytes=b">",
                message="Chiusura sintattica completabile con '>'",
            )
        ],
        note="Autoriparazione sintattica inequivocabile disponibile.",
    )
