# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_normalizer import (
    LineSeparator,
    contains_semantic_text,
    count_unrecognized_chords,
    detect_line_separator,
    normalize_synct_content,
)


class WinLiveNormalizerTests(unittest.TestCase):
    def test_detect_line_separator_variants(self) -> None:
        self.assertEqual(detect_line_separator("a\n b"), LineSeparator.LF)
        self.assertEqual(detect_line_separator("a\r\n b"), LineSeparator.CRLF)
        self.assertEqual(detect_line_separator("a\r b"), LineSeparator.CR)

    def test_rows_with_only_time_and_spaces_are_removed(self) -> None:
        content = "|1290|\n   |1291|\n|1292|   \n"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "")
        self.assertFalse(result.text_semantically_valid)
        self.assertEqual(result.counters.empty_timed_lines_detected, 3)
        self.assertEqual(result.counters.empty_timed_lines_removed, 3)
        self.assertEqual(result.empty_timed_line_values, [1290, 1291, 1292])

    def test_timed_line_with_text_is_kept(self) -> None:
        content = "|1290|Testo"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, content)

    def test_timed_line_with_double_pipe_is_not_removed_as_empty(self) -> None:
        content = "|1290||"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, content)

    def test_synct_initial_prefix_and_final_terminator_are_preserved(self) -> None:
        content = "0|\n|100||200|CIAO|\n|0||"
        result = normalize_synct_content(content)
        self.assertTrue(result.normalized_text.startswith("0|"))
        self.assertTrue(result.normalized_text.endswith("|0||"))

    def test_mixed_row_keeps_significant_text_and_reduces_consecutive_times(self) -> None:
        content = "|1515| |4080|ANTONIO|6000|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|4080|ANTONIO|6000|")
        self.assertTrue(result.text_semantically_valid)

    def test_consecutive_time_reduction_start_middle_end(self) -> None:
        content = "|22875||22877||22880| BEATS,|24165||24170| FAST,|25444||25446||25448|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|22880| BEATS,|24170| FAST,|25448|")

    def test_adjacent_chain_collapses_to_last_tag(self) -> None:
        content = "|12041||12042||12043||12044|RE"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|12044|RE")

    def test_non_adjacent_times_are_not_collapsed(self) -> None:
        content = "|100|A|200|B|300|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, content)
        self.assertEqual(result.counters.adjacent_time_chains_reduced, 0)

    def test_contains_semantic_text(self) -> None:
        self.assertFalse(contains_semantic_text("|100|  |200|"))
        self.assertTrue(contains_semantic_text("|100|CIAO|200|"))

    def test_count_unrecognized_chords(self) -> None:
        self.assertEqual(count_unrecognized_chords("|100|C?|200|?"), 2)

    def test_normalization_is_idempotent(self) -> None:
        content = "1000||1200| TESTO |5000|\n|5000|ALTRO|7000||0||"
        first = normalize_synct_content(content)
        second = normalize_synct_content(first.normalized_text)
        self.assertEqual(second.normalized_text, first.normalized_text)
        self.assertTrue(second.text_semantically_valid)

    def test_equal_timestamps_with_text_are_unchanged(self) -> None:
        content = "|137747|PA|137747|\n|137747|(|137747|coro)|137797|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, content)


if __name__ == "__main__":
    unittest.main()
