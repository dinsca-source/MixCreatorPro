# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from unittest import mock

from winlive_normalizer import (
    MAX_CANONICALIZATION_PASSES,
    LineSeparator,
    NormalizationCounters,
    build_logical_line_diffs,
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

    def test_synth_1_single_timed_row_between_text_rows_is_removed_and_relinked(self) -> None:
        content = "0|\n|100|A|190|\n|195|\n|200|B|300|\n|0||"
        first = normalize_synct_content(content)
        second = normalize_synct_content(first.normalized_text)
        self.assertNotIn("|195|", first.normalized_text)
        self.assertIn("|100|A|200|", first.normalized_text)
        self.assertEqual(first.normalized_text, second.normalized_text)

    def test_synth_2_multiple_consecutive_timed_rows_are_all_removed(self) -> None:
        content = "0|\n|100|A|150|\n|151|\n|152|\n|153|\n|160|B|200|\n|0||"
        result = normalize_synct_content(content)
        self.assertNotIn("|151|\n", result.normalized_text)
        self.assertNotIn("|152|\n", result.normalized_text)
        self.assertNotIn("|153|\n", result.normalized_text)
        self.assertGreaterEqual(result.counters.empty_timed_lines_removed, 3)

    def test_synth_3_chain_reduction_and_alignment_are_final_in_single_normalization(self) -> None:
        content = "0|\n|1000||1200||1300|A|4950|\n|5000|B|7000|\n|0||"
        first = normalize_synct_content(content)
        second = normalize_synct_content(first.normalized_text)
        self.assertIn("|1300|A|5000|", first.normalized_text)
        self.assertEqual(second.counters.adjacent_time_tags_removed, 0)
        self.assertEqual(second.counters.previous_row_end_adjustments, 0)

    def test_synth_4_timed_only_row_at_beginning_is_removed(self) -> None:
        content = "|90|\n|100|A|200|"
        result = normalize_synct_content(content)
        self.assertTrue(result.normalized_text.startswith("|100|A|200|"))
        self.assertNotIn("|90|", result.normalized_text)

    def test_synth_5_timed_only_row_at_end_is_removed(self) -> None:
        content = "|100|A|200|\n|250|\n"
        result = normalize_synct_content(content)
        self.assertFalse(result.normalized_text.rstrip().endswith("|250|"))

    def test_synth_6_reindex_after_removal_allows_new_adjacency_alignment(self) -> None:
        content = "0|\n|100|LEFT|199|\n|199|\n|200|RIGHT|500|\n|0||"
        result = normalize_synct_content(content)
        self.assertIn("|100|LEFT|200|", result.normalized_text)
        self.assertEqual(result.counters.previous_row_end_adjustments, 1)

    def test_synth_7_cycle_detection_returns_non_stable_result(self) -> None:
        a = "|100|A|200|"
        b = "|100|B|200|"

        def _flip(content: str, _sep: LineSeparator, iteration: int):
            next_value = b if content == a else a
            counters = NormalizationCounters()
            return next_value, {
                "iteration": iteration,
                "changed": True,
                "phase": "mock_flip",
                "modification_count": 1,
                "counters": counters,
                "empty_values": [],
                "alignment_events": [],
                "input_hash": "in",
                "output_hash": "out",
            }

        with mock.patch("winlive_normalizer._apply_canonical_rules_once", side_effect=_flip):
            result = normalize_synct_content(a)
        self.assertFalse(result.canonicalization_stabilized)
        self.assertTrue(result.canonicalization_cycle_detected)
        self.assertLessEqual(result.canonicalization_iterations, MAX_CANONICALIZATION_PASSES)

    def test_synth_8_already_normalized_input_is_stable_immediately(self) -> None:
        content = "0|\n|100|A|200|\n|200|B|300|\n|0||"
        result = normalize_synct_content(content)
        self.assertFalse(result.changed)
        self.assertTrue(result.canonicalization_stabilized)
        self.assertEqual(result.canonicalization_iterations, 1)

    def test_structured_diff_reports_logical_line_and_tags(self) -> None:
        before = "|100|A|150|\n|200|B|300|"
        after = "|100|A|200|\n|200|B|300|"
        diffs = build_logical_line_diffs(before, after)
        self.assertEqual(diffs[0]["logical_line"], 1)
        self.assertEqual(diffs[0]["tags_before"], [100, 150])
        self.assertEqual(diffs[0]["tags_after"], [100, 200])


if __name__ == "__main__":
    unittest.main()
