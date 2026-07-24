from __future__ import annotations

import csv
import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty

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
TIMEOUT_SECONDS = 180


@dataclass
class PrevRow:
    original_path: str
    file_name: str
    prev_status: str
    prev_category: str
    prev_zone: str
    prev_sig_end: str
    prev_trailing: str


class SingleFileEngine(MP3DiagnosticsEngine):
    def __init__(self, target_file: Path) -> None:
        super().__init__()
        self.target_file = target_file.resolve()

    def _scan_files(self, *, source_dir: Path, include_subfolders: bool, excluded_roots: list[Path]) -> list[Path]:
        _ = (source_dir, include_subfolders, excluded_roots)
        return [self.target_file]


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
                path = (row.get("Percorso") or "").strip()
                zone = (row.get("Posizione rispetto all'audio significativo") or "").strip()
                if path and zone and path not in zone_by_path:
                    zone_by_path[path] = zone
    except Exception as exc:
        errors.append(f"Errore lettura Dettaglio_Problemi.csv: {exc}")

    selected: list[PrevRow] = []
    try:
        with PREV_SUMMARY.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                status = (row.get("Stato finale file") or "").strip()
                if status not in TARGET_STATUSES:
                    continue
                op = (row.get("Percorso originale") or "").strip()
                selected.append(
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

    return selected, errors


def worker(file_path: str, output_root: str, queue_out: mp.Queue) -> None:
    try:
        target = Path(file_path)
        out_root = Path(output_root)
        out_root.mkdir(parents=True, exist_ok=True)
        engine = SingleFileEngine(target)
        result = engine.run_diagnostics(
            input_folder=str(target.parent),
            include_subfolders=False,
            output_folder=str(out_root),
            repair_mode=True,
            placement_mode=PLACEMENT_MODE_COPY,
        )
        with Path(result["report_paths"]["csv_summary"]).open("r", encoding="utf-8", newline="") as h:
            sum_rows = list(csv.DictReader(h))
        with Path(result["report_paths"]["csv_problems"]).open("r", encoding="utf-8", newline="") as h:
            prob_rows = list(csv.DictReader(h))
        queue_out.put({"ok": True, "summary": sum_rows[0] if sum_rows else {}, "problem": prob_rows[0] if prob_rows else {}})
    except Exception as exc:
        queue_out.put({"ok": False, "error": str(exc)})


def main() -> None:
    prev_rows, read_errors = load_previous()

    problematic_rows = len(prev_rows)
    missing = 0
    dedup = 0
    valid_rows: list[PrevRow] = []
    missing_rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in prev_rows:
        op = row.original_path
        if not op:
            missing += 1
            missing_rows.append({"row": row, "reason": "Originale non trovato"})
            continue
        key = normalize_key(op)
        if key in seen:
            dedup += 1
            continue
        seen.add(key)
        if is_forbidden(op):
            missing += 1
            missing_rows.append({"row": row, "reason": "Percorso originale in cartella generata precedente"})
            continue
        if not Path(op).exists():
            missing += 1
            missing_rows.append({"row": row, "reason": "Originale non trovato"})
            continue
        valid_rows.append(row)

    print("PRECHECK")
    print(f"problematic_rows={problematic_rows}")
    print(f"valid_original_paths={len(valid_rows)}")
    print(f"missing_originals={missing}")
    print(f"deduplicated={dedup}")
    print(f"final_reverify_count={len(valid_rows)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = NEW_BASE_OUTPUT / f"run_selective_{ts}"
    run_root.mkdir(parents=True, exist_ok=True)
    report_dir = run_root / "REPORT"
    report_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    per_file_errors: list[dict[str, str]] = []

    for idx, prev in enumerate(valid_rows, start=1):
        print(f"PROCESS {idx}/{len(valid_rows)} | {prev.file_name}")
        per_file_out = run_root / "files" / f"{idx:05d}"
        queue_out: mp.Queue = mp.Queue()
        proc = mp.Process(target=worker, args=(prev.original_path, str(per_file_out), queue_out), daemon=True)
        proc.start()
        proc.join(TIMEOUT_SECONDS)

        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            payload = {"ok": False, "error": f"Timeout oltre {TIMEOUT_SECONDS}s"}
        else:
            try:
                payload = queue_out.get_nowait()
            except Empty:
                payload = {"ok": False, "error": "Nessun risultato dal worker"}

        if not payload.get("ok"):
            err = str(payload.get("error", "Errore sconosciuto"))
            per_file_errors.append({"percorso originale": prev.original_path, "nome file": prev.file_name, "errore": err})
            rows.append(
                {
                    "percorso originale": prev.original_path,
                    "nome file": prev.file_name,
                    "stato precedente": prev.prev_status,
                    "stato nuovo": "Errore di lettura",
                    "categoria precedente": prev.prev_category,
                    "categoria nuova": "",
                    "zona del problema precedente": prev.prev_zone,
                    "zona del problema nuova": "",
                    "significant_end precedente": prev.prev_sig_end,
                    "significant_end nuovo": "",
                    "trailing_silence precedente": prev.prev_trailing,
                    "trailing_silence nuovo": "",
                    "RMS": "",
                    "Peak": "",
                    "anomalia ignorata Sì/No": "",
                    "riparazione eseguita Sì/No": "NO",
                    "percorso del nuovo output": "",
                    "motivo dell'eventuale cambiamento": f"Errore di lettura: {err}",
                }
            )
            continue

        summary = payload.get("summary", {})
        problem = payload.get("problem", {})
        new_status = summary.get("Stato finale file", "")
        new_category = summary.get("Categoria finale", "")
        new_zone = problem.get("Posizione rispetto all'audio significativo", "")

        reason_parts: list[str] = []
        if prev.prev_status != new_status:
            reason_parts.append(f"Stato {prev.prev_status} -> {new_status}")
        if prev.prev_category != new_category:
            reason_parts.append(f"Categoria {prev.prev_category} -> {new_category}")
        if prev.prev_zone != new_zone:
            reason_parts.append(f"Zona {prev.prev_zone or 'N/A'} -> {new_zone or 'N/A'}")
        if prev.prev_sig_end != summary.get("Fine audio significativo", ""):
            reason_parts.append("significant_end aggiornato")
        if prev.prev_trailing != summary.get("Silenzio finale (ms)", ""):
            reason_parts.append("trailing_silence aggiornato")

        rows.append(
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
                "significant_end nuovo": summary.get("Fine audio significativo", ""),
                "trailing_silence precedente": prev.prev_trailing,
                "trailing_silence nuovo": summary.get("Silenzio finale (ms)", ""),
                "RMS": problem.get("RMS segmento (dBFS)", ""),
                "Peak": problem.get("Picco segmento (dBFS)", ""),
                "anomalia ignorata Sì/No": problem.get("Problema ignorato ai fini dello stato", ""),
                "riparazione eseguita Sì/No": "SI" if new_status == "Riparato" else "NO",
                "percorso del nuovo output": summary.get("Percorso finale", ""),
                "motivo dell'eventuale cambiamento": "; ".join(reason_parts) if reason_parts else "Nessun cambiamento",
            }
        )

    for miss in missing_rows:
        prev = miss["row"]
        rows.append(
            {
                "percorso originale": prev.original_path,
                "nome file": prev.file_name,
                "stato precedente": prev.prev_status,
                "stato nuovo": "Originale non trovato",
                "categoria precedente": prev.prev_category,
                "categoria nuova": "",
                "zona del problema precedente": prev.prev_zone,
                "zona del problema nuova": "",
                "significant_end precedente": prev.prev_sig_end,
                "significant_end nuovo": "",
                "trailing_silence precedente": prev.prev_trailing,
                "trailing_silence nuovo": "",
                "RMS": "",
                "Peak": "",
                "anomalia ignorata Sì/No": "",
                "riparazione eseguita Sì/No": "NO",
                "percorso del nuovo output": "",
                "motivo dell'eventuale cambiamento": miss["reason"],
            }
        )

    compare_csv = report_dir / "Riverifica_Comparativa.csv"
    if rows:
        with compare_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
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
    for row in rows:
        old = row.get("stato precedente", "")
        new = row.get("stato nuovo", "")
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
        "previous_report": str(PREV_SUMMARY),
        "previous_problem_report": str(PREV_PROBLEMS),
        "run_root": str(run_root),
        "comparison_csv": str(compare_csv),
        "problematic_rows": problematic_rows,
        "valid_original_paths": len(valid_rows),
        "missing_originals": missing,
        "deduplicated": dedup,
        "final_reverify_count": len(valid_rows),
        "totale_file_riverificati": len(valid_rows),
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
    mp.freeze_support()
    main()
