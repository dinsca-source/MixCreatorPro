# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any


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
    canonicalization_iterations: int = 0
    canonicalization_stabilized: bool = True
    canonicalization_cycle_detected: bool = False
    canonicalization_cycle_at_iteration: int = 0
    canonicalization_state_hashes: list[str] = field(default_factory=list)
    canonicalization_pass_summaries: list[dict[str, Any]] = field(default_factory=list)


MAX_CANONICALIZATION_PASSES = 8


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

    current = content
    seen_hashes: dict[str, int] = {}
    state_hashes: list[str] = []
    pass_summaries: list[dict[str, Any]] = []
    all_empty_values: list[int] = []
    all_alignment_events: list[dict[str, object]] = []
    stabilized = False
    cycle_detected = False
    cycle_at_iteration = 0

    for iteration in range(1, MAX_CANONICALIZATION_PASSES + 1):
        input_hash = _hash_text(current)
        state_hashes.append(input_hash)
        seen_hashes[input_hash] = iteration

        next_value, pass_info = _apply_canonical_rules_once(
            current,
            separator,
            iteration,
        )
        _accumulate_counters(counters, pass_info["counters"])
        all_empty_values.extend(pass_info["empty_values"])
        all_alignment_events.extend(pass_info["alignment_events"])
        pass_summaries.append(pass_info)

        if next_value == current:
            stabilized = True
            break

        output_hash = _hash_text(next_value)
        if output_hash in seen_hashes:
            cycle_detected = True
            cycle_at_iteration = iteration
            state_hashes.append(output_hash)
            current = next_value
            break

        current = next_value

    normalized_text = current
    changed = normalized_text != content
    text_valid = contains_semantic_text(normalized_text)

    if counters.adjacent_time_chains_reduced > 0:
        notes.append(
            "Ridotte "
            f"{counters.adjacent_time_chains_reduced} catene di TAG temporali adiacenti "
            f"({counters.adjacent_time_tags_removed} TAG rimossi)."
        )
    if counters.empty_timed_lines_removed > 0:
        notes.append(
            f"Eliminate {counters.empty_timed_lines_removed} righe composte solo da TAG temporale ({all_empty_values})."
        )
    if all_alignment_events:
        notes.append(
            f"Allineati {len(all_alignment_events)} timestamp finali di collegamento tra righe testuali consecutive."
        )

    if stabilized:
        notes.append(f"Canonicalizzazione stabile in {len(pass_summaries)} passate.")
    elif cycle_detected:
        notes.append(
            "Canonicalizzazione non stabile: ciclo rilevato "
            f"alla passata {cycle_at_iteration} (max={MAX_CANONICALIZATION_PASSES})."
        )
    else:
        notes.append(
            "Canonicalizzazione non stabile entro il limite massimo "
            f"di {MAX_CANONICALIZATION_PASSES} passate."
        )

    temporal_attempted = any(pass_info["changed"] for pass_info in pass_summaries)
    temporal_ok = stabilized and text_valid and not cycle_detected

    return SynctNormalizationResult(
        line_separator=separator,
        normalized_text=normalized_text,
        changed=changed,
        text_semantically_valid=text_valid,
        temporal_normalization_attempted=temporal_attempted,
        temporal_normalization_succeeded=temporal_ok,
        counters=counters,
        empty_timed_line_values=all_empty_values,
        alignment_events=all_alignment_events,
        notes=notes,
        canonicalization_iterations=len(pass_summaries),
        canonicalization_stabilized=stabilized,
        canonicalization_cycle_detected=cycle_detected,
        canonicalization_cycle_at_iteration=cycle_at_iteration,
        canonicalization_state_hashes=state_hashes,
        canonicalization_pass_summaries=pass_summaries,
    )


def build_logical_line_diffs(before: str, after: str, *, max_items: int = 200) -> list[dict[str, Any]]:
    before_lines = _split_lines(before)
    after_lines = _split_lines(after)
    max_len = max(len(before_lines), len(after_lines))
    diffs: list[dict[str, Any]] = []

    for idx in range(max_len):
        old = before_lines[idx] if idx < len(before_lines) else None
        new = after_lines[idx] if idx < len(after_lines) else None
        if old == new:
            continue

        if old is None:
            operation = "added"
        elif new is None:
            operation = "removed"
        else:
            operation = "changed"

        diffs.append(
            {
                "logical_line": idx + 1,
                "operation": operation,
                "before": old,
                "after": new,
                "tags_before": _extract_time_tags(old or ""),
                "tags_after": _extract_time_tags(new or ""),
                "old_index": idx + 1 if old is not None else None,
                "new_index": idx + 1 if new is not None else None,
            }
        )
        if len(diffs) >= max_items:
            break

    return diffs


def _extract_time_tags(line: str) -> list[int]:
    return [int(match.group(1)) for match in _TOKEN_TIME_RE.finditer(line or "")]


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _accumulate_counters(total: NormalizationCounters, delta: NormalizationCounters) -> None:
    total.non_significant_rows_removed += delta.non_significant_rows_removed
    total.consecutive_time_reductions += delta.consecutive_time_reductions
    total.adjacent_time_chains_reduced += delta.adjacent_time_chains_reduced
    total.adjacent_time_tags_removed += delta.adjacent_time_tags_removed
    total.left_trims += delta.left_trims
    total.right_trims += delta.right_trims
    total.previous_row_end_adjustments += delta.previous_row_end_adjustments
    total.current_row_start_adjustments += delta.current_row_start_adjustments
    total.empty_timed_lines_detected += delta.empty_timed_lines_detected
    total.empty_timed_lines_removed += delta.empty_timed_lines_removed


def _apply_canonical_rules_once(
    content: str,
    separator: LineSeparator,
    iteration: int,
) -> tuple[str, dict[str, Any]]:
    pass_counters = NormalizationCounters()
    prefix, body = _split_initial_value_prefix(content)
    body_without_terminator, protected_terminator = _strip_final_synct_terminator(body)

    body_a, empty_detected_a, empty_removed_a, empty_values_a = _remove_empty_timed_lines(body_without_terminator)
    body_b, reduced_chains_a, removed_tags_a = _collapse_adjacent_time_chains(body_a)
    body_c, empty_detected_b, empty_removed_b, empty_values_b = _remove_empty_timed_lines(body_b)
    body_d, alignment_events = _align_link_timestamps_between_rows(body_c, separator, pass_counters)
    body_e, reduced_chains_b, removed_tags_b = _collapse_adjacent_time_chains(body_d)
    body_f, empty_detected_c, empty_removed_c, empty_values_c = _remove_empty_timed_lines(body_e)

    pass_counters.adjacent_time_chains_reduced = reduced_chains_a + reduced_chains_b
    pass_counters.adjacent_time_tags_removed = removed_tags_a + removed_tags_b
    pass_counters.consecutive_time_reductions = removed_tags_a + removed_tags_b
    pass_counters.empty_timed_lines_detected = empty_detected_a + empty_detected_b + empty_detected_c
    pass_counters.empty_timed_lines_removed = empty_removed_a + empty_removed_b + empty_removed_c

    next_value = prefix + body_f + protected_terminator
    changed = next_value != content
    modification_count = (
        pass_counters.adjacent_time_tags_removed
        + pass_counters.empty_timed_lines_removed
        + pass_counters.previous_row_end_adjustments
        + pass_counters.current_row_start_adjustments
    )
    phase = "stable"
    if pass_counters.empty_timed_lines_removed > 0:
        phase = "remove_empty_timed_rows"
    elif pass_counters.adjacent_time_tags_removed > 0:
        phase = "collapse_adjacent_time_chains"
    elif alignment_events:
        phase = "align_consecutive_text_rows"

    pass_info = {
        "iteration": iteration,
        "changed": changed,
        "phase": phase,
        "modification_count": modification_count,
        "counters": pass_counters,
        "empty_values": empty_values_a + empty_values_b + empty_values_c,
        "alignment_events": alignment_events,
        "input_hash": _hash_text(content),
        "output_hash": _hash_text(next_value),
    }
    return next_value, pass_info


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
    # Align from bottom to top so changes on a single-timestamp row are
    # immediately visible to the previous row in the same canonical pass.
    for pos in range(len(textual_indices) - 2, -1, -1):
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

    events.sort(key=lambda item: int(item.get("line_current", 0)))

    updated_lines: list[str] = []
    for idx, row in enumerate(row_states):
        if idx in modified_rows:
            updated_lines.append(_render_row(row.fields))
        else:
            updated_lines.append(lines[idx])
    return separator.value.join(updated_lines), events
