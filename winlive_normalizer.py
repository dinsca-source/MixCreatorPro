# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LineSeparator(str, Enum):
    LF = "\n"
    CRLF = "\r\n"
    CR = "\r"


@dataclass(slots=True)
class NormalizationCounters:
    non_significant_rows_removed: int = 0
    consecutive_time_reductions: int = 0
    left_trims: int = 0
    right_trims: int = 0
    previous_row_end_adjustments: int = 0
    current_row_start_adjustments: int = 0


@dataclass(slots=True)
class RowState:
    original: str
    fields: list[str]
    has_significant_text: bool
    first_time_index: int | None
    last_time_index: int | None


@dataclass(slots=True)
class SynctNormalizationResult:
    line_separator: LineSeparator
    normalized_text: str
    changed: bool
    text_semantically_valid: bool
    temporal_normalization_attempted: bool
    temporal_normalization_succeeded: bool
    counters: NormalizationCounters
    notes: list[str] = field(default_factory=list)


def detect_line_separator(content: str) -> LineSeparator:
    crlf = content.count("\r\n")
    tmp = content.replace("\r\n", "")
    lf = tmp.count("\n")
    cr = tmp.count("\r")
    if crlf >= lf and crlf >= cr and crlf > 0:
        return LineSeparator.CRLF
    if lf >= cr and lf > 0:
        return LineSeparator.LF
    if cr > 0:
        return LineSeparator.CR
    return LineSeparator.LF


def normalize_synct_content(content: str) -> SynctNormalizationResult:
    separator = detect_line_separator(content)
    counters = NormalizationCounters()
    notes: list[str] = []

    lines = _split_lines(content)
    rows: list[RowState] = []

    for line in lines:
        if line == "":
            continue
        parsed = _parse_line(line)
        if not parsed.has_significant_text:
            counters.non_significant_rows_removed += 1
            continue

        reduced_fields, reductions = _reduce_consecutive_time_fields(parsed.fields)
        counters.consecutive_time_reductions += reductions

        trimmed_fields, left_trim, right_trim = _trim_edges_of_significant_text(reduced_fields)
        counters.left_trims += left_trim
        counters.right_trims += right_trim

        row = _build_row_state(line, trimmed_fields)
        if not row.has_significant_text:
            counters.non_significant_rows_removed += 1
            continue
        rows.append(row)

    temporal_attempted = len(rows) > 1
    temporal_ok = True
    if temporal_attempted:
        temporal_ok = _normalize_chronology(rows, counters, notes)

    normalized_lines = [_render_row(row.fields) for row in rows if row.fields]
    normalized_text = separator.value.join(normalized_lines)

    changed = normalized_text != content
    text_valid = bool(rows) and temporal_ok

    return SynctNormalizationResult(
        line_separator=separator,
        normalized_text=normalized_text,
        changed=changed,
        text_semantically_valid=text_valid,
        temporal_normalization_attempted=temporal_attempted,
        temporal_normalization_succeeded=temporal_ok,
        counters=counters,
        notes=notes,
    )


def contains_semantic_text(content: str) -> bool:
    for line in _split_lines(content):
        parsed = _parse_line(line)
        if parsed.has_significant_text:
            return True
    return False


def count_unrecognized_chords(chord_content: str) -> int:
    return chord_content.count("?")


def _split_lines(content: str) -> list[str]:
    if "\r\n" in content:
        return content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if "\n" in content:
        return content.split("\n")
    if "\r" in content:
        return content.split("\r")
    return [content]


def _parse_line(line: str) -> RowState:
    fields = _extract_fields(line)
    return _build_row_state(line, fields)


def _build_row_state(original: str, fields: list[str]) -> RowState:
    significant_positions = [index for index, field in enumerate(fields) if _is_significant_text_field(field)]
    time_positions = [index for index, field in enumerate(fields) if _is_time_field(field)]
    return RowState(
        original=original,
        fields=fields,
        has_significant_text=bool(significant_positions),
        first_time_index=time_positions[0] if time_positions else None,
        last_time_index=time_positions[-1] if time_positions else None,
    )


def _extract_fields(line: str) -> list[str]:
    if line.startswith("|") and line.endswith("|") and len(line) >= 2:
        return line[1:-1].split("|")
    return line.split("|")


def _render_row(fields: list[str]) -> str:
    return "|" + "|".join(fields) + "|"


def _is_time_field(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and stripped.isdigit()


def _is_significant_text_field(value: str) -> bool:
    if _is_time_field(value):
        return False
    return value.strip() != ""


def _reduce_consecutive_time_fields(fields: list[str]) -> tuple[list[str], int]:
    out: list[str] = []
    pending_time: str | None = None
    reductions = 0

    for field in fields:
        if _is_time_field(field):
            clean = field.strip()
            if pending_time is not None:
                reductions += 1
            pending_time = clean
            continue

        if _is_significant_text_field(field):
            if pending_time is not None:
                out.append(pending_time)
                pending_time = None
            out.append(field)
            continue

        # non-significant text segment (spaces/empty) is dropped

    if pending_time is not None:
        out.append(pending_time)

    return out, reductions


def _trim_edges_of_significant_text(fields: list[str]) -> tuple[list[str], int, int]:
    text_positions = [index for index, field in enumerate(fields) if _is_significant_text_field(field)]
    if not text_positions:
        return fields, 0, 0

    first = text_positions[0]
    last = text_positions[-1]
    left_trim = 0
    right_trim = 0
    updated = list(fields)

    first_value = updated[first]
    first_ltrim = first_value.lstrip()
    if first_ltrim != first_value:
        left_trim = 1
        updated[first] = first_ltrim

    last_value = updated[last]
    last_rtrim = last_value.rstrip()
    if last_rtrim != last_value:
        right_trim = 1
        updated[last] = last_rtrim

    return updated, left_trim, right_trim


def _first_time(row: RowState) -> int | None:
    if row.first_time_index is None:
        return None
    value = row.fields[row.first_time_index].strip()
    if not value.isdigit():
        return None
    return int(value)


def _last_time(row: RowState) -> int | None:
    if row.last_time_index is None:
        return None
    value = row.fields[row.last_time_index].strip()
    if not value.isdigit():
        return None
    return int(value)


def _normalize_chronology(rows: list[RowState], counters: NormalizationCounters, notes: list[str]) -> bool:
    for index in range(1, len(rows)):
        previous = rows[index - 1]
        current = rows[index]

        prev_start = _first_time(previous)
        prev_end = _last_time(previous)
        cur_start = _first_time(current)
        cur_end = _last_time(current)

        if prev_start is None or prev_end is None or cur_start is None or cur_end is None:
            notes.append(
                f"Riga {index + 1}: tempi mancanti o non numerici (prev_start={prev_start}, prev_end={prev_end}, "
                f"cur_start={cur_start}, cur_end={cur_end})."
            )
            return False

        current_start_plausible = cur_start >= prev_start and cur_start <= cur_end
        if current_start_plausible:
            if previous.last_time_index is None:
                notes.append(f"Riga {index}: indice tempo finale precedente mancante.")
                return False
            if prev_end != cur_start:
                previous.fields[previous.last_time_index] = str(cur_start)
                counters.previous_row_end_adjustments += 1
            continue

        previous_end_plausible = prev_end >= prev_start and prev_end <= cur_end
        if previous_end_plausible:
            if current.first_time_index is None:
                notes.append(f"Riga {index + 1}: indice tempo iniziale corrente mancante.")
                return False
            current.fields[current.first_time_index] = str(prev_end)
            counters.current_row_start_adjustments += 1
            continue

        notes.append(
            f"Riga {index + 1}: normalizzazione cronologica fallita "
            f"(prev_start={prev_start}, prev_end={prev_end}, cur_start={cur_start}, cur_end={cur_end})."
        )
        return False

    return True
