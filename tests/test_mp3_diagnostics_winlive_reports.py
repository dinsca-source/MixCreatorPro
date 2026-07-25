# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from mp3_diagnostics import AnalysisResult, AudioBounds, MP3DiagnosticsEngine
from winlive_classification import WinLiveOutcome


class _FakeFFmpegManager:
    def __init__(self) -> None:
        self.ffmpeg_path = Path("ffmpeg")

    def validate(self) -> None:
        return

    def get_duration(self, _path: Path) -> float:
        return 30.0

    @staticmethod
    def _creation_flags() -> int:
        return 0


class _ReportScenarioEngine(MP3DiagnosticsEngine):
    def __init__(self) -> None:
        super().__init__(ffmpeg=_FakeFFmpegManager())
        self.bounds = AudioBounds(
            file_duration_ms=30_000,
            significant_start_ms=0,
            significant_end_ms=30_000,
            leading_silence_ms=0,
            trailing_silence_ms=0,
            detection_confidence=1.0,
            threshold_rms_db=-55.0,
            threshold_peak_db=-45.0,
        )

    def detect_significant_audio_bounds(self, file_path: Path) -> AudioBounds:
        _ = file_path
        return self.bounds

    def _analyze_significant_segment(self, *, file_path: Path, bounds: AudioBounds, cancel_event):
        _ = (file_path, bounds, cancel_event)
        return {
            "header_missing": 0,
            "corrupted_frames": 0,
            "crc_errors": 0,
            "sync_errors": 0,
            "undecodable_frames": 0,
            "invalid_data": 0,
            "xing_issues": 0,
            "vbr_issues": 0,
            "id3_issues": 0,
        }

    def _analyze_mp3(self, *, file_path: Path, cancel_event, segment_ss, segment_t) -> AnalysisResult:
        _ = (file_path, cancel_event, segment_ss, segment_t)
        metrics = {
            "header_missing": 0,
            "corrupted_frames": 0,
            "crc_errors": 0,
            "sync_errors": 0,
            "undecodable_frames": 0,
            "invalid_data": 0,
            "xing_issues": 0,
            "vbr_issues": 0,
            "id3_issues": 0,
        }
        return AnalysisResult(
            command=["ffmpeg", "-i", str(file_path)],
            command_text=f"ffmpeg -i {file_path}",
            return_code=0,
            decode_log="deterministic",
            issues=[],
            metrics=metrics,
            integrity_index=100,
            total_errors=0,
        )


def _frame(length: int = 417, payload_byte: int = 0x11) -> bytes:
    return b"\xFF\xFB\x90\x64" + bytes([payload_byte]) * (length - 4)


def _mp3_blob(synct: bytes | None = None, chord: bytes | None = None, trailing: bytes = b"\x00") -> bytes:
    audio = _frame(payload_byte=0x11) + _frame(payload_byte=0x22) + _frame(payload_byte=0x33)
    parts = [audio]
    if synct is not None:
        parts.append(b"<WL5SYNCT>" + synct + b"/<WL5SYNCT>")
    if chord is not None:
        parts.append(b"<WL5CHORD>" + chord + b"/<WL5CHORD>")
    parts.append(trailing)
    return b"".join(parts)


class MP3DiagnosticsWinLiveReportTests(unittest.TestCase):
    LEGACY_SUMMARY_HEADERS = [
        "Numero",
        "File",
        "Percorso originale",
        "Controllo integrità MP3 eseguito",
        "Stato finale file",
        "Categoria finale",
        "Integrita operativa iniziale",
        "Integrita operativa finale",
        "Errori iniziali",
        "Errori finali",
        "Anomalie tecniche ignorate",
        "Inizio audio significativo",
        "Fine audio significativo",
        "Silenzio iniziale (ms)",
        "Silenzio finale (ms)",
        "File collocato",
        "Tipo file collocato",
        "Percorso finale",
        "Operazione eseguita",
        "Modalità collocazione",
        "Originale conservato",
        "Percorso originale conservato",
        "Operazione effettivamente eseguita",
        "File già presente",
        "Cartella finale",
        "Motivo classificazione finale",
        "Hash SHA-256",
        "Errori bloccanti residui",
        "Comando analisi",
        "Exit code analisi",
        "Comando riparazione",
        "Exit code riparazione",
        "Metodo riparazione",
        "Output riparato",
        "Output non recuperabile",
    ]

    WINLIVE_HEADERS = [
        "Verifica WinLive",
        "Stato WinLive",
        "Presenza WL5SYNCT",
        "Presenza WL5CHORD",
        "Testo presente",
        "Accordi presenti",
        "Accordi non riconosciuti",
        "Token accordi non riconosciuti",
        "Normalizzazione richiesta",
        "Normalizzazione tentata",
        "Normalizzazione validata",
        "Motivo validazione post-modifica",
        "Note WinLive",
        "Codice errore WinLive",
        "Errore WinLive",
        "Cartella esito WinLive",
        "Percorso esito WinLive",
        "Operazione esito WinLive",
        "Catene temporali rilevate",
        "Tag temporali rimossi",
        "Righe solo temporali rilevate",
        "Righe solo temporali eliminate",
    ]

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_input(self, name: str, data: bytes) -> None:
        (self.input_dir / name).write_bytes(data)

    def _run(self, *, verify_winlive: bool, repair_mode: bool = False, verify_mp3_integrity: bool = True) -> dict[str, object]:
        engine = _ReportScenarioEngine()
        return engine.run_diagnostics(
            input_folder=str(self.input_dir),
            include_subfolders=False,
            output_folder=str(self.output_dir),
            repair_mode=repair_mode,
            verify_mp3_integrity=verify_mp3_integrity,
            verify_winlive=verify_winlive,
        )

    @staticmethod
    def _read_csv_rows(path: str) -> tuple[list[str], list[dict[str, str]]]:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
        return headers, rows

    @staticmethod
    def _xlsx_sheet_headers(path: str, sheet_xml: str = "xl/worksheets/sheet1.xml") -> list[str]:
        with zipfile.ZipFile(path, "r") as archive:
            xml_text = archive.read(sheet_xml)

        root = ElementTree.fromstring(xml_text)
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        header_row = root.find(".//x:sheetData/x:row[@r='1']", ns)
        if header_row is None:
            return []

        headers: list[str] = []
        for cell in header_row.findall("x:c", ns):
            text_node = cell.find("x:is/x:t", ns)
            headers.append(text_node.text if text_node is not None and text_node.text is not None else "")
        return headers

    def test_verify_winlive_false_keeps_legacy_csv_headers_identical(self) -> None:
        self._write_input("song.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(verify_winlive=False)

        headers, _rows = self._read_csv_rows(result["report_paths"]["csv_summary"])
        self.assertEqual(headers, self.LEGACY_SUMMARY_HEADERS)
        for name in self.WINLIVE_HEADERS:
            self.assertNotIn(name, headers)

    def test_verify_winlive_true_adds_winlive_columns_to_csv_and_xlsx(self) -> None:
        self._write_input("song.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(verify_winlive=True)

        csv_headers, csv_rows = self._read_csv_rows(result["report_paths"]["csv_summary"])
        self.assertEqual(csv_headers[: len(self.LEGACY_SUMMARY_HEADERS)], self.LEGACY_SUMMARY_HEADERS)
        self.assertEqual(csv_headers[-len(self.WINLIVE_HEADERS) :], self.WINLIVE_HEADERS)
        self.assertEqual(csv_rows[0]["Verifica WinLive"], "SI")

        xlsx_headers = self._xlsx_sheet_headers(result["report_paths"]["xlsx"])
        self.assertEqual(xlsx_headers[: len(self.LEGACY_SUMMARY_HEADERS)], self.LEGACY_SUMMARY_HEADERS)
        self.assertEqual(xlsx_headers[-len(self.WINLIVE_HEADERS) :], self.WINLIVE_HEADERS)

    def test_html_contains_winlive_section_only_when_verification_runs(self) -> None:
        self._write_input("song.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))

        no_verify = self._run(verify_winlive=False)
        html_no_verify = Path(no_verify["report_paths"]["html"]).read_text(encoding="utf-8")
        self.assertNotIn("Sezione WinLive", html_no_verify)

        verify = self._run(verify_winlive=True)
        html_verify = Path(verify["report_paths"]["html"]).read_text(encoding="utf-8")
        self.assertIn("Sezione WinLive", html_verify)
        self.assertIn("Stato MP3", html_verify)
        self.assertIn("Stato WinLive", html_verify)

    def test_winlive_categories_are_exported_in_summary(self) -> None:
        self._write_input("ok.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        self._write_input("norm.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        self._write_input("unrec.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C?|200|"))
        self._write_input("m_txt.mp3", _mp3_blob(None, b"|100|C|200|"))
        self._write_input("m_chr.mp3", _mp3_blob(b"|100|CIAO|200|", None))
        self._write_input("m_both.mp3", _mp3_blob(None, None))
        self._write_input("m_txt_unrec.mp3", _mp3_blob(None, b"|100|C?|200|"))
        self._write_input("to_norm.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        self._write_input(
            "not_validated.mp3",
            _frame() + _frame() + _frame() + b"<WL5SYNCT>|1|A|2|<WL5SYNCT>|3|B|4|/<WL5SYNCT>",
        )

        result = self._run(verify_winlive=True, repair_mode=True)
        _headers, rows = self._read_csv_rows(result["report_paths"]["csv_summary"])
        exported = {row["Stato WinLive"] for row in rows}

        self.assertIn(WinLiveOutcome.FILE_ALREADY_OK.value, exported)
        self.assertIn(WinLiveOutcome.FILE_NORMALIZED.value, exported)
        self.assertIn(WinLiveOutcome.UNRECOGNIZED_CHORDS.value, exported)
        self.assertIn(WinLiveOutcome.MISSING_TEXT_ONLY.value, exported)
        self.assertIn(WinLiveOutcome.MISSING_CHORDS_ONLY.value, exported)
        self.assertIn(WinLiveOutcome.MISSING_TEXT_AND_CHORDS.value, exported)
        self.assertIn(WinLiveOutcome.MISSING_TEXT_AND_UNRECOGNIZED_CHORDS.value, exported)
        self.assertIn(WinLiveOutcome.STRUCTURE_ERROR.value, exported)

    def test_winlive_notes_and_errors_are_exported(self) -> None:
        self._write_input("boom.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        engine = _ReportScenarioEngine()
        with mock.patch("mp3_diagnostics.parse_winlive_blocks_strict", side_effect=RuntimeError("boom")):
            result = engine.run_diagnostics(
                input_folder=str(self.input_dir),
                include_subfolders=False,
                output_folder=str(self.output_dir),
                repair_mode=False,
                verify_winlive=True,
            )

        _headers, rows = self._read_csv_rows(result["report_paths"]["csv_summary"])
        self.assertEqual(len(rows), 1)
        self.assertIn("RuntimeError", rows[0]["Codice errore WinLive"])
        self.assertIn("RuntimeError", rows[0]["Errore WinLive"])
        self.assertTrue(rows[0]["Note WinLive"].strip())

        html_text = Path(result["report_paths"]["html"]).read_text(encoding="utf-8")
        self.assertIn("Note WinLive", html_text)

    def test_report_export_no_rows_no_crash(self) -> None:
        engine = _ReportScenarioEngine()
        report_dir = self.output_dir / "REPORT"
        report_dir.mkdir(parents=True, exist_ok=True)

        report_paths = engine._write_reports([], report_dir, "2026-01-02_03-04-05", include_integrity=True)

        self.assertTrue(Path(report_paths["csv_summary"]).is_file())
        self.assertTrue(Path(report_paths["csv_problems"]).is_file())
        self.assertTrue(Path(report_paths["html"]).is_file())
        self.assertTrue(Path(report_paths["xlsx"]).is_file())

    def test_legacy_compatibility_no_winlive_strings_in_html_xlsx_when_disabled(self) -> None:
        self._write_input("legacy.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(verify_winlive=False)

        html_text = Path(result["report_paths"]["html"]).read_text(encoding="utf-8")
        self.assertNotIn("WinLive", html_text)

        xlsx_headers = self._xlsx_sheet_headers(result["report_paths"]["xlsx"])
        self.assertEqual(xlsx_headers, self.LEGACY_SUMMARY_HEADERS)

    def test_winlive_only_summary_excludes_integrity_columns(self) -> None:
        self._write_input("winlive_only.mp3", _mp3_blob(b"|100|CIAO|200|", b"|100|C|200|"))
        result = self._run(verify_winlive=True, verify_mp3_integrity=False)
        headers, rows = self._read_csv_rows(result["report_paths"]["csv_summary"])
        self.assertIn("Controllo integrità MP3 eseguito", headers)
        self.assertEqual(rows[0]["Controllo integrità MP3 eseguito"], "NO")
        self.assertNotIn("Integrita operativa iniziale", headers)
        self.assertEqual(result["report_paths"].get("integrity_index", ""), "")

    def test_winlive_only_problems_sheet_contains_winlive_rows(self) -> None:
        self._write_input("wlp.mp3", _mp3_blob(b"0|\n|100||200|CIAO|300|\n|350|\n|300|MONDO|500|\n|0||", b"|100|C|200|"))
        result = self._run(verify_winlive=True, verify_mp3_integrity=False, repair_mode=True)
        headers, rows = self._read_csv_rows(result["report_paths"]["csv_problems"])

        self.assertIn("Tipo controllo", headers)
        self.assertIn("Codice", headers)
        self.assertIn("Decisione finale", headers)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(any((r.get("Tipo controllo") or "") == "WinLive" for r in rows))

    def test_human_report_includes_safe_write_split_and_counters(self) -> None:
        self._write_input("song.mp3", _mp3_blob(b"|100||200|CIAO|300|", b"|100|C|200|"))
        result = self._run(verify_winlive=True, verify_mp3_integrity=False, repair_mode=True)

        log_path = Path(result["report_paths"]["html"]).parent / "Log.txt"
        report_text = log_path.read_text(encoding="utf-8")
        self.assertIn("Safe-write totale (ms):", report_text)
        self.assertIn("Validazione totale (ms):", report_text)
        self.assertIn("Tempo non attribuito (ms):", report_text)
        self.assertIn("Contatori diagnostici WinLive:", report_text)


if __name__ == "__main__":
    unittest.main()
