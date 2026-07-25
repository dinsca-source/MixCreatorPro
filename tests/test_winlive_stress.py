# -*- coding: utf-8 -*-

from __future__ import annotations

import random
import time
import tracemalloc
import unittest

from winlive_normalizer import normalize_synct_content
from winlive_tags import WinLiveStructureState, parse_winlive_blocks_strict


def _mk_blob(payload: bytes) -> bytes:
    return b"\xFF\xFB\x90\x64" + (b"A" * 24) + payload + b"\x00"


def _rand_line(rng: random.Random, idx: int) -> str:
    start = idx * 10
    end = start + rng.randint(1, 30)
    choices = [
        "CIAO",
        "Citta\u00e0",
        "M\u00e9lange",
        "na\u00efve",
        "\u6f22\u5b57",
        "emoji\U0001f642",
        "",
        "   ",
    ]
    text = rng.choice(choices)

    extra_times = "|".join(str(start + rng.randint(0, 3)) for _ in range(rng.randint(0, 3)))
    if extra_times:
        return f"|{start}|{extra_times}|{text}|{end}|"
    return f"|{start}|{text}|{end}|"


class WinLiveStressTests(unittest.TestCase):
    def test_normalize_10000_rows_time_memory_and_stability(self) -> None:
        rows = [f"|{i*10}||{i*10+2}|TESTO LUNGO {i} con accenti citta\u00e0 e unicode \u6f22\u5b57|{i*10+20}|" for i in range(10000)]
        content = "\n".join(rows)

        tracemalloc.start()
        start = time.perf_counter()
        result = normalize_synct_content(content)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertTrue(result.text_semantically_valid)
        self.assertLess(elapsed, 8.0)
        self.assertLess(peak, 300 * 1024 * 1024)

    def test_massive_consecutive_times_and_long_text(self) -> None:
        times = "||".join(str(i) for i in range(3000, 3400))
        long_text = "A" * 20000 + " citta\u00e0 \u6f22\u5b57"
        content = f"|{times}|{long_text}|5000|"
        result = normalize_synct_content(content)

        self.assertTrue(result.text_semantically_valid)
        self.assertIn(long_text, result.normalized_text)
        self.assertGreater(result.counters.consecutive_time_reductions, 0)

    def test_lines_without_text_removed(self) -> None:
        content = "\n".join(["|100||200|", "|200|   |300|", "|300|CIAO|400|"])
        result = normalize_synct_content(content)
        self.assertEqual(result.normalized_text, "|300|CIAO|400|")

    def test_parser_without_winlive_tags(self) -> None:
        parsed = parse_winlive_blocks_strict(_mk_blob(b"NO_TAGS_HERE"))
        self.assertEqual(parsed.synct.state, WinLiveStructureState.ABSENT)
        self.assertEqual(parsed.chord.state, WinLiveStructureState.ABSENT)

    def test_parser_near_corrupt_and_structural_anomalies(self) -> None:
        samples = [
            b"<WL5SYNCT>|1|A|2|/<WL5SYNCT",  # near-corrupt close
            b"<WL5SYNCT>|1|A|2|<WL5SYNCT>|3|B|4|/<WL5SYNCT>",  # duplicate open
            b"<WL5SYNCT>|1|A|2|<WL5CHORD>|1|C|2|/<WL5SYNCT>/<WL5CHORD>",  # overlap
            b"/<WL5SYNCT><WL5SYNCT>|1|A|2|",  # inverted
        ]
        for item in samples:
            parsed = parse_winlive_blocks_strict(_mk_blob(item))
            self.assertTrue(
                parsed.synct.state != WinLiveStructureState.VALID or parsed.chord.state != WinLiveStructureState.VALID
            )

    def test_random_idempotence_20_cases(self) -> None:
        rng = random.Random(1337)
        for case_index in range(20):
            lines = [_rand_line(rng, idx) for idx in range(1, rng.randint(4, 40))]
            content = "\n".join(lines)

            first = normalize_synct_content(content)
            second = normalize_synct_content(first.normalized_text)

            self.assertEqual(
                second.normalized_text,
                first.normalized_text,
                msg=f"Idempotence failed at random case {case_index}",
            )


if __name__ == "__main__":
    unittest.main()
