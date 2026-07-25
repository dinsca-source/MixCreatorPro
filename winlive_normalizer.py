# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re


class LineSeparator(str, Enum):
    LF = "\n"
    CRLF = "\r\n"
    CR = "\r"


@dataclass(slots=True)
class NormalizationCounters:
    non_significant_rows_removed: int = 0
    consecutive_time_reductions: int = 0
    adjacent_time_chains_reduced: int = 0
    adjacent_time_tags_removed: int = 0
    left_trims: int = 0
    right_trims: int = 0
    previous_row_end_adjustments: int = 0
    current_row_start_adjustments: int = 0
    empty_timed_lines_detected: int = 0
    empty_timed_lines_removed: int = 0


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
    empty_timed_line_values: list[int] = field(default_factory=list)
    alignment_events: list[dict[str, object]] = field(default_factory=list)
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

    prefix, body = _split_initial_value_prefix(content)
    body_without_terminator, protected_terminator = _strip_final_synct_terminator(body)
    body_without_empty_lines, empty_detected, empty_removed, empty_values = _remove_empty_timed_lines(body_without_terminator)
    collapsed_body, reduced_chains, removed_tags = _collapse_adjacent_time_chains(body_without_empty_lines)
    normalized_body, post_empty_detected, post_empty_removed, post_empty_values = _remove_empty_timed_lines(collapsed_body)
    aligned_body, alignment_events = _align_link_timestamps_between_rows(
        normalized_body,
        separator,
        counters,
    )

    counters.adjacent_time_chains_reduced = reduced_chains
    counters.adjacent_time_tags_removed = removed_tags
    counters.consecutive_time_reductions = removed_tags
    counters.empty_timed_lines_detected = empty_detected + post_empty_detected
    counters.empty_timed_lines_removed = empty_removed + post_empty_removed
    all_empty_values = empty_values + post_empty_values

    normalized_text = prefix + aligned_body + protected_terminator
    changed = normalized_text != content
    text_valid = contains_semantic_text(normalized_text)

    if reduced_chains > 0:
        notes.append(
            f"Ridotte {reduced_chains} catene di TAG temporali adiacenti ({removed_tags} TAG rimossi)."
        )
    if counters.empty_timed_lines_removed > 0:
        notes.append(
            f"Eliminate {counters.empty_timed_lines_removed} righe composte solo da TAG temporale ({all_empty_values})."
        )
    if alignment_events:
        notes.append(
            f"Allineati {len(alignment_events)} timestamp finali di collegamento tra righe testuali consecutive."
        )

    temporal_attempted = reduced_chains > 0 or counters.empty_timed_lines_removed > 0 or bool(alignment_events)
    temporal_ok = True

    return SynctNormalizationResult(
        line_separator=separator,
        normalized_text=normalized_text,
        changed=changed,
        text_semantically_valid=text_valid,
        temporal_normalization_attempted=temporal_attempted,
        temporal_normalization_succeeded=temporal_ok,
        counters=counters,
        empty_timed_line_values=all_empty_values,
        alignment_events=alignment_events,
        notes=notes,
    )


def contains_semantic_text(content: str) -> bool:
    for line in _split_lines(content):
        parsed = _parse_line(line)
        if parsed.has_significant_text:
            return True
    return False


def extract_significant_text(content: str) -> str:
    lines: list[str] = []
    for raw_line in _split_lines(content):
        parsed = _parse_line(raw_line)
        significant_parts = [field for field in parsed.fields if _is_significant_text_field(field)]
        if significant_parts:
            lines.append("|".join(significant_parts))
    return "\n".join(lines)


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


_EMPTY_TIMED_LINE_RE = re.compile(r"^[ \t]*\|(\d+)\|[ \t]*$")


def _remove_empty_timed_lines(content: str) -> tuple[str, int, int, list[int]]:
    if not content:
        return content, 0, 0, []

    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    detected = 0
    removed = 0
    values: list[int] = []

    for line in lines:
        stripped_line = line.rstrip("\r\n")
        match = _EMPTY_TIMED_LINE_RE.fullmatch(stripped_line)
        if not match:
            kept.append(line)
            continue

        detected += 1
        removed += 1
        values.append(int(match.group(1)))

    # Preserve content exactly when splitlines() returns a single non-terminated line.
    if len(lines) == 1 and kept and kept[0] == lines[0]:
        return content, detected, removed, values

    return "".join(kept), detected, removed, values


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


_TOKEN_TIME_RE = re.compile(r"\|(\d+)\|")
_SYNCT_INITIAL_PREFIX_RE = re.compile(r"^(\d+\|)")
_SYNCT_FINAL_TERMINATOR_RE = re.compile(r"(\|0\|\|)\s*$")


def _split_initial_value_prefix(content: str) -> tuple[str, str]:
    match = _SYNCT_INITIAL_PREFIX_RE.match(content)
    if not match:
        return "", content
    return match.group(1), content[match.end() :]


def _strip_final_synct_terminator(content: str) -> tuple[str, str]:
    match = _SYNCT_FINAL_TERMINATOR_RE.search(content)
    if not match:
        return content, ""
    return content[: match.start(1)], content[match.start(1) :]


def _is_reducible_gap(text: str) -> bool:
    # Chain continuation is allowed through separators and inline whitespace only.
    # New lines delimit rows and must not be collapsed together.
    return all(ch in "| \t" for ch in text)


def _collapse_adjacent_time_chains(content: str) -> tuple[str, int, int]:
    tokens = list(_TOKEN_TIME_RE.finditer(content))
    if len(tokens) < 2:
        return content, 0, 0

    chains: list[list[re.Match[str]]] = []
    current_chain: list[re.Match[str]] = [tokens[0]]

    for token in tokens[1:]:
        previous = current_chain[-1]
        gap = content[previous.end() : token.start()]
        if _is_reducible_gap(gap):
            current_chain.append(token)
            continue
        if len(current_chain) >= 2:
            chains.append(current_chain)
        current_chain = [token]

    if len(current_chain) >= 2:
        chains.append(current_chain)

    if not chains:
        return content, 0, 0

    out_parts: list[str] = []
    cursor = 0
    removed_tags = 0

    for chain in chains:
        first = chain[0]
        last = chain[-1]
        out_parts.append(content[cursor : first.start()])
        out_parts.append(content[last.start() : last.end()])
        cursor = last.end()
        removed_tags += len(chain) - 1

    out_parts.append(content[cursor:])
    return "".join(out_parts), len(chains), removed_tags


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


def _align_link_timestamps_between_rows(
    content: str,
    separator: LineSeparator,
    counters: NormalizationCounters,
) -> tuple[str, list[dict[str, object]]]:
    if not content:
        return content, []

    lines = _split_lines(content)
    if len(lines) <= 1:
        return content, []

    line_offsets: list[int] = []
    cursor = 0
    for line in lines:
        line_offsets.append(cursor)
        cursor += len(line) + len(separator.value)

    row_states = [_parse_line(line) for line in lines]
    events: list[dict[str, object]] = []
    modified_rows: set[int] = set()

    textual_indices = [idx for idx, row in enumerate(row_states) if row.has_significant_text]
    for pos in range(len(textual_indices) - 1):
        prev_idx = textual_indices[pos]
        next_idx = textual_indices[pos + 1]
        previous = row_states[prev_idx]
        current = row_states[next_idx]

        if previous.last_time_index is None or current.first_time_index is None:
            continue

        previous_end_text = previous.fields[previous.last_time_index].strip()
        current_start_text = current.fields[current.first_time_index].strip()
        if not previous_end_text.isdigit() or not current_start_text.isdigit():
            continue

        previous_end_value = int(previous_end_text)
        current_start_value = int(current_start_text)
        if previous_end_value == current_start_value:
            continue

        old_fragment = _render_row(previous.fields)
        previous.fields[previous.last_time_index] = str(current_start_value)
        new_fragment = _render_row(previous.fields)
        counters.previous_row_end_adjustments += 1
        modified_rows.add(prev_idx)

        events.append(
            {
                "line_current": prev_idx + 1,
                "line_next": next_idx + 1,
                "offset": line_offsets[prev_idx],
                "original_final_ms": previous_end_value,
                "linked_initial_ms": current_start_value,
                "result_final_ms": current_start_value,
                "fragment_before": old_fragment,
                "fragment_after": new_fragment,
            }
        )

    if not events:
        return content, []

    updated_lines: list[str] = []
    for idx, row in enumerate(row_states):
        if idx in modified_rows:
            updated_lines.append(_render_row(row.fields))
        else:
            updated_lines.append(lines[idx])
    return separator.value.join(updated_lines), events
