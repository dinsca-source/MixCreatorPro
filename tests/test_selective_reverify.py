# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from selective_reverify import (
    REVERIFY_STATUS_REPAIRED,
    REVERIFY_STATUS_UNRECOVERABLE,
    SelectiveReverifyError,
    prepare_selective_reverify_selection,
)


class SelectiveReverifyParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_csv(self, name: str, headers: list[str], rows: list[dict[str, str]], *, bom: bool = False) -> Path:
        path = self.root / name
        encoding = "utf-8-sig" if bom else "utf-8"
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _make_file(self, relative: str, content: bytes = b"x") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_parses_bom_and_filters_target_statuses(self) -> None:
        ok_original = self._make_file("music/song_ok.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File", "Categoria finale"],
            [
                {
                    "Stato finale file": " Riparato ",
                    "Percorso originale": str(ok_original),
                    "File": "song_ok.mp3",
                    "Categoria finale": "File riparati",
                },
                {
                    "Stato finale file": "Integro",
                    "Percorso originale": str(ok_original),
                    "File": "ignored.mp3",
                    "Categoria finale": "File gia rilevati OK",
                },
                {
                    "Stato finale file": " NON RECUPERABILE ",
                    "Percorso originale": str(self.root / "missing.mp3"),
                    "File": "missing.mp3",
                    "Categoria finale": "Non recuperabili",
                },
            ],
            bom=True,
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.repaired_rows, 1)
        self.assertEqual(result.unrecoverable_rows, 1)
        self.assertEqual(result.final_reverify_count, 1)
        self.assertEqual(len(result.missing_originals), 1)

    def test_missing_required_columns_raises(self) -> None:
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "File": "a.mp3",
                }
            ],
        )

        with self.assertRaises(SelectiveReverifyError):
            prepare_selective_reverify_selection(csv_path)

    def test_optional_columns_can_be_missing(self) -> None:
        original = self._make_file("music/a.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "Percorso originale": str(original),
                    "File": "a.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 1)
        self.assertEqual(result.selected_rows[0].previous_category, "")
        self.assertEqual(result.selected_rows[0].previous_significant_end, "")
        self.assertEqual(result.selected_rows[0].previous_trailing_silence_ms, "")

    def test_deduplicates_windows_like_paths_case_insensitive(self) -> None:
        original = self._make_file("music/TrackA.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "Percorso originale": str(original),
                    "File": "TrackA.mp3",
                },
                {
                    "Stato finale file": "riparato",
                    "Percorso originale": str(original).replace("TrackA.mp3", "tracka.mp3"),
                    "File": "tracka.mp3",
                },
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 1)
        self.assertEqual(result.duplicates_excluded, 1)

    def test_excludes_generated_folders_explicitly(self) -> None:
        generated = self._make_file("old/File riparati/sample.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Non recuperabile",
                    "Percorso originale": str(generated),
                    "File": "sample.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 0)
        self.assertEqual(len(result.missing_originals), 1)
        self.assertIn("cartella generata", result.missing_originals[0].reason)

    def test_excludes_new_session_generated_folders(self) -> None:
        generated = self._make_file(
            "output/Diagnostica_MP3_2026-01-02_03-04-05/Esito integrità MP3/File riparati/sample.mp3"
        )
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "Percorso originale": str(generated),
                    "File": "sample.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 0)
        self.assertEqual(len(result.missing_originals), 1)
        self.assertIn("cartella generata", result.missing_originals[0].reason)

    def test_historical_report_path_outside_generated_folders_still_valid(self) -> None:
        original = self._make_file("music/live_original.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "Percorso originale": str(original),
                    "File": "live_original.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 1)

    def test_marks_missing_originals_without_substitution(self) -> None:
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Riparato",
                    "Percorso originale": str(self.root / "no_longer_here.mp3"),
                    "File": "no_longer_here.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.final_reverify_count, 0)
        self.assertEqual(len(result.missing_originals), 1)
        self.assertEqual(result.missing_originals[0].reason, "Originale non trovato")

    def test_no_problematic_rows(self) -> None:
        original = self._make_file("music/a.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "Integro",
                    "Percorso originale": str(original),
                    "File": "a.mp3",
                }
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        self.assertEqual(result.repaired_rows, 0)
        self.assertEqual(result.unrecoverable_rows, 0)
        self.assertEqual(result.final_reverify_count, 0)
        self.assertEqual(len(result.selected_rows), 0)

    def test_previous_status_is_normalized_to_expected_labels(self) -> None:
        repaired_file = self._make_file("music/r.mp3")
        unrecoverable_file = self._make_file("music/u.mp3")
        csv_path = self._write_csv(
            "Riepilogo_File.csv",
            ["Stato finale file", "Percorso originale", "File"],
            [
                {
                    "Stato finale file": "  rIPaRATo ",
                    "Percorso originale": str(repaired_file),
                    "File": "r.mp3",
                },
                {
                    "Stato finale file": " non   recuperabile ",
                    "Percorso originale": str(unrecoverable_file),
                    "File": "u.mp3",
                },
            ],
        )

        result = prepare_selective_reverify_selection(csv_path)

        statuses = {row.previous_status for row in result.selected_rows}
        self.assertIn(REVERIFY_STATUS_REPAIRED, statuses)
        self.assertIn(REVERIFY_STATUS_UNRECOVERABLE, statuses)


if __name__ == "__main__":
    unittest.main()
