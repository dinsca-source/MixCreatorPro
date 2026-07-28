# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import Iterable

SUMMARY_SEPARATOR = "============================================================"


def format_si_no(value: bool) -> str:
    return "SI" if bool(value) else "NO"


def _clean_text(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return text


def clean_path_value(value: object) -> str:
    text = _clean_text(value)
    if not text or text.casefold() == "none":
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except Exception:
        return text


def build_session_configuration_header(
    *,
    processing_type: str,
    options: Iterable[tuple[str, str]],
    paths: Iterable[tuple[str, str]],
) -> list[str]:
    lines: list[str] = [
        SUMMARY_SEPARATOR,
        "CONFIGURAZIONE DELLA SESSIONE",
        SUMMARY_SEPARATOR,
        "",
        "Tipo di elaborazione:",
        _clean_text(processing_type) or "Non specificato",
        "",
        "Opzioni selezionate:",
    ]

    has_options = False
    for label, value in options:
        label_text = _clean_text(label)
        if not label_text:
            continue
        value_text = _clean_text(value) or "Non disponibile"
        lines.append(f"{label_text}: {value_text}")
        has_options = True
    if not has_options:
        lines.append("Nessuna opzione specifica disponibile per questa modalità.")

    lines.append("")
    lines.append("Percorsi utilizzati:")

    has_paths = False
    for label, raw_value in paths:
        label_text = _clean_text(label)
        path_text = clean_path_value(raw_value)
        if not label_text or not path_text:
            continue
        lines.append(f"{label_text}: {path_text}")
        has_paths = True
    if not has_paths:
        lines.append("Nessun percorso aggiuntivo previsto da questa modalità.")

    lines.extend(
        [
            "",
            SUMMARY_SEPARATOR,
            "RIEPILOGO RISULTATI",
            SUMMARY_SEPARATOR,
            "",
        ]
    )
    return lines
