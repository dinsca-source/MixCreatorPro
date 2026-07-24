# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from winlive_tags import (
    StructuralRepairability,
    WinLiveAnomalyCode,
    WinLiveStructureState,
    WinLiveTagKind,
    assess_structural_repairability,
    parse_winlive_blocks_strict,
)


def _blob(synct: bytes = b"", chord: bytes = b"", tail: bytes = b"\x00") -> bytes:
    return b"\xFF\xFB\x90\x64" + b"A" * 16 + synct + chord + tail


class WinLiveStrictParserTests(unittest.TestCase):
    def test_valid_single_pairs_offsets(self) -> None:
        data = _blob(
            b"<WL5SYNCT>|100|CIAO|200|/<WL5SYNCT>",
            b"<WL5CHORD>|100|C|200|/<WL5CHORD>",
        )
        parsed = parse_winlive_blocks_strict(data)

        self.assertEqual(parsed.synct.state, WinLiveStructureState.VALID)
        self.assertEqual(parsed.chord.state, WinLiveStructureState.VALID)
        self.assertEqual(parsed.synct.content_bytes, b"|100|CIAO|200|")
        self.assertEqual(parsed.chord.content_bytes, b"|100|C|200|")
        self.assertEqual(parsed.trailing_bytes, b"\x00")

    def test_open_count_does_not_include_close_substring(self) -> None:
        data = _blob(b"<WL5SYNCT>|x|/<WL5SYNCT>")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.open_offsets and len(parsed.synct.open_offsets), 1)
        self.assertEqual(parsed.synct.close_offsets and len(parsed.synct.close_offsets), 1)

    def test_missing_close_is_invalid(self) -> None:
        data = _blob(b"<WL5SYNCT>|100|CIAO|200|")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.state, WinLiveStructureState.INVALID_STRUCTURE)
        codes = {a.code for a in parsed.synct.anomalies}
        self.assertIn(WinLiveAnomalyCode.MISSING_CLOSING, codes)

    def test_multiple_openings_is_invalid(self) -> None:
        data = _blob(b"<WL5SYNCT>|1|A|2|<WL5SYNCT>|3|B|4|/<WL5SYNCT>")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.state, WinLiveStructureState.INVALID_STRUCTURE)
        codes = {a.code for a in parsed.synct.anomalies}
        self.assertIn(WinLiveAnomalyCode.MULTIPLE_OPENINGS, codes)

    def test_multiple_closings_is_invalid(self) -> None:
        data = _blob(b"<WL5SYNCT>|1|A|2|/<WL5SYNCT>/<WL5SYNCT>")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.state, WinLiveStructureState.INVALID_STRUCTURE)
        codes = {a.code for a in parsed.synct.anomalies}
        self.assertIn(WinLiveAnomalyCode.MULTIPLE_CLOSINGS, codes)

    def test_inverted_order_is_invalid(self) -> None:
        data = _blob(b"/<WL5SYNCT><WL5SYNCT>|A|")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.state, WinLiveStructureState.INVALID_STRUCTURE)

    def test_overlap_between_synct_and_chord_invalidates_both(self) -> None:
        synct = b"<WL5SYNCT>|1|A|2|<WL5CHORD>|1|C|2|/<WL5SYNCT>"
        chord_close = b"/<WL5CHORD>"
        data = _blob(synct + chord_close)
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.synct.state, WinLiveStructureState.INVALID_STRUCTURE)
        self.assertEqual(parsed.chord.state, WinLiveStructureState.INVALID_STRUCTURE)

    def test_cr_lf_crlf_are_preserved_in_content_bytes(self) -> None:
        synct = b"<WL5SYNCT>|1|A|2|\r\n|2|B|3|\n|3|C|4|\r|4|D|5|/<WL5SYNCT>"
        data = _blob(synct)
        parsed = parse_winlive_blocks_strict(data)
        self.assertIn(b"\r\n", parsed.synct.content_bytes or b"")
        self.assertIn(b"\n", parsed.synct.content_bytes or b"")
        self.assertIn(b"\r", parsed.synct.content_bytes or b"")

    def test_detects_bytes_after_final_close(self) -> None:
        data = _blob(b"<WL5SYNCT>|1|A|2|/<WL5SYNCT>", b"", tail=b"\x00\x01")
        parsed = parse_winlive_blocks_strict(data)
        self.assertEqual(parsed.trailing_bytes, b"\x00\x01")

    def test_repairability_detects_inequivocal_missing_angle_bracket(self) -> None:
        data = _blob(b"<WL5SYNCT>|1|A|2|/<WL5SYNCT")
        parsed = parse_winlive_blocks_strict(data)
        assessments = assess_structural_repairability(data, parsed)
        syn = next(item for item in assessments if item.kind == WinLiveTagKind.SYNCT)
        self.assertEqual(syn.status, StructuralRepairability.REPAIRABLE)
        self.assertEqual(len(syn.candidates), 1)
        self.assertEqual(syn.candidates[0].insert_bytes, b">")

    def test_repairability_rejects_ambiguous_multiple_closes(self) -> None:
        data = _blob(b"<WL5CHORD>|1|C|2|/<WL5CHORD/<WL5CHORD")
        parsed = parse_winlive_blocks_strict(data)
        assessments = assess_structural_repairability(data, parsed)
        chord = next(item for item in assessments if item.kind == WinLiveTagKind.CHORD)
        self.assertEqual(chord.status, StructuralRepairability.NOT_REPAIRABLE)


if __name__ == "__main__":
    unittest.main()
