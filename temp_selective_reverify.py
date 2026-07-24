from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mp3_diagnostics import MP3DiagnosticsEngine, PLACEMENT_MODE_COPY

PREV_SUMMARY = Path(r"C:\BASI Organizzate\Generico\Diagnostica_pre_fix\REPORT\Riepilogo_File.csv")
PREV_PROBLEMS = Path(r"C:\BASI Organizzate\Generico\Diagnostica_pre_fix\REPORT\Dettaglio_Problemi.csv")
NEW_BASE_OUTPUT = Path(r"C:\BASI Organizzate\Riverifica diagnostica")

TARGET_STATUSES = {"Riparato", "Non recuperabile"}
FORBIDDEN_SEGMENTS = {
    "\\file riparati\\",
    "\\non recuperabili\\",
    "\\file già rilevati ok\\",
    "\\originali dei file elaborati\\",
}


@dataclass
class PreviousInfo:
    original_path: str
    file_name: str
    prev_status: str
    prev_category: str
    prev_significant_end: str
    prev_trailing_silence: str
    prev_zone: str


class SingleFileEngine(MP3DiagnosticsEngine):
    def __init__(self, target_file: Path) -> None:
        super().__init__()
        self.target_file = target_file.resolve()

    def _scan_files(self, *, source_dir: Path, include_subfolders: bool, excluded_roots: list[Path]) -> list[Path]:
        _ = (source_dir, include_subfolders, excluded_roots)
        return [self.target_file]


def normalize_path_key(value: str) -> str:
    return str(Path(value).resolve()).casefold()


def load_previous() -> tuple[list[PreviousInfo], dict[str, str], list[str]]:
    errors: list[str] = []
    rows: list[PreviousInfo] = []
    zone_by_path: dict[str, str] = {}

    try:
        with PREV_PROBLEMS.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                path = (row.get("Percorso") or "").strip()
                zone = (row.get("Posizione rispetto all'audio significativo") or "").strip()
                if path and zone and path not in zone_by_path:
                    zone_by_path[path] = zone
    except Exception as exc:  # pragma: no cover
        errors.append(f"Errore lettura Dettaglio_Problemi.csv: {exc}")

    try:
        with PREV_SUMMARY.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                status = (row.get("Stato finale file") or "").strip()
                if status not in TARGET_STATUSES:
                    continue
                rows.append(
                    PreviousInfo(
                        original_path=(row.get("Percorso originale") or "").strip(),
                        file_name=(row.get("File") or "").strip(),
                        prev_status=status,
                        prev_category=(row.get("Categoria finale") or "").strip(),
                        prev_significant_end=(row.get("Fine audio significativo") or "").strip(),
                        prev_trailing_silence=(row.get("Silenzio finale (ms)") or "").strip(),
                        prev_zone=zone_by_path.get((row.get("Percorso originale") or "").strip(), ""),
                    )
                )
    except Exception as exc:  # pragma: no cover
        errors.append(f"Errore lettura Riepilogo_File.csv: {exc}")

    return rows, zone_by_path, errors


def is_forbidden_generated_path(path_text: str) -> bool:
    low = path_text.casefold()
    return any(seg in low for seg in FORBIDDEN_SEGMENTS)


def read_single_row(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[0] if rows else {}


def build_change_reason(prev: PreviousInfo, new_summary: dict[str, str], new_problem: dict[str, str]) -> str:
    if new_summary.get("Stato finale file", "") == "Originale non trovato":
        return "Originale non trovato"

    changes: list[str] = []
    if prev.prev_status != new_summary.get("Stato finale file", ""):
        changes.append(f"Stato {prev.prev_status} -> {new_summary.get('Stato finale file', '')}")
    if prev.prev_category != new_summary.get("Categoria finale", ""):
        changes.append(f"Categoria {prev.prev_category} -> {new_summary.get('Categoria finale', '')}")
    if prev.prev_zone != new_problem.get("Posizione rispetto all'audio significativo", ""):
        changes.append(
            "Zona "
            f"{prev.prev_zone or 'N/A'} -> {new_problem.get('Posizione rispetto all\'audio significativo', '') or 'N/A'}"
        )
    if prev.prev_significant_end != new_summary.get("Fine audio significativo", ""):
        changes.append("significant_end aggiornato")
    if prev.prev_trailing_silence != new_summary.get("Silenzio finale (ms)", ""):
        changes.append("trailing_silence aggiornato")

    if not changes:
        return "Nessun cambiamento" if prev.prev_status == new_summary.get("Stato finale file", "") else "Riclassificazione"
    return "; ".join(changes)


def main() -> None:
    precheck_only = "--precheck-only" in sys.argv

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = NEW_BASE_OUTPUT / f"run_{timestamp}"
    run_root.mkdir(parents=True, exist_ok=True)
    report_dir = run_root / "REPORT"
    report_dir.mkdir(parents=True, exist_ok=True)

    previous_rows, _zone_map, read_errors = load_previous()

    problematic_count = len(previous_rows)
    unique_map: dict[str, PreviousInfo] = {}
    duplicate_count = 0
    missing_count = 0
    valid_count = 0

    missing_rows: list[dict[str, str]] = []
    selected_rows: list[PreviousInfo] = []

    for prev in previous_rows:
        original_path = prev.original_path
        if not original_path:
            missing_count += 1
            missing_rows.append(
                {
                    "percorso_originale": "",
                    "nome_file": prev.file_name,
                    "stato_precedente": prev.prev_status,
                    "categoria_precedente": prev.prev_category,
                    "motivo": "Originale non trovato",
                }
            )
            continue

        key = normalize_path_key(original_path)
        if key in unique_map:
            duplicate_count += 1
            continue

        unique_map[key] = prev

        if is_forbidden_generated_path(original_path):
            missing_count += 1
            missing_rows.append(
                {
                    "percorso_originale": original_path,
                    "nome_file": prev.file_name,
                    "stato_precedente": prev.prev_status,
                    "categoria_precedente": prev.prev_category,
                    "motivo": "Percorso originale in cartella generata precedente",
                }
            )
            continue

        if not Path(original_path).exists():
            missing_count += 1
            missing_rows.append(
                {
                    "percorso_originale": original_path,
                    "nome_file": prev.file_name,
                    "stato_precedente": prev.prev_status,
                    "categoria_precedente": prev.prev_category,
                    "motivo": "Originale non trovato",
                }
            )
            continue

        valid_count += 1
        selected_rows.append(prev)

    print("PRECHECK")
    print(f"problematic_rows={problematic_count}")
    print(f"valid_original_paths={valid_count}")
    print(f"missing_originals={missing_count}")
    print(f"deduplicated={duplicate_count}")
    print(f"final_reverify_count={len(selected_rows)}")

    if precheck_only:
        return

    comparative_rows: list[dict[str, str]] = []
    per_file_errors: list[dict[str, str]] = []

    for index, prev in enumerate(selected_rows, start=1):
        original_file = Path(prev.original_path)
        per_file_out = run_root / "files" / f"{index:05d}"
        per_file_out.mkdir(parents=True, exist_ok=True)
        try:
            engine = SingleFileEngine(original_file)
            result = engine.run_diagnostics(
                input_folder=str(original_file.parent),
                include_subfolders=False,
                output_folder=str(per_file_out),
                repair_mode=True,
                placement_mode=PLACEMENT_MODE_COPY,
            )

            summary_row = read_single_row(Path(result["report_paths"]["csv_summary"]))
            problem_row = read_single_row(Path(result["report_paths"]["csv_problems"]))

            new_status = summary_row.get("Stato finale file", "")
            new_category = summary_row.get("Categoria finale", "")
            new_zone = problem_row.get("Posizione rispetto all'audio significativo", "")
            new_sig_end = summary_row.get("Fine audio significativo", "")
            new_trailing = summary_row.get("Silenzio finale (ms)", "")
            new_rms = problem_row.get("RMS segmento (dBFS)", "")
            new_peak = problem_row.get("Picco segmento (dBFS)", "")
            ignored = problem_row.get("Problema ignorato ai fini dello stato", "")
            repair_executed = "SI" if new_status == "Riparato" else "NO"
            new_output_path = summary_row.get("Percorso finale", "")

            comparative_rows.append(
                {
                    "percorso originale": prev.original_path,
                    "nome file": prev.file_name,
                    "stato precedente": prev.prev_status,
                    "stato nuovo": new_status,
                    "categoria precedente": prev.prev_category,
                    "categoria nuova": new_category,
                    "zona problema precedente": prev.prev_zone,
                    "zona problema nuova": new_zone,
                    "significant_end precedente": prev.prev_significant_end,
                    "significant_end nuovo": new_sig_end,
                    "trailing_silence precedente": prev.prev_trailing_silence,
                    "trailing_silence nuovo": new_trailing,
                    "RMS": new_rms,
                    "Peak": new_peak,
                    "anomalia ignorata Sì/No": ignored,
                    "riparazione eseguita Sì/No": repair_executed,
                    "percorso nuovo output": new_output_path,
                    "motivo cambiamento": build_change_reason(prev, summary_row, problem_row),
                }
            )
        except Exception as exc:
            per_file_errors.append(
                {
                    "percorso originale": prev.original_path,
                    "nome file": prev.file_name,
                    "errore": str(exc),
                }
            )
            comparative_rows.append(
                {
                    "percorso originale": prev.original_path,
                    "nome file": prev.file_name,
                    "stato precedente": prev.prev_status,
                    "stato nuovo": "Errore di lettura",
                    "categoria precedente": prev.prev_category,
                    "categoria nuova": "",
                    "zona problema precedente": prev.prev_zone,
                    "zona problema nuova": "",
                    "significant_end precedente": prev.prev_significant_end,
                    "significant_end nuovo": "",
                    "trailing_silence precedente": prev.prev_trailing_silence,
                    "trailing_silence nuovo": "",
                    "RMS": "",
                    "Peak": "",
                    "anomalia ignorata Sì/No": "",
                    "riparazione eseguita Sì/No": "NO",
                    "percorso nuovo output": "",
                    "motivo cambiamento": f"Errore di lettura: {exc}",
                }
            )

    for missing in missing_rows:
        comparative_rows.append(
            {
                "percorso originale": missing.get("percorso_originale", ""),
                "nome file": missing.get("nome_file", ""),
                "stato precedente": missing.get("stato_precedente", ""),
                "stato nuovo": "Originale non trovato",
                "categoria precedente": missing.get("categoria_precedente", ""),
                "categoria nuova": "",
                "zona problema precedente": "",
                "zona problema nuova": "",
                "significant_end precedente": "",
                "significant_end nuovo": "",
                "trailing_silence precedente": "",
                "trailing_silence nuovo": "",
                "RMS": "",
                "Peak": "",
                "anomalia ignorata Sì/No": "",
                "riparazione eseguita Sì/No": "NO",
                "percorso nuovo output": "",
                "motivo cambiamento": missing.get("motivo", "Originale non trovato"),
            }
        )

    compare_csv = report_dir / "Riverifica_Comparativa.csv"
    if comparative_rows:
        with compare_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparative_rows[0].keys()))
            writer.writeheader()
            writer.writerows(comparative_rows)
    else:
        compare_csv.write_text("", encoding="utf-8")

    transition_counts = {
        "Riparato->OK": 0,
        "Riparato->Riparato": 0,
        "Riparato->Non recuperabile": 0,
        "Non recuperabile->OK": 0,
        "Non recuperabile->Riparato": 0,
        "Non recuperabile->Non recuperabile": 0,
    }

    for row in comparative_rows:
        old = row.get("stato precedente", "")
        new = row.get("stato nuovo", "")
        if old == "Riparato" and new == "Integro":
            transition_counts["Riparato->OK"] += 1
        elif old == "Riparato" and new == "Riparato":
            transition_counts["Riparato->Riparato"] += 1
        elif old == "Riparato" and new == "Non recuperabile":
            transition_counts["Riparato->Non recuperabile"] += 1
        elif old == "Non recuperabile" and new == "Integro":
            transition_counts["Non recuperabile->OK"] += 1
        elif old == "Non recuperabile" and new == "Riparato":
            transition_counts["Non recuperabile->Riparato"] += 1
        elif old == "Non recuperabile" and new == "Non recuperabile":
            transition_counts["Non recuperabile->Non recuperabile"] += 1

    summary = {
        "previous_report": str(PREV_SUMMARY),
        "previous_problem_report": str(PREV_PROBLEMS),
        "run_root": str(run_root),
        "comparison_csv": str(compare_csv),
        "problematic_rows": problematic_count,
        "valid_original_paths": valid_count,
        "missing_originals": missing_count,
        "deduplicated": duplicate_count,
        "final_reverify_count": len(selected_rows),
        "totale_file_riverificati": len(selected_rows),
        "originali_non_trovati": missing_count,
        "errori_di_lettura": len(read_errors) + len(per_file_errors),
        **transition_counts,
        "read_errors": read_errors,
        "per_file_errors": per_file_errors,
    }

    summary_json = report_dir / "Riverifica_Summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RUN_RESULT")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
