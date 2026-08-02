# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mp3_repertory_diagnostics import (
    DIAGNOSTICS_FOLDER_NAME,
    DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL,
    DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT,
    ROOT_FILES_TOKEN,
    ROOT_FILES_REPORT_LABEL,
    DiagnosticsConfig,
    DiagnosticsError,
    DiagnosticsStatus,
    _is_path_in_excluded_roots,
    run_repertory_diagnostics,
)


class Mp3RepertoryDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.split = self.root / "split"
        self.general = self.root / "general"
        self.split.mkdir()
        self.general.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_mp3(self, path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _read_rows(self, csv_path: str) -> list[dict[str, str]]:
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter=";"))

    def test_present_in_both_and_only_sides(self) -> None:
        self._write_mp3(self.split / "A.mp3", b"AAA")
        self._write_mp3(self.split / "B.mp3", b"BBB")
        self._write_mp3(self.general / "A.mp3", b"AAA")
        self._write_mp3(self.general / "C.mp3", b"CCC")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        statuses = {row["Stato"] for row in rows}
        self.assertIn(DiagnosticsStatus.PRESENTE_ENTRAMBI.value, statuses)
        self.assertIn(DiagnosticsStatus.SOLO_GENERALE.value, statuses)
        self.assertIn(DiagnosticsStatus.SOLO_SUDDIVISO.value, statuses)
        self.assertEqual(result.matched_both, 1)
        self.assertEqual(result.only_general, 1)
        self.assertEqual(result.only_split, 1)
        self.assertTrue(Path(result.session_folder).is_dir())
        self.assertTrue(Path(result.report_paths["html"]).is_file())
        self.assertTrue(Path(result.report_paths["xlsx"]).is_file())
        self.assertTrue(Path(result.log_path).is_file())
        self.assertTrue(Path(result.session_folder).name.startswith("Diagnosi_Repertorio_"))

    def test_duplicate_in_split_is_reported_and_copied_once_in_flat_output(self) -> None:
        self._write_mp3(self.split / "disc1" / "Brano.mp3", b"AAA")
        self._write_mp3(self.split / "disc2" / "Brano.mp3", b"AAA")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.DUPLICATO_SUDDIVISO.value)
        self.assertIn("disc1", rows[0]["Percorsi duplicati"])
        self.assertIn("disc2", rows[0]["Percorsi duplicati"])
        self.assertEqual(result.split_duplicates, 1)
        self.assertEqual(result.copied_files, 1)
        copied_root = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        self.assertTrue((copied_root / "Brano.mp3").is_file())
        self.assertFalse((copied_root / "Brano (1).mp3").exists())
        self.assertFalse(any(path.is_dir() for path in copied_root.iterdir()))

    def test_excludes_general_folder_inside_split_root(self) -> None:
        nested_general = self.split / "Tutti i brani"
        nested_general.mkdir()
        self._write_mp3(nested_general / "A.mp3", b"AAA")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, nested_general))

        self.assertEqual(result.analyzed_split_files, 0)
        self.assertEqual(result.analyzed_general_files, 1)
        self.assertEqual(result.only_general, 1)
        self.assertTrue((Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL).exists())
        self.assertTrue(Path(result.diagnosis_root).name == DIAGNOSTICS_FOLDER_NAME)

    def test_split_only_file_is_copied_flat_without_subfolders(self) -> None:
        self._write_mp3(self.split / "sub" / "C.mp3", b"CCC")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.SOLO_SUDDIVISO.value)
        self.assertIn("sub", rows[0]["Percorso Repertorio suddiviso"])
        self.assertEqual(result.only_split, 1)
        flat_root = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        self.assertTrue((flat_root / "C.mp3").is_file())
        self.assertFalse(any(path.is_dir() for path in flat_root.iterdir()))

    def test_routing_1_solo_generale_goes_only_to_missing_in_split_folder(self) -> None:
        self._write_mp3(self.general / "Brano Solo Generale.mp3", b"GEN")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.SOLO_GENERALE.value)

        missing_in_split = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL
        missing_in_general = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        self.assertTrue((missing_in_split / "Brano Solo Generale.mp3").is_file())
        self.assertFalse(any(path.is_file() for path in missing_in_general.rglob("*")))

    def test_routing_2_solo_suddiviso_goes_only_to_missing_in_general_folder(self) -> None:
        self._write_mp3(self.split / "Italiano" / "Brano Solo Suddiviso.mp3", b"SPL")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.SOLO_SUDDIVISO.value)

        missing_in_split = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL
        missing_in_general = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        copied_file = missing_in_general / "Brano Solo Suddiviso.mp3"
        self.assertTrue(copied_file.is_file())
        self.assertFalse(any(path.is_dir() for path in missing_in_general.iterdir()))
        self.assertFalse(any(path.is_file() for path in missing_in_split.rglob("*")))

    def test_duplicate_split_with_different_content_reports_anomaly_and_single_flat_copy(self) -> None:
        self._write_mp3(self.split / "disc1" / "Anomalo.mp3", b"AAA")
        self._write_mp3(self.split / "disc2" / "Anomalo.mp3", b"BBB")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.DUPLICATO_SUDDIVISO.value)
        self.assertIn("omonimi con contenuto differente", rows[0]["Motivo/Note"].casefold())
        self.assertIn("disc1", rows[0]["Percorsi duplicati"])
        self.assertIn("disc2", rows[0]["Percorsi duplicati"])
        self.assertEqual(rows[0]["Numero occorrenze nel Repertorio suddiviso"], "2")
        flat_root = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        self.assertTrue((flat_root / "Anomalo.mp3").is_file())
        self.assertFalse((flat_root / "Anomalo (1).mp3").exists())
        self.assertEqual(result.copied_files, 1)

    def test_solo_generale_distinct_titles_are_all_copied(self) -> None:
        self._write_mp3(self.general / "A.mp3", b"A")
        self._write_mp3(self.general / "B.mp3", b"B")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        missing_in_split = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL

        self.assertEqual(result.only_general, 2)
        self.assertEqual(result.copied_files, 2)
        self.assertTrue((missing_in_split / "A.mp3").is_file())
        self.assertTrue((missing_in_split / "B.mp3").is_file())

    def test_partial_copy_is_marked_in_report_for_split_duplicates(self) -> None:
        self._write_mp3(self.split / "disc1" / "Brano.mp3", b"A")
        self._write_mp3(self.split / "disc2" / "Brano.mp3", b"B")

        with mock.patch("mp3_repertory_diagnostics._copy_with_suffix", side_effect=[OSError("copy failed")]):
            result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))

        rows = self._read_rows(result.report_paths["csv"])
        self.assertEqual(result.copied_files, 0)
        self.assertEqual(result.copy_errors, 1)
        self.assertEqual(rows[0]["Esito copia Diagnosi"], "Errore copia")
        self.assertIn("Errori copia: 1", rows[0]["Dettaglio errore"])

    def test_routing_3_present_in_both_does_not_copy_to_missing_folders(self) -> None:
        self._write_mp3(self.split / "Presente.mp3", b"AAA")
        self._write_mp3(self.general / "Presente.mp3", b"AAA")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        missing_in_split = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL
        missing_in_general = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        self.assertFalse(any(path.is_file() for path in missing_in_split.rglob("*")))
        self.assertFalse(any(path.is_file() for path in missing_in_general.rglob("*")))

    def test_selected_subtree_limits_split_scan(self) -> None:
        self._write_mp3(self.split / "A" / "A1.mp3", b"A1")
        self._write_mp3(self.split / "B" / "B1.mp3", b"B1")

        result = run_repertory_diagnostics(
            DiagnosticsConfig(
                self.split,
                self.general,
                selected_relative_roots=("A",),
                include_root_files=False,
            )
        )

        self.assertEqual(result.analyzed_split_files, 1)
        rows = self._read_rows(result.report_paths["csv"])
        normalized = {row["Nome normalizzato"] for row in rows}
        self.assertIn("a1.mp3", normalized)
        self.assertNotIn("b1.mp3", normalized)

    def test_root_files_can_be_excluded(self) -> None:
        self._write_mp3(self.split / "ROOT.mp3", b"ROOT")
        self._write_mp3(self.split / "sub" / "INSIDE.mp3", b"IN")

        result = run_repertory_diagnostics(
            DiagnosticsConfig(
                self.split,
                self.general,
                include_root_files=False,
                selected_relative_roots=("sub",),
            )
        )

        self.assertEqual(result.analyzed_split_files, 1)
        rows = self._read_rows(result.report_paths["csv"])
        normalized = {row["Nome normalizzato"] for row in rows}
        self.assertIn("inside.mp3", normalized)
        self.assertNotIn("root.mp3", normalized)

    def test_folder_reports_are_generated_and_linked(self) -> None:
        self._write_mp3(self.split / "root.mp3", b"ROOT")
        self._write_mp3(self.split / "disc1" / "A.mp3", b"AAA")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))

        folder_csv = Path(result.folder_report_paths["csv"])
        self.assertTrue(folder_csv.is_file())
        text = Path(result.report_paths["html"]).read_text(encoding="utf-8")
        self.assertIn("Alberatura repertorio e selezione", text)
        self.assertTrue(Path(result.report_paths["xlsx"]).is_file())
        self.assertTrue(any(record.relative_path == ROOT_FILES_TOKEN for record in result.folder_records))

    def test_same_name_different_timestamp_is_present_in_both(self) -> None:
        split_file = self._write_mp3(self.split / "Song.mp3", b"AAA")
        general_file = self._write_mp3(self.general / "Song.mp3", b"AAA")
        split_file.touch()
        general_file.touch()

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        self.assertEqual(result.matched_both, 1)
        self.assertEqual(result.only_general, 0)
        self.assertEqual(result.only_split, 0)

    def test_same_name_different_size_is_present_in_both(self) -> None:
        self._write_mp3(self.split / "Song.mp3", b"AAAA")
        self._write_mp3(self.general / "Song.mp3", b"A")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        self.assertEqual(rows[0]["Confronto contenuto"], "Differente")
        self.assertEqual(result.matched_both, 1)

    def test_case_difference_in_name_is_present_in_both(self) -> None:
        self._write_mp3(self.split / "Adesso Tu.MP3", b"AAA")
        self._write_mp3(self.general / "adesso tu.mp3", b"BBB")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        self.assertEqual(result.matched_both, 1)

    def test_duplicate_in_split_and_present_in_general_counts_in_both_and_duplicates(self) -> None:
        self._write_mp3(self.split / "disc1" / "Brano.mp3", b"AAA")
        self._write_mp3(self.split / "disc2" / "Brano.mp3", b"BBB")
        self._write_mp3(self.general / "Brano.mp3", b"CCC")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.DUPLICATO_SUDDIVISO.value)
        self.assertEqual(result.matched_both, 1)
        self.assertEqual(result.split_duplicates, 1)
        self.assertEqual(result.split_duplicate_extra_occurrences, 1)

    def test_unique_titles_are_separate_from_occurrences(self) -> None:
        self._write_mp3(self.general / "g1.mp3", b"1")
        self._write_mp3(self.general / "g2.mp3", b"2")
        self._write_mp3(self.general / "shared.mp3", b"3")

        self._write_mp3(self.split / "s1.mp3", b"4")
        self._write_mp3(self.split / "shared.mp3", b"5")
        self._write_mp3(self.split / "dup" / "dup.mp3", b"6")
        self._write_mp3(self.split / "dup2" / "dup.mp3", b"7")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))

        self.assertEqual(result.general_unique_titles, 3)
        self.assertEqual(result.split_unique_titles, 3)
        self.assertEqual(result.matched_both, 1)
        self.assertEqual(result.only_general, 2)
        self.assertEqual(result.only_split, 2)
        self.assertEqual(result.split_duplicates, 1)
        self.assertEqual(result.split_duplicate_extra_occurrences, 1)

    def test_perfect_alignment_flag_true_only_for_one_to_one(self) -> None:
        self._write_mp3(self.split / "A.mp3", b"A")
        self._write_mp3(self.split / "B.mp3", b"B")
        self._write_mp3(self.general / "A.mp3", b"X")
        self._write_mp3(self.general / "B.mp3", b"Y")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))

        self.assertTrue(result.is_perfect_alignment)
        self.assertEqual(result.only_general, 0)
        self.assertEqual(result.only_split, 0)
        self.assertEqual(result.split_duplicates, 0)

    def test_not_aligned_flag_false_when_only_split_exists(self) -> None:
        self._write_mp3(self.split / "A.mp3", b"A")
        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        self.assertFalse(result.is_perfect_alignment)

    def test_root_virtual_row_present_even_without_root_mp3(self) -> None:
        self._write_mp3(self.split / "sub" / "A.mp3", b"A")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        folder_rows = self._read_rows(result.folder_report_paths["csv"])

        root_rows = [row for row in folder_rows if row["Percorso relativo"] == ROOT_FILES_REPORT_LABEL]
        self.assertEqual(len(root_rows), 1)
        self.assertEqual(root_rows[0]["Numero MP3 rilevati"], "0")

    def test_root_count_limited_to_direct_files(self) -> None:
        self._write_mp3(self.split / "root1.mp3", b"1")
        self._write_mp3(self.split / "root2.mp3", b"2")
        self._write_mp3(self.split / "sub" / "inside.mp3", b"3")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        folder_rows = self._read_rows(result.folder_report_paths["csv"])
        root_row = next(row for row in folder_rows if row["Percorso relativo"] == ROOT_FILES_REPORT_LABEL)

        self.assertEqual(root_row["Numero MP3 rilevati"], "2")

    def test_root_virtual_row_is_present_in_csv_xlsx_and_html_reports(self) -> None:
        self._write_mp3(self.split / "sub" / "inside.mp3", b"X")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))

        folder_rows = self._read_rows(result.folder_report_paths["csv"])
        self.assertTrue(any(row["Percorso relativo"] == ROOT_FILES_REPORT_LABEL for row in folder_rows))

        html_text = Path(result.report_paths["html"]).read_text(encoding="utf-8")
        self.assertIn(ROOT_FILES_REPORT_LABEL, html_text)

        with zipfile.ZipFile(result.report_paths["xlsx"], "r") as zf:
            sheet2 = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
        self.assertIn(ROOT_FILES_REPORT_LABEL, sheet2)

    def test_real_case_duplicate_also_in_general_has_no_physical_copy_in_missing_folders(self) -> None:
        song = "Adesso Tu (Eros Ramazzotti).mp3"
        self._write_mp3(self.general / song, b"A")
        self._write_mp3(self.split / "Andante" / song, b"A")
        self._write_mp3(self.split / "Italiano" / song, b"A")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])

        self.assertEqual(result.general_unique_titles, 1)
        self.assertEqual(result.split_unique_titles, 1)
        self.assertEqual(result.matched_both, 1)
        self.assertEqual(result.only_general, 0)
        self.assertEqual(result.only_split, 0)
        self.assertEqual(result.split_duplicates, 1)
        self.assertEqual(result.split_duplicate_extra_occurrences, 1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Stato"], DiagnosticsStatus.DUPLICATO_SUDDIVISO.value)
        self.assertEqual(rows[0]["Presente in Cartella Repertorio Generale"], "SI")
        self.assertEqual(rows[0]["Presente in Repertorio suddiviso"], "SI")
        self.assertEqual(rows[0]["Numero occorrenze nel Repertorio suddiviso"], "2")
        self.assertIn(rows[0]["Esito copia Diagnosi"], {"Non applicabile", ""})
        self.assertIn(rows[0]["Percorso copia Diagnosi"], {"", " "})

        missing_general_folder = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_SPLIT
        missing_split_folder = Path(result.session_folder) / DIAGNOSTICS_SUBFOLDER_ONLY_GENERAL
        self.assertFalse(any(path.is_file() and path.name == song for path in missing_general_folder.rglob("*")))
        self.assertFalse(any(path.is_file() and path.name == song for path in missing_split_folder.rglob("*")))

    def test_report_still_contains_technical_detail_columns(self) -> None:
        self._write_mp3(self.split / "A.mp3", b"AAA")
        self._write_mp3(self.general / "A.mp3", b"BBB")

        result = run_repertory_diagnostics(DiagnosticsConfig(self.split, self.general))
        rows = self._read_rows(result.report_paths["csv"])
        row = rows[0]

        self.assertIn("Dimensione file Cartella Generale", row)
        self.assertIn("Dimensione file Repertorio", row)
        self.assertIn("Data/Ora file Cartella Generale", row)
        self.assertIn("Data/Ora file Repertorio", row)
        self.assertIn("Confronto contenuto", row)
        self.assertIn("Percorso Repertorio suddiviso", row)

    def test_nested_general_folder_selected_children_are_processed(self) -> None:
        split_root = self.root / "RepertorioSuddiviso"
        general_nested = split_root / "Generico"
        split_root.mkdir()
        general_nested.mkdir()

        self._write_mp3(general_nested / "Brano A.mp3", b"A")
        self._write_mp3(general_nested / "Brano B.mp3", b"B")
        self._write_mp3(split_root / "Italiano" / "Brano A.mp3", b"A")
        self._write_mp3(split_root / "Italiano" / "Brano C.mp3", b"C")
        self._write_mp3(split_root / "Estero" / "Brano B.mp3", b"B")
        self._write_mp3(split_root / "Archivio" / "Brano D.mp3", b"D")

        result = run_repertory_diagnostics(
            DiagnosticsConfig(
                split_root,
                general_nested,
                selected_relative_roots=("Italiano", "Estero"),
                excluded_relative_roots=("Archivio",),
                include_root_files=False,
            )
        )

        rows = self._read_rows(result.report_paths["csv"])
        by_name = {row["Nome normalizzato"]: row for row in rows}

        self.assertEqual(result.general_unique_titles, 2)
        self.assertEqual(result.split_unique_titles, 3)
        self.assertEqual(result.matched_both, 2)
        self.assertEqual(result.only_split, 1)
        self.assertEqual(result.only_general, 0)

        self.assertEqual(by_name["brano a.mp3"]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        self.assertEqual(by_name["brano b.mp3"]["Stato"], DiagnosticsStatus.PRESENTE_ENTRAMBI.value)
        self.assertEqual(by_name["brano c.mp3"]["Stato"], DiagnosticsStatus.SOLO_SUDDIVISO.value)
        self.assertNotIn("brano d.mp3", by_name)

        folder_rows = self._read_rows(result.folder_report_paths["csv"])
        folder_by_rel = {row["Percorso relativo"]: row for row in folder_rows}

        self.assertEqual(folder_by_rel["Generico"]["Stato"], "Esclusa automaticamente")
        self.assertEqual(folder_by_rel["Generico"]["Numero MP3 elaborati"], "0")
        self.assertEqual(folder_by_rel["Italiano"]["Numero MP3 rilevati"], "2")
        self.assertEqual(folder_by_rel["Italiano"]["Numero MP3 elaborati"], "2")
        self.assertEqual(folder_by_rel["Estero"]["Numero MP3 rilevati"], "1")
        self.assertEqual(folder_by_rel["Estero"]["Numero MP3 elaborati"], "1")
        self.assertEqual(folder_by_rel["Archivio"]["Numero MP3 rilevati"], "1")
        self.assertEqual(folder_by_rel["Archivio"]["Numero MP3 elaborati"], "0")

    def test_root_files_deselected_does_not_block_selected_children(self) -> None:
        self._write_mp3(self.split / "root.mp3", b"ROOT")
        self._write_mp3(self.split / "A" / "a.mp3", b"A")
        self._write_mp3(self.split / "B" / "b.mp3", b"B")

        result = run_repertory_diagnostics(
            DiagnosticsConfig(
                self.split,
                self.general,
                selected_relative_roots=("A", "B"),
                include_root_files=False,
            )
        )

        normalized = {row["Nome normalizzato"] for row in self._read_rows(result.report_paths["csv"])}
        self.assertIn("a.mp3", normalized)
        self.assertIn("b.mp3", normalized)
        self.assertNotIn("root.mp3", normalized)
        self.assertEqual(result.analyzed_split_files, 2)

    def test_excluded_path_matching_is_precise_for_siblings_and_parent(self) -> None:
        base = self.root / "Base"
        excluded = base / "Generico"
        self.assertTrue(_is_path_in_excluded_roots(excluded, [excluded]))
        self.assertTrue(_is_path_in_excluded_roots(excluded / "Sottocartella", [excluded]))
        self.assertFalse(_is_path_in_excluded_roots(base / "Italiano", [excluded]))
        self.assertFalse(_is_path_in_excluded_roots(base / "Generico2", [excluded]))
        self.assertFalse(_is_path_in_excluded_roots(base / "Altro", [excluded]))
        self.assertFalse(_is_path_in_excluded_roots(base, [excluded]))

    def test_protective_block_when_selected_detected_but_split_scan_returns_zero(self) -> None:
        self._write_mp3(self.split / "Italiano" / "A.mp3", b"A")

        with mock.patch("mp3_repertory_diagnostics._scan_split_repertory", return_value=[]):
            with self.assertRaisesRegex(DiagnosticsError, "Non e stato elaborato alcun file del Repertorio suddiviso"):
                run_repertory_diagnostics(
                    DiagnosticsConfig(
                        self.split,
                        self.general,
                        selected_relative_roots=("Italiano",),
                        include_root_files=False,
                    )
                )
