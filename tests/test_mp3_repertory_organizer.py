# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import mp3_repertory_organizer as organizer
from mp3_repertory_organizer import RepertoryStatus, organize_repertory_from_folders


_CSV_LABEL_TO_KEY = {value: key for key, value in organizer.REPORT_HEADER_LABELS.items()}


class _CancelAfterFirst:
    def __init__(self) -> None:
        self._event = threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


class Mp3RepertoryOrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.updates = self.root / "updates"
        self.repertory = self.root / "repertory"
        self.general = self.root / "general"
        self.results = self.root / "results"
        self.smartphone = self.root / "smartphone_tablet"
        self.updates.mkdir(parents=True, exist_ok=True)
        self.repertory.mkdir(parents=True, exist_ok=True)
        self.general.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.smartphone.mkdir(parents=True, exist_ok=True)
        self._original_smartphone_root = organizer.SMARTPHONE_TABLET_ROOT
        organizer.SMARTPHONE_TABLET_ROOT = self.smartphone

    def tearDown(self) -> None:
        organizer.SMARTPHONE_TABLET_ROOT = self._original_smartphone_root
        self.temp.cleanup()

    def _write_mp3(self, path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _set_source_newer_than_destinations(self, source: Path, *destinations: Path) -> None:
        for destination in destinations:
            os.utime(destination, (1000.0, 1000.0))
        os.utime(source, (2000.0, 2000.0))

    def _read_csv_rows(self, report_csv: str) -> list[dict[str, str]]:
        with Path(report_csv).open("r", encoding="utf-8", newline="") as handle:
            out: list[dict[str, str]] = []
            for row in csv.DictReader(handle):
                out.append({_CSV_LABEL_TO_KEY.get(key, key): value for key, value in row.items()})
            return out

    def _read_csv_headers(self, report_csv: str) -> list[str]:
        with Path(report_csv).open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle).fieldnames or [])

    def _read_xlsx_headers(self, report_xlsx: str) -> list[str]:
        with ZipFile(report_xlsx, "r") as archive:
            sheet_xml = archive.read("xl/worksheets/sheet1.xml")
        root = ET.fromstring(sheet_xml)
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        header_row = root.find(".//x:sheetData/x:row[@r='1']", ns)
        if header_row is None:
            return []
        headers: list[str] = []
        for cell in header_row.findall("x:c", ns):
            text_node = cell.find("x:is/x:t", ns)
            headers.append(text_node.text if text_node is not None and text_node.text is not None else "")
        return headers

    def _read_html_headers(self, report_html: str) -> list[str]:
        html_text = Path(report_html).read_text(encoding="utf-8")
        start = html_text.find("<thead><tr>")
        end = html_text.find("</tr></thead>")
        if start < 0 or end < 0:
            return []
        header_row = html_text[start:end]
        headers: list[str] = []
        cursor = 0
        while True:
            th_start = header_row.find("<th>", cursor)
            if th_start < 0:
                break
            th_end = header_row.find("</th>", th_start)
            if th_end < 0:
                break
            headers.append(header_row[th_start + 4 : th_end])
            cursor = th_end + 5
        return headers

    def test_a_source_empty(self) -> None:
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        self.assertEqual(result.total_source_files, 0)
        self.assertEqual(result.processed_source_files, 0)
        self.assertTrue(Path(result.report_paths["csv"]).is_file())

    def test_b_repertory_empty(self) -> None:
        self._write_mp3(self.updates / "Volare.mp3", b"A")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stato"], "File non trovato nel Repertorio")

    def test_c_single_match(self) -> None:
        source = self._write_mp3(self.updates / "Volare.mp3", b"NEW_DATA")
        dest = self._write_mp3(self.repertory / "Volare.mp3", b"OLD_DATA")
        self._set_source_newer_than_destinations(source, dest)
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.AGGIORNATO.value)
        self.assertEqual(dest.read_bytes(), source.read_bytes())

    def test_d_multiple_matches_in_subfolders(self) -> None:
        source = self._write_mp3(self.updates / "Brano.mp3", b"NEW")
        dest_a = self._write_mp3(self.repertory / "A" / "Brano.mp3", b"OLD_A")
        dest_b = self._write_mp3(self.repertory / "B" / "Brano.mp3", b"OLD_B")
        self._set_source_newer_than_destinations(source, dest_a, dest_b)
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["stato"] == RepertoryStatus.AGGIORNATO_MULTIPLO.value for row in rows))
        self.assertEqual(dest_a.read_bytes(), source.read_bytes())
        self.assertEqual(dest_b.read_bytes(), source.read_bytes())

    def test_d2_counters_track_distinct_updated_sources_and_repertory_copies(self) -> None:
        source = self._write_mp3(self.updates / "Brano.mp3", b"NEW")
        dest_a = self._write_mp3(self.repertory / "A" / "Brano.mp3", b"OLD_A")
        dest_b = self._write_mp3(self.repertory / "B" / "Brano.mp3", b"OLD_B")
        self._set_source_newer_than_destinations(source, dest_a, dest_b)

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )

        self.assertEqual(result.counters.get(organizer.COUNTER_BRANI_AGGIORNATI), 1)
        self.assertEqual(result.counters.get(organizer.COUNTER_COPIE_AGGIORNATE_REPERTORIO), 2)

    def test_e_case_insensitive_match(self) -> None:
        source = self._write_mp3(self.updates / "VOLARE.mp3", b"NEW_CASE")
        dest = self._write_mp3(self.repertory / "volare.mp3", b"OLD_CASE")
        self._set_source_newer_than_destinations(source, dest)
        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        self.assertEqual(dest.read_bytes(), source.read_bytes())

    def test_f_similar_names_not_equivalent(self) -> None:
        self._write_mp3(self.updates / "Volare.mp3", b"NEW")
        self._write_mp3(self.repertory / "Volare_.mp3", b"OLD")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], "File non trovato nel Repertorio")

    def test_g_file_not_found(self) -> None:
        self._write_mp3(self.updates / "NotThere.mp3", b"X")
        self._write_mp3(self.repertory / "Other.mp3", b"Y")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], "File non trovato nel Repertorio")

    def test_h_backup_enabled_preserves_relative_path(self) -> None:
        source = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dest = self._write_mp3(self.repertory / "disc1" / "setA" / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(source, dest)
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            backup_enabled=True,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        backup = Path(rows[0]["percorso_backup"])
        self.assertTrue(backup.is_file())
        self.assertIn(str(Path("disc1") / "setA" / "Song.mp3"), str(backup))

    def test_i_backup_disabled(self) -> None:
        self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            backup_enabled=False,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["backup_eseguito"], "NO")
        self.assertEqual(rows[0]["percorso_backup"], "")

    def test_i2_backup_snapshots_not_found_folder_when_present(self) -> None:
        self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._write_mp3(
            self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME / "preexisting" / "Missing.mp3",
            b"PRE",
        )

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            backup_enabled=True,
        )

        backup_snapshot = (
            Path(result.session_folder)
            / "Backup"
            / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
            / "preexisting"
            / "Missing.mp3"
        )
        self.assertTrue(backup_snapshot.is_file())
        self.assertEqual(backup_snapshot.read_bytes(), b"PRE")

    def test_i3_backup_does_not_create_empty_not_found_snapshot_when_absent(self) -> None:
        self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            backup_enabled=True,
        )

        empty_backup_candidate = Path(result.session_folder) / "Backup" / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        self.assertFalse(empty_backup_candidate.exists())
        log_text = Path(result.log_path).read_text(encoding="utf-8")
        self.assertIn("non presente prima dell'avvio", log_text)

    def test_i4_backup_not_found_snapshot_error_is_reported(self) -> None:
        self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        dedicated = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        self._write_mp3(dedicated / "Missing.mp3", b"PRE")

        original_copytree = organizer.shutil.copytree

        def _fake_copytree(src, dst, *args, **kwargs):
            if Path(src).resolve() == dedicated.resolve():
                raise OSError("backup copytree fail")
            return original_copytree(src, dst, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copytree", side_effect=_fake_copytree):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
                backup_enabled=True,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertTrue(any(row["stato"] == RepertoryStatus.ERRORE_BACKUP.value for row in rows))
        self.assertIn("backup copytree fail", str(result.error or ""))

    def test_j_source_read_error(self) -> None:
        source = self._write_mp3(self.updates / "Bad.mp3", b"X")

        original = organizer._read_size_and_sha256

        def _fake(path: Path):
            if path == source:
                raise OSError("boom")
            return original(path)

        with mock.patch.object(organizer, "_read_size_and_sha256", side_effect=_fake):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_SORGENTE.value)

    def test_k_copy_error(self) -> None:
        source = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dest = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(source, dest)

        original_copy2 = organizer.shutil.copy2

        def _fake_copy2(src, dst, *args, **kwargs):
            if str(dst).endswith(".tmp.mp3"):
                raise OSError("copy fail")
            return original_copy2(src, dst, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2", side_effect=_fake_copy2):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_COPIA.value)

    def test_l_temp_hash_mismatch(self) -> None:
        source = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dest = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(source, dest)

        original = organizer._read_size_and_sha256

        def _fake(path: Path):
            size, digest = original(path)
            if path.suffix == ".mp3" and path.name.startswith(".Song_"):
                return size, "wrong_hash"
            return size, digest

        with mock.patch.object(organizer, "_read_size_and_sha256", side_effect=_fake):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_VERIFICA.value)
        self.assertEqual(source.read_bytes(), b"NEW")

    def test_m_final_hash_mismatch(self) -> None:
        source = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dest = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(source, dest)

        original = organizer._read_size_and_sha256
        state = {"final_called": False}

        def _fake(path: Path):
            size, digest = original(path)
            if path == dest and state["final_called"]:
                return size + 1, digest
            if path == dest and not state["final_called"]:
                state["final_called"] = True
            return size, digest

        with mock.patch.object(organizer, "_read_size_and_sha256", side_effect=_fake):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_VERIFICA.value)
        self.assertEqual(source.read_bytes(), b"NEW")

    def test_n_restore_after_replace_failure(self) -> None:
        source = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dest = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(source, dest)

        original_replace = organizer.os.replace

        def _fake_replace(src, dst):
            if str(dst).endswith("Song.mp3") and str(src).endswith(".tmp.mp3"):
                raise OSError("replace fail")
            return original_replace(src, dst)

        with mock.patch.object(organizer.os, "replace", side_effect=_fake_replace):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
                backup_enabled=True,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_COPIA.value)
        self.assertIn("Ripristino", rows[0]["motivo"])
        self.assertEqual(dest.read_bytes(), b"OLD")

    def test_o_source_casefold_collision(self) -> None:
        a = self._write_mp3(self.updates / "A.mp3", b"A")
        b = self._write_mp3(self.updates / "B.mp3", b"B")

        original_normalize = organizer._normalize_name

        def _fake(name: str) -> str:
            if name in (a.name, b.name):
                return "same-key"
            return original_normalize(name)

        with mock.patch.object(organizer, "_normalize_name", side_effect=_fake):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["stato"] == RepertoryStatus.AMBIGUO.value for row in rows))

    def test_p_interrupt_during_multiple(self) -> None:
        self._write_mp3(self.updates / "a.mp3", b"A")
        self._write_mp3(self.updates / "b.mp3", b"B")
        self._write_mp3(self.updates / "c.mp3", b"C")
        self._write_mp3(self.repertory / "a.mp3", b"A0")
        self._write_mp3(self.repertory / "b.mp3", b"B0")
        self._write_mp3(self.repertory / "c.mp3", b"C0")

        cancel_event = _CancelAfterFirst()

        def _progress(current: int, total: int, message: str) -> None:
            _ = (total, message)
            if current >= 1:
                cancel_event.cancel()

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            cancel_event=cancel_event,
            progress_callback=_progress,
        )

        rows = self._read_csv_rows(result.report_paths["csv"])
        interrupted = [row for row in rows if row["stato"] == RepertoryStatus.INTERROTTO.value]
        self.assertTrue(result.interrupted)
        self.assertGreaterEqual(len(interrupted), 1)

    def test_q_temp_files_removed(self) -> None:
        self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")

        with mock.patch.object(organizer.os, "replace", side_effect=OSError("replace fail")):
            organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        leftovers = list(self.repertory.rglob("*.tmp.mp3"))
        self.assertEqual(leftovers, [])

    def test_r_empty_dirs_removed(self) -> None:
        self._write_mp3(self.updates / "NoMatch.mp3", b"X")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        backup_dir = Path(result.session_folder) / "Backup"
        self.assertFalse(backup_dir.exists())

    def test_s_source_files_unchanged(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"SOURCE_BYTES")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        before = src.read_bytes()

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )

        after = src.read_bytes()
        self.assertEqual(before, after)

    def test_mtime_1_destination_older_updates_without_dialog(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        now = 2000000.0
        os_times_src = (now, now)
        os_times_dst = (now - 10.0, now - 10.0)
        src.touch()
        dst.touch()
        os.utime(src, os_times_src)
        os.utime(dst, os_times_dst)

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_CURRENT"

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(calls["count"], 0)
        self.assertEqual(rows[0]["stato"], RepertoryStatus.AGGIORNATO.value)

    def test_mtime_2_same_timestamp_requires_decision(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        now = 2200000.0
        os.utime(src, (now, now))
        os.utime(dst, (now, now))

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_CURRENT"

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(calls["count"], 1)
        self.assertEqual(rows[0]["stato"], RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value)
        self.assertEqual(rows[0]["motivo_confronto"], "Stessa data e ora di modifica")
        self.assertEqual(rows[0]["differenza_temporale"], "Stessa data e ora")

    def test_mtime_3_destination_newer_requires_decision(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(self.repertory / "Song.mp3", (2000.0, 2000.0))

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_CURRENT"

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        self.assertEqual(calls["count"], 1)

    def test_mtime_4_update_current_keeps_control_active(self) -> None:
        src1 = self._write_mp3(self.updates / "A.mp3", b"A_NEW")
        src2 = self._write_mp3(self.updates / "B.mp3", b"B_NEW")
        dst1 = self._write_mp3(self.repertory / "A.mp3", b"A_OLD")
        self._write_mp3(self.repertory / "B.mp3", b"B_OLD")
        os.utime(src1, (1000.0, 1000.0))
        os.utime(dst1, (2000.0, 2000.0))
        os.utime(src2, (1000.0, 1000.0))
        os.utime(self.repertory / "B.mp3", (2000.0, 2000.0))

        decisions: list[str] = []

        def _decision(_payload):
            decisions.append("asked")
            return "UPDATE_CURRENT"

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        self.assertEqual(len(decisions), 2)
        self.assertEqual(dst1.read_bytes(), src1.read_bytes())

    def test_mtime_5_skip_current_keeps_destination_and_no_backup(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (2000.0, 2000.0))

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=lambda _payload: "SKIP_CURRENT",
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value)
        self.assertEqual(rows[0]["backup_eseguito"], "NO")
        self.assertEqual(rows[0]["copia_eseguita"], "NO")
        self.assertEqual(rows[0]["verifica_finale"], "NON_ESEGUITA")
        self.assertEqual(dst.read_bytes(), b"OLD")

    def test_mtime_6_update_and_bypass_session(self) -> None:
        src1 = self._write_mp3(self.updates / "A.mp3", b"A_NEW")
        src2 = self._write_mp3(self.updates / "B.mp3", b"B_NEW")
        self._write_mp3(self.repertory / "A.mp3", b"A_OLD")
        self._write_mp3(self.repertory / "B.mp3", b"B_OLD")
        os.utime(src1, (1000.0, 1000.0))
        os.utime(self.repertory / "A.mp3", (2000.0, 2000.0))
        os.utime(src2, (1000.0, 1000.0))
        os.utime(self.repertory / "B.mp3", (3000.0, 3000.0))

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "UPDATE_AND_BYPASS_SESSION"

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(calls["count"], 1)
        self.assertEqual(rows[0]["decisione_utente"], "Aggiornato manualmente")
        self.assertEqual(rows[1]["controllo_data_ora_eseguito"], "NO")
        self.assertEqual(rows[1]["decisione_utente"], "Aggiornato automaticamente per scelta di sessione")

    def test_mtime_7_skip_and_bypass_session(self) -> None:
        src1 = self._write_mp3(self.updates / "A.mp3", b"A_NEW")
        src2 = self._write_mp3(self.updates / "B.mp3", b"B_NEW")
        dst1 = self._write_mp3(self.repertory / "A.mp3", b"A_OLD")
        dst2 = self._write_mp3(self.repertory / "B.mp3", b"B_OLD")
        os.utime(src1, (1000.0, 1000.0))
        os.utime(dst1, (2000.0, 2000.0))
        os.utime(src2, (1000.0, 1000.0))
        os.utime(dst2, (3000.0, 3000.0))

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_AND_BYPASS_SESSION"

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(calls["count"], 1)
        self.assertEqual(rows[0]["decisione_utente"], "Mantenuto manualmente")
        self.assertEqual(rows[0]["stato"], RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value)
        self.assertEqual(rows[1]["decisione_utente"], "Mantenuto automaticamente per scelta di sessione")
        self.assertEqual(rows[1]["stato"], RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value)
        self.assertEqual(dst1.read_bytes(), b"A_OLD")
        self.assertEqual(dst2.read_bytes(), b"B_OLD")

    def test_mtime_8_multiple_destinations_independent(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        old_dst = self._write_mp3(self.repertory / "A" / "Song.mp3", b"OLD_A")
        new_dst = self._write_mp3(self.repertory / "B" / "Song.mp3", b"OLD_B")
        os.utime(src, (1000.0, 1000.0))
        os.utime(old_dst, (900.0, 900.0))
        os.utime(new_dst, (2000.0, 2000.0))

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=lambda payload: "SKIP_CURRENT" if Path(payload["destination_path"]).parts[-2:] == ("B", "Song.mp3") else "UPDATE_CURRENT",
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        states = {row["percorso_destinazione"]: row["stato"] for row in rows}
        self.assertIn(RepertoryStatus.AGGIORNATO_MULTIPLO.value, states.values())
        self.assertIn(RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value, states.values())

    def test_mtime_8b_equal_timestamp_respects_bypass_policy(self) -> None:
        src1 = self._write_mp3(self.updates / "A.mp3", b"A_NEW")
        src2 = self._write_mp3(self.updates / "B.mp3", b"B_NEW")
        self._write_mp3(self.repertory / "A.mp3", b"A_OLD")
        self._write_mp3(self.repertory / "B.mp3", b"B_OLD")
        os.utime(src1, (1000.0, 1000.0))
        os.utime(self.repertory / "A.mp3", (2000.0, 2000.0))
        os.utime(src2, (3000.0, 3000.0))
        os.utime(self.repertory / "B.mp3", (3000.0, 3000.0))

        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_AND_BYPASS_SESSION"

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(calls["count"], 1)
        self.assertEqual(rows[1]["motivo_confronto"], "Stessa data e ora di modifica")
        self.assertEqual(rows[1]["decisione_utente"], "Mantenuto automaticamente per scelta di sessione")
        self.assertEqual(rows[1]["stato"], RepertoryStatus.SALTATO_FILE_DESTINAZIONE_PIU_RECENTE.value)

    def test_mtime_9_interrupt_while_waiting_decision(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (2000.0, 2000.0))

        cancel_event = _CancelAfterFirst()

        def _decision(_payload):
            cancel_event.cancel()
            return None

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            cancel_event=cancel_event,
            decision_callback=_decision,
        )
        self.assertTrue(result.interrupted)
        self.assertEqual(dst.read_bytes(), b"OLD")

    def test_mtime_11_report_contains_new_columns(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (900.0, 900.0))

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        csv_headers = self._read_csv_headers(result.report_paths["csv"])
        rows = self._read_csv_rows(result.report_paths["csv"])
        xlsx_headers = self._read_xlsx_headers(result.report_paths["xlsx"])
        html_headers = self._read_html_headers(result.report_paths["html"])
        expected_leading_headers = [
            "File sorgente",
            "Percorso sorgente",
            "Data/Ora sorgente",
            "File repertorio",
            "Percorso repertorio",
            "Data/Ora repertorio",
            "Differenza temporale",
            "Motivo confronto",
            "Esito",
            "Decisione",
            "Motivo/Note",
            "Copiato Smartphone/Tablet",
            "Percorso copia Smartphone/Tablet",
            "Errore copia Smartphone/Tablet",
            "Repertorio Generale aggiornato",
            "Percorso Repertorio Generale",
            "File presente precedentemente nel Repertorio Generale",
            "Backup Repertorio Generale eseguito",
            "Percorso backup Repertorio Generale",
            "Esito backup Repertorio Generale",
            "Dettaglio errore backup Repertorio Generale",
            "Percorso copia Smartphone/Tablet Repertorio Generale",
            "Errore copia Smartphone/Tablet Repertorio Generale",
            "Percorso File Non trovato",
            "Timestamp sorgente",
            "Timestamp repertorio",
        ]
        self.assertEqual(csv_headers[: len(expected_leading_headers)], expected_leading_headers)
        self.assertEqual(xlsx_headers[: len(expected_leading_headers)], expected_leading_headers)
        self.assertEqual(html_headers[: len(expected_leading_headers)], expected_leading_headers)
        expected = {
            "data_ora_sorgente",
            "timestamp_sorgente",
            "data_ora_destinazione_precedente",
            "timestamp_destinazione_precedente",
            "file_repertorio",
            "differenza_temporale",
            "motivo_confronto",
            "destinazione_piu_recente",
            "controllo_data_ora_eseguito",
            "decisione_utente",
            "bypass_data_ora_sessione",
            "aggiornamento_saltato_per_data_ora",
            "percorso_file_non_trovato",
        }
        self.assertTrue(expected.issubset(set(rows[0].keys())))
        self.assertNotIn("Il file", rows[0]["differenza_temporale"])
        self.assertEqual(rows[0]["motivo_confronto"], "File della cartella Aggiornamenti più recente")

    def test_not_found_1_creates_dedicated_folder_and_copies_file(self) -> None:
        src = self._write_mp3(self.updates / "NuovoBrano.mp3", b"NEW")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )

        dedicated = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        copied = dedicated / "NuovoBrano.mp3"
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertTrue(dedicated.is_dir())
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_bytes(), src.read_bytes())
        self.assertEqual(rows[0]["stato"], "File non trovato nel Repertorio")
        self.assertEqual(rows[0]["decisione_utente"], "Copiato in \"File Non trovati in Repertorio\"")
        self.assertEqual(rows[0]["motivo"], "Nessuna corrispondenza trovata nel Repertorio")
        self.assertEqual(rows[0]["percorso_destinazione"], "Non trovato")
        self.assertEqual(rows[0]["copiato_smartphone_tablet"], "Non applicabile")
        self.assertEqual(rows[0]["errore_copia_smartphone_tablet"], "")
        self.assertEqual(rows[0]["percorso_file_non_trovato"], str(copied))
        self.assertEqual(result.counters.get(organizer.COUNTER_SMARTPHONE_TABLET_COPIATI), 0)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO), 1)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_COPIATI), 1)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_ERRORI_COPIA), 0)
        self.assertEqual(result.repertory_not_found_dir, str(dedicated.resolve()))

    def test_not_found_2_folder_not_created_when_not_needed(self) -> None:
        src = self._write_mp3(self.updates / "Volare.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Volare.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, dst)

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        dedicated = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        self.assertFalse(dedicated.exists())

    def test_not_found_3_duplicate_name_uses_progressive_suffix(self) -> None:
        self._write_mp3(self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME / "Volare.mp3", b"OLD_KEEP")
        src = self._write_mp3(self.updates / "Volare.mp3", b"NEW")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        dedicated = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        copied = dedicated / "Volare (1).mp3"
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertTrue((dedicated / "Volare.mp3").is_file())
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_bytes(), src.read_bytes())
        self.assertEqual(rows[0]["percorso_file_non_trovato"], str(copied))

    def test_not_found_4_copy_error_is_counted_and_reported(self) -> None:
        self._write_mp3(self.updates / "Err.mp3", b"NEW")

        original_copy2 = organizer.shutil.copy2

        def _fake_copy2(source, target, *args, **kwargs):
            if str(target).endswith("Err.mp3") and organizer.REPERTORY_NON_TROVATI_FOLDER_NAME in str(target):
                raise OSError("non trovato copy fail")
            return original_copy2(source, target, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2", side_effect=_fake_copy2):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertIn("Errore copia file non trovato", rows[0]["motivo"])
        self.assertEqual(rows[0]["decisione_utente"], "Errore copia in \"File Non trovati in Repertorio\"")
        self.assertEqual(rows[0]["percorso_file_non_trovato"], "")
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_ERRORI_COPIA), 1)

    def test_not_found_5_report_consistency_csv_html_xlsx(self) -> None:
        self._write_mp3(self.updates / "OnlyMissing.mp3", b"NEW")
        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
        )
        csv_headers = self._read_csv_headers(result.report_paths["csv"])
        xlsx_headers = self._read_xlsx_headers(result.report_paths["xlsx"])
        html_headers = self._read_html_headers(result.report_paths["html"])
        self.assertEqual(csv_headers, xlsx_headers)
        self.assertEqual(csv_headers, html_headers)

    def test_not_found_6_missing_in_both_goes_to_session_root_brani_non_trovati_da_inserire(self) -> None:
        src = self._write_mp3(self.updates / "DaInserire.mp3", b"NEW")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
        )

        rows = self._read_csv_rows(result.report_paths["csv"])
        destination = Path(result.repertory_to_insert_dir) / "DaInserire.mp3"
        self.assertTrue(destination.is_file())
        self.assertEqual(destination.read_bytes(), src.read_bytes())
        self.assertEqual(Path(result.repertory_to_insert_dir).name, organizer.REPERTORY_TO_INSERT_FOLDER_NAME)
        self.assertEqual(Path(result.repertory_to_insert_dir).parent.resolve(), Path(result.session_folder).resolve())
        self.assertEqual(rows[0]["stato"], "Brano non trovato da inserire manualmente")
        self.assertEqual(rows[0]["decisione_utente"], "Copiato in \"Brani non trovati in Repertorio da inserire\"")
        self.assertEqual(rows[0]["percorso_destinazione"], "Da inserire")
        self.assertEqual(rows[0]["copiato_smartphone_tablet"], "Non applicabile")
        self.assertEqual(rows[0]["percorso_file_non_trovato"], str(destination))
        self.assertEqual(result.counters.get(organizer.COUNTER_BRANI_DA_INSERIRE), 1)
        self.assertEqual(result.counters.get(organizer.COUNTER_BRANI_DA_INSERIRE_ERRORI), 0)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO), 0)
        self.assertFalse((Path(result.session_folder) / "Diagnosi").exists())

    def test_not_found_7_split_hit_and_general_missing_adds_note(self) -> None:
        src = self._write_mp3(self.updates / "SoloSplit.mp3", b"NEW")
        split_dest = self._write_mp3(self.repertory / "DiskA" / "SoloSplit.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, split_dest)

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertIn("Inserito automaticamente nel Repertorio Generale", rows[0]["motivo"])
        self.assertEqual(rows[0]["repertorio_generale_aggiornato"], "Si")
        self.assertEqual(rows[0]["file_presente_precedentemente_repertorio_generale"], "NO")
        self.assertEqual(rows[0]["backup_repertorio_generale_eseguito"], "NO")

    def test_not_found_8_real_case_three_in_general_one_missing_in_both(self) -> None:
        for name in ("Brano A.mp3", "Brano B.mp3", "Brano C.mp3", "Brano D.mp3"):
            self._write_mp3(self.updates / name, b"UPD")
        for name in ("Brano A.mp3", "Brano B.mp3", "Brano C.mp3"):
            self._write_mp3(self.general / name, b"GEN")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )

        not_found_dir = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        to_insert_dir = Path(result.repertory_to_insert_dir)
        self.assertEqual(sorted(path.name for path in not_found_dir.glob("*.mp3")), ["Brano A.mp3", "Brano B.mp3", "Brano C.mp3"])
        self.assertEqual(sorted(path.name for path in to_insert_dir.glob("*.mp3")), ["Brano D.mp3"])

        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO), 3)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_COPIATI), 3)
        self.assertEqual(result.counters.get(organizer.COUNTER_BRANI_DA_INSERIRE), 1)
        self.assertEqual(result.counters.get(organizer.COUNTER_SMARTPHONE_TABLET_COPIATI), 0)

        rows = self._read_csv_rows(result.report_paths["csv"])
        by_name = {row["file_sorgente"]: row for row in rows}
        self.assertEqual(by_name["Brano A.mp3"]["stato"], "File non trovato nel Repertorio")
        self.assertEqual(by_name["Brano D.mp3"]["stato"], "Brano non trovato da inserire manualmente")

    def test_not_found_9_five_category_a_files_are_all_copied(self) -> None:
        for name in ("A.mp3", "B.mp3", "C.mp3", "D.mp3", "E.mp3"):
            self._write_mp3(self.updates / name, b"UPD")
            self._write_mp3(self.general / name, b"GEN")

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )

        not_found_dir = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        copied_names = sorted(path.name for path in not_found_dir.glob("*.mp3"))
        self.assertEqual(copied_names, ["A.mp3", "B.mp3", "C.mp3", "D.mp3", "E.mp3"])
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_COPIATI), 5)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO), 5)

    def test_not_found_10_copy_error_on_one_file_does_not_stop_loop(self) -> None:
        for name in ("A.mp3", "B.mp3", "C.mp3", "D.mp3", "E.mp3"):
            self._write_mp3(self.updates / name, b"UPD")
            self._write_mp3(self.general / name, b"GEN")

        original_copy2 = organizer.shutil.copy2

        def _fake_copy2(source, target, *args, **kwargs):
            if str(target).endswith("C.mp3") and organizer.REPERTORY_NON_TROVATI_FOLDER_NAME in str(target):
                raise OSError("forced copy error for C")
            return original_copy2(source, target, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2", side_effect=_fake_copy2):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                repertory_general_dir=self.general,
                results_dir=self.results,
                smartphone_tablet_dir=self.smartphone,
            )

        not_found_dir = self.repertory / organizer.REPERTORY_NON_TROVATI_FOLDER_NAME
        copied_names = sorted(path.name for path in not_found_dir.glob("*.mp3"))
        self.assertEqual(copied_names, ["A.mp3", "B.mp3", "D.mp3", "E.mp3"])
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO), 5)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_COPIATI), 4)
        self.assertEqual(result.counters.get(organizer.COUNTER_FILE_NON_TROVATI_ERRORI_COPIA), 1)

        rows = self._read_csv_rows(result.report_paths["csv"])
        rows_by_name = {row["file_sorgente"]: row for row in rows}
        self.assertIn("Errore copia file non trovato", rows_by_name["C.mp3"]["motivo"])
        self.assertEqual(rows_by_name["D.mp3"]["decisione_utente"], "Copiato in \"File Non trovati in Repertorio\"")

    def test_mtime_12_decision_payload_contains_delta(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (5000.0, 5000.0))

        captured: list[dict[str, str]] = []

        def _decision(payload):
            captured.append(payload)
            return "SKIP_CURRENT"

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )

        self.assertEqual(len(captured), 1)
        self.assertGreater(float(captured[0].get("mtime_delta_seconds", 0.0)), 0.0)
        self.assertTrue(bool(str(captured[0].get("mtime_delta_human", "")).strip()))
        self.assertEqual(captured[0].get("comparison_reason"), "File del repertorio più recente")
        self.assertIn("vecchio", str(captured[0].get("comparison_summary", "")))

    def test_mtime_12b_human_delta_format_uses_compact_and_plural(self) -> None:
        self.assertEqual(organizer._format_time_delta_human(24 * 3600), "1 giorno")
        self.assertEqual(
            organizer._format_time_delta_human((2 * 24 * 3600) + (3 * 3600) + (18 * 60)),
            "2 giorni, 3 ore e 18 minuti",
        )
        self.assertEqual(
            organizer._format_time_delta_human((365 * 24 * 3600) + (2 * 30 * 24 * 3600) + (5 * 24 * 3600) + (3 * 3600) + 12),
            "1 anno, 2 mesi, 5 giorni, 3 ore e 12 secondi",
        )
        self.assertEqual(organizer._format_mtime_delta_compact(1000.0, 1000.0), "Stessa data e ora")

    def test_mtime_13_bypass_not_persisted_across_sessions(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (2000.0, 2000.0))

        first = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=lambda _payload: "UPDATE_AND_BYPASS_SESSION",
        )
        rows_first = self._read_csv_rows(first.report_paths["csv"])
        self.assertEqual(rows_first[0]["bypass_data_ora_sessione"], "SI")

        # restore newer destination for second run to require decision again
        self._write_mp3(self.repertory / "Song.mp3", b"OLD_AGAIN")
        os.utime(self.repertory / "Song.mp3", (3000.0, 3000.0))
        calls = {"count": 0}

        def _decision(_payload):
            calls["count"] += 1
            return "SKIP_CURRENT"

        organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            decision_callback=_decision,
        )
        self.assertEqual(calls["count"], 1)

    def test_smartphone_copy_1_updated_file_is_copied(self) -> None:
        src = self._write_mp3(self.updates / "Italiano" / "Volare.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Italiano" / "Volare.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, dst)

        result = organize_repertory_from_folders(
            updates_dir=self.updates / "Italiano",
            repertory_dir=self.repertory,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        copied_path = self.smartphone / "Italiano" / "Volare.mp3"
        self.assertTrue(copied_path.is_file())
        self.assertEqual(rows[0]["copiato_smartphone_tablet"], "Si")
        self.assertEqual(rows[0]["percorso_copia_smartphone_tablet"], str(copied_path))
        self.assertEqual(rows[0]["errore_copia_smartphone_tablet"], "")

    def test_smartphone_copy_2_kept_file_is_not_copied(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        os.utime(src, (1000.0, 1000.0))
        os.utime(dst, (2000.0, 2000.0))

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
            decision_callback=lambda _payload: "SKIP_CURRENT",
        )
        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["copiato_smartphone_tablet"], "No")
        self.assertEqual(rows[0]["percorso_copia_smartphone_tablet"], "")

    def test_smartphone_copy_3_error_is_reported_without_rollback(self) -> None:
        src = self._write_mp3(self.updates / "Song.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Song.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, dst)

        original_copy2 = organizer.shutil.copy2

        def _fake_copy2(source, target, *args, **kwargs):
            if str(target).endswith("Song.mp3") and str(self.smartphone) in str(target):
                raise OSError("smartphone copy fail")
            return original_copy2(source, target, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2", side_effect=_fake_copy2):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                results_dir=self.results,
                smartphone_tablet_dir=self.smartphone,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.AGGIORNATO.value)
        self.assertEqual(rows[0]["copiato_smartphone_tablet"], "Errore")
        self.assertIn("smartphone copy fail", rows[0]["errore_copia_smartphone_tablet"])
        self.assertEqual(dst.read_bytes(), b"NEW")

    def test_smartphone_copy_4_cumulative_preserves_unrelated_files(self) -> None:
        stale = self._write_mp3(self.smartphone / "keep" / "old.mp3", b"OLD_KEEP")
        src = self._write_mp3(self.updates / "Eventi" / "Matrimoni" / "Volare.mp3", b"NEW")
        dst = self._write_mp3(self.repertory / "Eventi" / "Matrimoni" / "Volare.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, dst)

        organize_repertory_from_folders(
            updates_dir=self.updates / "Eventi" / "Matrimoni",
            repertory_dir=self.repertory,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )
        copied = self.smartphone / "Eventi" / "Matrimoni" / "Volare.mp3"
        self.assertTrue(copied.is_file())
        self.assertTrue(stale.is_file())

    def test_general_sync_1_updates_general_and_smartphone_general_when_configured(self) -> None:
        src = self._write_mp3(self.updates / "Latin.mp3", b"NEW")
        dst_split = self._write_mp3(self.repertory / "Dance" / "Latin.mp3", b"OLD")
        self._set_source_newer_than_destinations(src, dst_split)

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )

        rows = self._read_csv_rows(result.report_paths["csv"])
        general_target = self.general / "Latin.mp3"
        smartphone_general_target = self.smartphone / organizer.REPERTORY_GENERAL_TECH_FOLDER_NAME / "Latin.mp3"
        self.assertTrue(general_target.is_file())
        self.assertTrue(smartphone_general_target.is_file())
        self.assertEqual(general_target.read_bytes(), b"NEW")
        self.assertEqual(smartphone_general_target.read_bytes(), b"NEW")
        self.assertEqual(rows[0]["repertorio_generale_aggiornato"], "Si")
        self.assertEqual(rows[0]["percorso_repertorio_generale"], str(general_target))
        self.assertEqual(rows[0]["file_presente_precedentemente_repertorio_generale"], "NO")

    def test_general_sync_2_existing_general_file_is_backed_up_before_update(self) -> None:
        src = self._write_mp3(self.updates / "Storia.mp3", b"NEW")
        split_dest = self._write_mp3(self.repertory / "Dance" / "Storia.mp3", b"OLD_SPLIT")
        general_dest = self._write_mp3(self.general / "Storia.mp3", b"OLD_GENERAL")
        self._set_source_newer_than_destinations(src, split_dest, general_dest)

        result = organize_repertory_from_folders(
            updates_dir=self.updates,
            repertory_dir=self.repertory,
            repertory_general_dir=self.general,
            results_dir=self.results,
            smartphone_tablet_dir=self.smartphone,
        )

        rows = self._read_csv_rows(result.report_paths["csv"])
        backup_path = Path(rows[0]["percorso_backup_repertorio_generale"])
        self.assertEqual(rows[0]["file_presente_precedentemente_repertorio_generale"], "SI")
        self.assertEqual(rows[0]["backup_repertorio_generale_eseguito"], "SI")
        self.assertEqual(rows[0]["esito_backup_repertorio_generale"], "OK")
        self.assertTrue(backup_path.is_file())
        self.assertIn(organizer.REPERTORY_GENERAL_TECH_FOLDER_NAME, str(backup_path))
        self.assertEqual(backup_path.read_bytes(), b"OLD_GENERAL")
        self.assertEqual((self.general / "Storia.mp3").read_bytes(), b"NEW")

    def test_general_sync_3_backup_error_blocks_general_update(self) -> None:
        src = self._write_mp3(self.updates / "ErroreBackup.mp3", b"NEW")
        split_dest = self._write_mp3(self.repertory / "Dance" / "ErroreBackup.mp3", b"OLD_SPLIT")
        general_dest = self._write_mp3(self.general / "ErroreBackup.mp3", b"OLD_GENERAL")
        self._set_source_newer_than_destinations(src, split_dest, general_dest)

        original_copy2 = organizer.shutil.copy2

        def _fake_copy2(source, target, *args, **kwargs):
            if organizer.REPERTORY_GENERAL_TECH_FOLDER_NAME in str(target):
                raise OSError("general backup fail")
            return original_copy2(source, target, *args, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2", side_effect=_fake_copy2):
            result = organize_repertory_from_folders(
                updates_dir=self.updates,
                repertory_dir=self.repertory,
                repertory_general_dir=self.general,
                results_dir=self.results,
                smartphone_tablet_dir=self.smartphone,
            )

        rows = self._read_csv_rows(result.report_paths["csv"])
        self.assertEqual(rows[0]["stato"], RepertoryStatus.ERRORE_BACKUP.value)
        self.assertEqual(rows[0]["esito_backup_repertorio_generale"], "Errore")
        self.assertIn("general backup fail", rows[0]["dettaglio_errore_backup_repertorio_generale"])
        self.assertEqual((self.general / "ErroreBackup.mp3").read_bytes(), b"OLD_GENERAL")
        self.assertFalse(result.success)

    def test_smartphone_reset_5_keeps_root_and_counts_deleted_items(self) -> None:
        self._write_mp3(self.smartphone / "a.mp3", b"A")
        self._write_mp3(self.smartphone / "folder" / "b.mp3", b"B")

        files, folders = organizer.reset_smartphone_tablet_dir(
            self.smartphone,
            expected_root=self.smartphone,
        )
        self.assertEqual(files, 2)
        self.assertEqual(folders, 1)
        self.assertTrue(self.smartphone.is_dir())
        self.assertEqual(list(self.smartphone.iterdir()), [])

    def test_smartphone_reset_6_rejects_mismatched_root(self) -> None:
        with self.assertRaises(RuntimeError):
            organizer.reset_smartphone_tablet_dir(
                self.smartphone,
                expected_root=self.results,
            )

    def test_smartphone_reset_7_rejects_filesystem_root(self) -> None:
        root_path = Path(self.smartphone.anchor)
        if not str(root_path).strip():
            self.skipTest("Filesystem root anchor unavailable")
        with self.assertRaises(RuntimeError):
            organizer.reset_smartphone_tablet_dir(root_path)


if __name__ == "__main__":
    unittest.main()
