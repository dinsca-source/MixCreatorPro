# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_normalizer import normalize_synct_content


class WinLiveRowAlignmentTests(unittest.TestCase):
    def test_misaligned_row_link_is_aligned(self) -> None:
        content = "0|\n|1000|Testo|4950|\n|5000|Altro|7000|\n|0||"
        result = normalize_synct_content(content)
        self.assertIn("|1000|Testo|5000|", result.normalized_text)
        self.assertIn("|5000|Altro|7000|", result.normalized_text)
        self.assertEqual(result.counters.previous_row_end_adjustments, 1)
        self.assertEqual(len(result.alignment_events), 1)

    def test_already_aligned_rows_remain_unchanged(self) -> None:
        content = "0|\n|1000|Testo|5000|\n|5000|Altro|7000|\n|0||"
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, content)
        self.assertEqual(result.counters.previous_row_end_adjustments, 0)
        self.assertEqual(len(result.alignment_events), 0)

    def test_multiple_consecutive_rows_alignment(self) -> None:
        content = "0|\n|100|A|190|\n|200|B|290|\n|300|C|390|\n|0||"
        result = normalize_synct_content(content)
        self.assertIn("|100|A|200|", result.normalized_text)
        self.assertIn("|200|B|300|", result.normalized_text)
        self.assertIn("|300|C|390|", result.normalized_text)
        self.assertEqual(result.counters.previous_row_end_adjustments, 2)
        self.assertEqual(len(result.alignment_events), 2)

    def test_empty_timed_line_between_text_rows_is_ignored_for_pairing(self) -> None:
        content = "0|\n|100|A|190|\n|195|\n|200|B|300|\n|0||"
        result = normalize_synct_content(content)
        self.assertNotIn("|195|", result.normalized_text)
        self.assertIn("|100|A|200|", result.normalized_text)
        self.assertEqual(len(result.alignment_events), 1)

    def test_idempotent_after_alignment(self) -> None:
        content = "0|\n|1000|Testo|4950|\n|5000|Altro|7000|\n|0||"
        first = normalize_synct_content(content)
        second = normalize_synct_content(first.normalized_text)
        self.assertEqual(first.normalized_text, second.normalized_text)
        self.assertEqual(len(second.alignment_events), 0)


if __name__ == "__main__":
    unittest.main()