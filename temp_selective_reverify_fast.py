from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
class PrevRow:
    original_path: str
    file_name: str
    prev_status: str
    prev_category: str
    prev_zone: str
    prev_sig_end: str
    prev_trailing: str


class SelectedFilesEngine(MP3DiagnosticsEngine):
    def __init__(self, selected_files: list[Path]) -> None:
        super().__init__()
        self.selected_files = [p.resolve() for p in selected_files]

    def _scan_files(self, *, source_dir: Path, include_subfolders: bool, excluded_roots: list[Path]) -> list[Path]:
        _ = (source_dir, include_subfolders, excluded_roots)
        return sorted(self.selected_files, key=lambda p: p.as_posix().lower())


def normalize_key(path_text: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path_text)))


def is_forbidden(path_text: str) -> bool:
    low = path_text.casefold()
    return any(seg in low for seg in FORBIDDEN_SEGMENTS)


def load_previous() -> tuple[list[PrevRow], list[str]]:
    errors: list[str] = []
    zone_by_path: dict[str, str] = {}

    try:
        with PREV_PROBLEMS.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                p = (row.get("Percorso") or "").strip()
                z = (row.get("Posizione rispetto all'audio significativo") or "").strip()
                if p and z and p not in zone_by_path:
                    zone_by_path[p] = z
    except Exception as exc:
        errors.append(f"Errore lettura Dettaglio_Problemi.csv: {exc}")

    rows: list[PrevRow] = []
    try:
        with PREV_SUMMARY.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                status = (row.get("Stato finale file") or "").strip()
                if status not in TARGET_STATUSES:
                    continue
                op = (row.get("Percorso originale") or "").strip()
                rows.append(
                    PrevRow(
                        original_path=op,
                        file_name=(row.get("File") or "").strip(),
                        prev_status=status,
                        prev_category=(row.get("Categoria finale") or "").strip(),
                        prev_zone=zone_by_path.get(op, ""),
                        prev_sig_end=(row.get("Fine audio significativo") or "").strip(),
                        prev_trailing=(row.get("Silenzio finale (ms)") or "").strip(),
                    )
                )
    except Exception as exc:
        errors.append(f"Errore lettura Riepilogo_File.csv: {exc}")

    return rows, errors


def main() -> None:
    prev_rows, read_errors = load_previous()

    problematic_rows = len(prev_rows)
    dedup = 0
    missing = 0
    selected_prev: list[PrevRow] = []
    selected_files: list[Path] = []
    missing_rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in prev_rows:
        op = row.original_path
        if not op:
            missing += 1
            missing_rows.append({"percorso originale": "", "nome file": row.file_name, "stato precedente": row.prev_status, "categoria precedente": row.prev_category, "motivo": "Originale non trovato"})
            continue

        key = normalize_key(op)
        if key in seen:
            dedup += 1
            continue
        seen.add(key)

        if is_forbidden(op):
            missing += 1
            missing_rows.append({"percorso originale": op, "nome file": row.file_name, "stato precedente": row.prev_status, "categoria precedente": row.prev_category, "motivo": "Percorso originale in cartella generata precedente"})
            continue

        p = Path(op)
        if not p.exists():
            missing += 1
            missing_rows.append({"percorso originale": op, "nome file": row.file_name, "stato precedente": row.prev_status, "categoria precedente": row.prev_category, "motivo": "Originale non trovato"})
            continue

        selected_prev.append(row)
        selected_files.append(p)

    print("PRECHECK")
    print(f"problematic_rows={problematic_rows}")
    print(f"valid_original_paths={len(selected_files)}")
    print(f"missing_originals={missing}")
    print(f"deduplicated={dedup}")
    print(f"final_reverify_count={len(selected_files)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = NEW_BASE_OUTPUT / f"run_fast_{ts}"
    run_root.mkdir(parents=True, exist_ok=True)

    compare_rows: list[dict[str, str]] = []
    per_file_errors: list[dict[str, str]] = []

    if selected_files:
        engine = SelectedFilesEngine(selected_files)
        try:
            result = engine.run_diagnostics(
                input_folder=str(selected_files[0].parent),
                include_subfolders=False,
                output_folder=str(run_root),
                repair_mode=True,
                placement_mode=PLACEMENT_MODE_COPY,
            )

            summary_rows = list(csv.DictReader(Path(result["report_paths"]["csv_summary"]).open("r", encoding="utf-8", newline="")))
            problem_rows = list(csv.DictReader(Path(result["report_paths"]["csv_problems"]).open("r", encoding="utf-8", newline="")))

            new_sum_by_op = {(r.get("Percorso originale") or "").strip(): r for r in summary_rows}
            new_prob_by_op = {(r.get("Percorso") or "").strip(): r for r in problem_rows}

            for prev in selected_prev:
                new_sum = new_sum_by_op.get(prev.original_path, {})
                new_prob = new_prob_by_op.get(prev.original_path, {})

                new_status = new_sum.get("Stato finale file", "")
                new_category = new_sum.get("Categoria finale", "")
                new_zone = new_prob.get("Posizione rispetto all'audio significativo", "")

                if not new_status:
                    per_file_errors.append({"percorso originale": prev.original_path, "nome file": prev.file_name, "errore": "Nessuna riga trovata nel nuovo report"})
                    new_status = "Errore di lettura"

                reason_parts: list[str] = []
                if prev.prev_status != new_status:
                    reason_parts.append(f"Stato {prev.prev_status} -> {new_status}")
                if prev.prev_category != new_category:
                    reason_parts.append(f"Categoria {prev.prev_category} -> {new_category}")
                if prev.prev_zone != new_zone:
                    reason_parts.append(f"Zona {prev.prev_zone or 'N/A'} -> {new_zone or 'N/A'}")
                if prev.prev_sig_end != (new_sum.get("Fine audio significativo") or ""):
                    reason_parts.append("significant_end aggiornato")
                if prev.prev_trailing != (new_sum.get("Silenzio finale (ms)") or ""):
                    reason_parts.append("trailing_silence aggiornato")

                compare_rows.append(
                    {
                        "percorso originale": prev.original_path,
                        "nome file": prev.file_name,
                        "stato precedente": prev.prev_status,
                        "stato nuovo": new_status,
                        "categoria precedente": prev.prev_category,
                        "categoria nuova": new_category,
                        "zona del problema precedente": prev.prev_zone,
                        "zona del problema nuova": new_zone,
                        "significant_end precedente": prev.prev_sig_end,
                        "significant_end nuovo": new_sum.get("Fine audio significativo", ""),
                        "trailing_silence precedente": prev.prev_trailing,
                        "trailing_silence nuovo": new_sum.get("Silenzio finale (ms)", ""),
                        "RMS": new_prob.get("RMS segmento (dBFS)", ""),
                        "Peak": new_prob.get("Picco segmento (dBFS)", ""),
                        "anomalia ignorata Sì/No": new_prob.get("Problema ignorato ai fini dello stato", ""),
                        "riparazione eseguita Sì/No": "SI" if new_status == "Riparato" else "NO",
                        "percorso del nuovo output": new_sum.get("Percorso finale", ""),
                        "motivo dell'eventuale cambiamento": "; ".join(reason_parts) if reason_parts else "Nessun cambiamento",
                    }
                )
        except Exception as exc:
            read_errors.append(f"Errore esecuzione riverifica: {exc}")

    for m in missing_rows:
        compare_rows.append(
            {
                "percorso originale": m.get("percorso originale", ""),
                "nome file": m.get("nome file", ""),
                "stato precedente": m.get("stato precedente", ""),
                "stato nuovo": "Originale non trovato",
                "categoria precedente": m.get("categoria precedente", ""),
                "categoria nuova": "",
                "zona del problema precedente": "",
                "zona del problema nuova": "",
                "significant_end precedente": "",
                "significant_end nuovo": "",
                "trailing_silence precedente": "",
                "trailing_silence nuovo": "",
                "RMS": "",
                "Peak": "",
                "anomalia ignorata Sì/No": "",
                "riparazione eseguita Sì/No": "NO",
                "percorso del nuovo output": "",
                "motivo dell'eventuale cambiamento": m.get("motivo", "Originale non trovato"),
            }
        )

    report_dir = run_root / "REPORT"
    report_dir.mkdir(parents=True, exist_ok=True)
    compare_csv = report_dir / "Riverifica_Comparativa.csv"
    if compare_rows:
        with compare_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(compare_rows[0].keys()))
            writer.writeheader()
            writer.writerows(compare_rows)
    else:
        compare_csv.write_text("", encoding="utf-8")

    transitions = {
        "Riparato->OK": 0,
        "Riparato->Riparato": 0,
        "Riparato->Non recuperabile": 0,
        "Non recuperabile->OK": 0,
        "Non recuperabile->Riparato": 0,
        "Non recuperabile->Non recuperabile": 0,
    }
    for r in compare_rows:
        old = r.get("stato precedente", "")
        new = r.get("stato nuovo", "")
        if old == "Riparato" and new == "Integro":
            transitions["Riparato->OK"] += 1
        elif old == "Riparato" and new == "Riparato":
            transitions["Riparato->Riparato"] += 1
        elif old == "Riparato" and new == "Non recuperabile":
            transitions["Riparato->Non recuperabile"] += 1
        elif old == "Non recuperabile" and new == "Integro":
            transitions["Non recuperabile->OK"] += 1
        elif old == "Non recuperabile" and new == "Riparato":
            transitions["Non recuperabile->Riparato"] += 1
        elif old == "Non recuperabile" and new == "Non recuperabile":
            transitions["Non recuperabile->Non recuperabile"] += 1

    summary = {
        "previous_summary": str(PREV_SUMMARY),
        "previous_problems": str(PREV_PROBLEMS),
        "run_root": str(run_root),
        "comparison_csv": str(compare_csv),
        "problematic_rows": problematic_rows,
        "valid_original_paths": len(selected_files),
        "missing_originals": missing,
        "deduplicated": dedup,
        "final_reverify_count": len(selected_files),
        "totale_file_riverificati": len(selected_files),
        "originali_non_trovati": missing,
        "errori_di_lettura": len(read_errors) + len(per_file_errors),
        **transitions,
        "read_errors": read_errors,
        "per_file_errors": per_file_errors,
    }

    summary_json = report_dir / "Riverifica_Summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RUN_RESULT")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
