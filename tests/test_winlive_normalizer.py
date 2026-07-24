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
        content = "|1515||4080|\n|1515|   |4080|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "")
        self.assertFalse(result.text_semantically_valid)
        self.assertEqual(result.counters.non_significant_rows_removed, 2)

    def test_mixed_row_keeps_significant_text_and_reduces_consecutive_times(self) -> None:
        content = "|1515| |4080|ANTONIO|6000|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|4080|ANTONIO|6000|")
        self.assertTrue(result.text_semantically_valid)

    def test_consecutive_time_reduction_start_middle_end(self) -> None:
        content = "|22875||22877||22880| BEATS,|24165||24170| FAST,|25444||25446||25448|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|22880|BEATS,|24170| FAST,|25448|")

    def test_ltrim_rtrim_only_on_edge_significant_segments(self) -> None:
        content = "|21587|  HEART,|22875| BEATS,|24165| FAST,  |25444|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|21587|HEART,|22875| BEATS,|24165| FAST,|25444|")
        self.assertEqual(result.counters.left_trims, 1)
        self.assertEqual(result.counters.right_trims, 1)

    def test_chronology_rule_a_adjusts_previous_end(self) -> None:
        content = "|1000|TESTO|5000|\n|4000|TESTO SUCCESSIVO|6000|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|1000|TESTO|4000|\n|4000|TESTO SUCCESSIVO|6000|")
        self.assertTrue(result.temporal_normalization_succeeded)
        self.assertEqual(result.counters.previous_row_end_adjustments, 1)

    def test_chronology_rule_b_adjusts_current_start(self) -> None:
        content = "|4000|A|5000|\n|300|A|6000|"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|4000|A|5000|\n|5000|A|6000|")
        self.assertTrue(result.temporal_normalization_succeeded)
        self.assertEqual(result.counters.current_row_start_adjustments, 1)

    def test_chronology_failure_produces_note_and_invalid_text(self) -> None:
        content = "|5000|A|4000|\n|300|B|200|"
        result = normalize_synct_content(content)
        self.assertFalse(result.temporal_normalization_succeeded)
        self.assertFalse(result.text_semantically_valid)
        self.assertTrue(result.notes)

    def test_contains_semantic_text(self) -> None:
        self.assertFalse(contains_semantic_text("|100|  |200|"))
        self.assertTrue(contains_semantic_text("|100|CIAO|200|"))

    def test_count_unrecognized_chords(self) -> None:
        self.assertEqual(count_unrecognized_chords("|100|C?|200|?"), 2)

    def test_normalization_is_idempotent(self) -> None:
        content = "|1000|  TESTO |5000|\n|5000|ALTRO|7000|"
        first = normalize_synct_content(content)
        second = normalize_synct_content(first.normalized_text)
        self.assertEqual(second.normalized_text, first.normalized_text)
        self.assertTrue(second.text_semantically_valid)


if __name__ == "__main__":
    unittest.main()
