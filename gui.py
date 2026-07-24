# -*- coding: utf-8 -*-
"""
MixCreator PRO
gui.py - Versione 2.8
Patch 1.3.05

Novità:
- tempo trascorso
- tempo residuo stimato
- contatore avanzamento
- barra di stato
- numero build visibile
"""

from __future__ import annotations

import time
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from tkinter import END, SINGLE, filedialog, messagebox
import tkinter as tk
from typing import Any

import customtkinter as ctk

from clip_editor import ClipEditorDialog
from clip_info import ClipInfo
from project_manager import (
    PROJECT_EXTENSION,
    ProjectManagerError,
    ProjectResolutionError,
    ProjectValidationError,
    load_project as load_project_file,
    resolve_project_files,
    save_project as save_project_file
)
from settings import SettingsManager
from tooltip import Tooltip
from utils import scan_mp3_files
from worker import MixWorker, SongExtractionWorker, MP3DiagnosticsWorker
from mp3_diagnostics import (
    STATUS_PERFECT,
    STATUS_REPAIRED,
    STATUS_UNRECOVERABLE,
)
from selective_reverify import (
    SelectiveReverifyError,
    SelectiveReverifySelection,
    prepare_selective_reverify_selection,
)


APP_VERSION = "1.3.05"
APP_BUILD = "2026.07.15.009"
CREATOR_TEXT = "Created by Dino S."
EXTRACT_SONG_TOOLTIP = (
    "Estrae le clip dell'ultimo mix quando sono disponibili i dati temporali. "
    "In alternativa esporta l'elenco ordinato delle Song."
)
INTEGRITY_TOOLTIP_TEXT = "Integrita MP3 dalla diagnostica piu recente."

FILTER_ALL = "Tutti"
FILTER_PERFECT = "Solo integri"
FILTER_REPAIRED = "Solo riparati"
FILTER_UNRECOVERABLE = "Solo non recuperabili"

ctk.set_default_color_theme("blue")


CUT_MODE_LABELS = {
    "Inizio del brano": "inizio",
    "Centro del brano": "centro",
    "Fine del brano": "fine",
    "Punto casuale": "casuale",
    "Intro + finale": "intro_fine",
    "Brano intero": "intero"
}

CUT_MODE_VALUES = {value: key for key, value in CUT_MODE_LABELS.items()}


class MixCreatorApp(ctk.CTk):
    def __init__(self) -> None:
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.load()

        ctk.set_appearance_mode(self.settings["appearance_mode"])
        super().__init__()

        self.title(f"MixCreator PRO {APP_VERSION}")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.input_folder = self.settings["input_folder"]
        self.output_folder = self.settings["output_folder"]
        self.track_count = 0
        self._drag_source_index: int | None = None
        self.track_clip_info: dict[str, ClipInfo] = {}
        self.ordered_track_names: list[str] = []
        self.tooltips: list[Tooltip] = []
        self._updating_track_list = False
        self.reuse_previous_clips_var = tk.BooleanVar(value=False)
        self.last_generated_mix_data: dict[str, Any] | None = None
        self._reusable_previous_clips: dict[str, dict[str, Any]] = {}
        self._track_mix_times_ms: dict[str, int] = {}
        self._extract_song_tooltip: Tooltip | None = None
        self._extract_progress_dialog = None
        self._extract_progress_label = None
        self._extract_progress_bar = None
        self._extract_progress_file_label = None
        self._extract_progress_log = None
        self._extract_progress_cancel_button = None
        self._extract_tracks_snapshot: list[dict[str, Any]] = []
        self._extract_has_temporal_mode = False
        self._diagnostics_integrity_by_file: dict[str, dict[str, Any]] = {}
        self._diagnostics_status_by_file: dict[str, str] = {}
        self._diagnostics_path_index: dict[str, dict[str, Any]] = {}
        self._diagnostics_stable_index: dict[str, dict[str, Any]] = {}
        self._display_track_names: list[str] = []
        self.track_filter_var = tk.StringVar(value=FILTER_ALL)
        self.diagnostics_include_subfolders_var = tk.BooleanVar(value=False)
        self.diagnostics_verify_winlive_var = tk.BooleanVar(value=False)
        self.diagnostics_winlive_autocorrect_var = tk.BooleanVar(value=False)
        self.diagnostics_placement_mode_var = tk.StringVar(
            value=str(self.settings.get("diagnostics_placement_mode", "copy"))
        )
        self.exclude_unrecoverable_var = tk.BooleanVar(value=False)
        self.diagnostics_worker_total = 0
        self.diagnostics_worker_start_time: float | None = None
        self.diagnostics_timer_job = None
        self.diagnostics_last_progress = 0
        self.diagnostics_window: ctk.CTkToplevel | None = None
        self.diagnostics_run_mode = "normal"
        self.diagnostics_reverify_selection: SelectiveReverifySelection | None = None

        self.current_project_path: str | None = None
        self.project_dirty = False
        self.project_source_folder = self.input_folder or ""
        self.project_name = ""
        self._suspend_project_dirty_tracking = False

        self.start_time: float | None = None
        self.timer_job = None
        self.last_progress_percent = 0

        self.worker = MixWorker(
            on_progress=self._worker_progress,
            on_completed=self._worker_completed,
            on_error=self._worker_error,
            on_cancelled=self._worker_cancelled
        )
        self.extract_worker = SongExtractionWorker(
            on_progress=self._extract_worker_progress,
            on_completed=self._extract_worker_completed,
            on_error=self._extract_worker_error,
            on_cancelled=self._extract_worker_cancelled,
        )
        self.diagnostics_worker = MP3DiagnosticsWorker(
            on_progress=self._diagnostics_worker_progress,
            on_completed=self._diagnostics_worker_completed,
            on_error=self._diagnostics_worker_error,
            on_cancelled=self._diagnostics_worker_cancelled,
        )

        self._configure_window()
        self._build_ui()
        self._load_settings_into_ui()

        if self.input_folder and Path(self.input_folder).is_dir():
            self.load_mp3_list()

        self._update_window_title()

    def _configure_window(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        window_width = min(1180, max(900, screen_width - 80))
        window_height = min(790, max(650, screen_height - 120))

        x_position = max(0, (screen_width - window_width) // 2)
        y_position = max(0, (screen_height - window_height) // 2)

        self.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.minsize(900, 630)
        self.resizable(True, True)

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(2, weight=1)

    def _build_ui(self) -> None:
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(
            row=0, column=0, columnspan=2,
            sticky="ew", padx=12, pady=(10, 4)
        )
        title_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            title_frame,
            text=f"MIXCREATOR PRO {APP_VERSION}",
            font=ctk.CTkFont(size=27, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        self.appearance_combo = ctk.CTkComboBox(
            title_frame,
            width=120,
            values=["System", "Light", "Dark"],
            command=self.change_appearance
        )
        self.appearance_combo.grid(row=0, column=1, sticky="e")

        project_bar = ctk.CTkFrame(self, fg_color="transparent")
        project_bar.grid(
            row=1, column=0, columnspan=2,
            sticky="ew", padx=12, pady=(0, 4)
        )
        project_bar.grid_columnconfigure(5, weight=1)

        self.new_project_button = ctk.CTkButton(
            project_bar,
            text="Nuovo progetto",
            width=140,
            command=self.new_project
        )
        self.new_project_button.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self._add_tooltip(
            self.new_project_button,
            "Crea un nuovo progetto e ripristina la schermata iniziale."
        )

        self.open_project_button = ctk.CTkButton(
            project_bar,
            text="Apri progetto",
            width=130,
            command=self.open_project
        )
        self.open_project_button.grid(row=0, column=1, padx=6, sticky="w")
        self._add_tooltip(
            self.open_project_button,
            "Apri un progetto MixCreatorPro salvato."
        )

        self.save_project_button = ctk.CTkButton(
            project_bar,
            text="Salva progetto",
            width=130,
            command=self.save_project
        )
        self.save_project_button.grid(row=0, column=2, padx=6, sticky="w")
        self._add_tooltip(
            self.save_project_button,
            "Salva le modifiche del progetto corrente."
        )

        self.save_project_as_button = ctk.CTkButton(
            project_bar,
            text="Salva progetto con nome",
            width=180,
            command=self.save_project_as
        )
        self.save_project_as_button.grid(row=0, column=3, padx=(6, 0), sticky="w")
        self._add_tooltip(
            self.save_project_as_button,
            "Salva il progetto corrente in un nuovo file."
        )

        self.open_diagnostics_button = ctk.CTkButton(
            project_bar,
            text="Diagnostica MP3",
            width=150,
            command=self.open_diagnostics_window,
        )
        self.open_diagnostics_button.grid(row=0, column=4, padx=(8, 0), sticky="w")
        self._add_tooltip(
            self.open_diagnostics_button,
            "Apri la finestra Diagnostica e Riparazione MP3."
        )

        self.project_status_label = ctk.CTkLabel(
            project_bar,
            text="Progetto: Nessuno",
            anchor="e"
        )
        self.project_status_label.grid(row=0, column=5, padx=(12, 0), sticky="ew")

        self.left_panel = ctk.CTkScrollableFrame(self, label_text="Impostazioni")
        self.left_panel.grid(
            row=2, column=0, sticky="nsew",
            padx=(12, 6), pady=6
        )
        self.left_panel.grid_columnconfigure(1, weight=1)

        self.right_panel = ctk.CTkScrollableFrame(
            self,
            label_text="Brani, anteprima e log"
        )
        self.right_panel.grid(
            row=2, column=1, sticky="nsew",
            padx=(6, 12), pady=6
        )
        self.right_panel.grid_columnconfigure(0, weight=1)

        self._build_left_panel()
        self._build_right_panel()

        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(
            row=3, column=0, columnspan=2,
            sticky="ew", padx=12, pady=(2, 2)
        )
        info_frame.grid_columnconfigure(0, weight=1)

        self.status_bar_label = ctk.CTkLabel(
            info_frame,
            text="Pronto",
            anchor="w"
        )
        self.status_bar_label.grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            info_frame,
            text=f"{CREATOR_TEXT}  |  Build {APP_BUILD}",
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray70")
        ).grid(row=0, column=1, sticky="e")

        buttons_frame = ctk.CTkFrame(self, fg_color="transparent")
        buttons_frame.grid(
            row=4, column=0, columnspan=2,
            sticky="ew", padx=12, pady=(2, 12)
        )
        buttons_frame.grid_columnconfigure(0, weight=1)
        buttons_frame.grid_columnconfigure(1, weight=1)

        self.create_button = ctk.CTkButton(
            buttons_frame,
            text="CREA MIX",
            height=46,
            font=ctk.CTkFont(size=17, weight="bold"),
            command=self.start_mix
        )
        self.create_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._add_tooltip(
            self.create_button,
            "Avvia la creazione del mix con le impostazioni correnti."
        )

        self.cancel_button = ctk.CTkButton(
            buttons_frame,
            text="ANNULLA",
            height=46,
            font=ctk.CTkFont(size=17, weight="bold"),
            state="disabled",
            command=self.cancel_mix
        )
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self._update_tracks_count()
        self._update_controls_state()
        self._refresh_reuse_previous_option(select_if_available=False)

    def _build_left_panel(self) -> None:
        row = 0

        self.input_entry = self._add_folder_row(
            row,
            "Cartella MP3",
            self.select_input,
            self.refresh_input_folder,
            browse_tooltip="Seleziona la cartella contenente i file MP3 da utilizzare.",
            refresh_tooltip="Rilegge gli MP3 presenti direttamente nella cartella selezionata.",
            entry_tooltip="Cartella contenente i file MP3 da utilizzare."
        )
        row += 1

        self.output_entry = self._add_folder_row(
            row,
            "Cartella output",
            self.select_output,
            None,
            browse_tooltip="Seleziona la cartella in cui salvare il mix finale.",
            entry_tooltip="Cartella in cui salvare il file MP3 finale."
        )
        row += 1

        ctk.CTkLabel(self.left_panel, text="Nome file finale").grid(
            row=row, column=0, padx=10, pady=8, sticky="w"
        )
        self.output_name_entry = ctk.CTkEntry(self.left_panel)
        self.output_name_entry.grid(
            row=row, column=1, columnspan=2,
            padx=5, pady=8, sticky="ew"
        )
        self.output_name_entry.bind("<KeyRelease>", self._on_output_name_changed)
        self._add_tooltip(
            self.output_name_entry,
            "Nome del file MP3 finale."
        )
        row += 1

        self.clip_slider, self.clip_value_label = self._add_slider(
            row, "Durata clip", 5, 180, 175
        )
        self._add_tooltip(
            self.clip_slider,
            "Durata usata per i brani che non hanno una clip personalizzata."
        )
        self.clip_slider.configure(command=self._on_clip_change)
        row += 1

        self.crossfade_slider, self.crossfade_value_label = self._add_slider(
            row, "Crossfade", 0, 15, 15
        )
        self.crossfade_slider.configure(command=self._on_crossfade_change)
        self._add_tooltip(
            self.crossfade_slider,
            "Sovrappone gradualmente la fine di un brano con l’inizio del successivo."
        )
        row += 1

        self.fade_in_slider, self.fade_in_value_label = self._add_slider(
            row, "Fade in", 0, 10, 10
        )
        self.fade_in_slider.configure(command=self._on_fade_in_change)
        self._add_tooltip(
            self.fade_in_slider,
            "Aumenta gradualmente il volume all’inizio di ogni clip."
        )
        row += 1

        self.fade_out_slider, self.fade_out_value_label = self._add_slider(
            row, "Fade out", 0, 10, 10
        )
        self.fade_out_slider.configure(command=self._on_fade_out_change)
        self._add_tooltip(
            self.fade_out_slider,
            "Riduce gradualmente il volume alla fine di ogni clip."
        )
        row += 1

        ctk.CTkLabel(self.left_panel, text="Bitrate").grid(
            row=row, column=0, padx=10, pady=8, sticky="w"
        )
        self.bitrate_combo = ctk.CTkComboBox(
            self.left_panel,
            values=["128k", "192k", "256k", "320k"],
            width=140,
            command=self._on_bitrate_change
        )
        self._add_tooltip(
            self.bitrate_combo,
            "Qualità di esportazione del file MP3 finale."
        )
        self.bitrate_combo.grid(
            row=row, column=1, padx=5, pady=8, sticky="w"
        )
        row += 1

        ctk.CTkLabel(self.left_panel, text="Posizione taglio").grid(
            row=row, column=0, padx=10, pady=8, sticky="w"
        )
        self.cut_mode_combo = ctk.CTkComboBox(
            self.left_panel,
            values=list(CUT_MODE_LABELS.keys()),
            width=220,
            command=self._on_cut_mode_change
        )
        self._add_tooltip(
            self.cut_mode_combo,
            "Sceglie da quale parte del brano estrarre la clip quando non esiste un intervallo personalizzato."
        )
        self.cut_mode_combo.grid(
            row=row, column=1, columnspan=2,
            padx=5, pady=8, sticky="w"
        )
        row += 1

        self.normalize_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Normalizza volume",
            command=self._on_project_setting_toggled
        )
        self._add_tooltip(
            self.normalize_checkbox,
            "Uniforma il livello audio dei brani prima della creazione del mix."
        )
        self.normalize_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=7, sticky="w"
        )
        row += 1

        self.random_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Ordine casuale",
            command=self._on_project_setting_toggled
        )
        self._add_tooltip(
            self.random_checkbox,
            "Mescola casualmente l’ordine dei brani prima della creazione del mix."
        )
        self.random_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=7, sticky="w"
        )
        row += 1

        self.short_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Usa tutto il brano se più corto",
            command=self._on_project_setting_toggled
        )
        self._add_tooltip(
            self.short_checkbox,
            "Gestisce i brani più corti della durata richiesta secondo l’opzione prevista dal programma."
        )
        self.short_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=7, sticky="w"
        )
        row += 1

        self.exclude_unrecoverable_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Escludi automaticamente i file non recuperabili dal mix",
            variable=self.exclude_unrecoverable_var,
            command=self._on_project_setting_toggled
        )
        self._add_tooltip(
            self.exclude_unrecoverable_checkbox,
            "Quando attivo, i brani classificati come non recuperabili restano visibili ma non vengono usati nel mix."
        )
        self.exclude_unrecoverable_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=7, sticky="w"
        )
        row += 1

        self.reuse_previous_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Utilizza clip generate in precedenza",
            variable=self.reuse_previous_clips_var,
            command=self._on_reuse_previous_toggled,
            state="disabled"
        )
        self._add_tooltip(
            self.reuse_previous_checkbox,
            "Riutilizza i segmenti temporali dell'ultima generazione salvata nel progetto quando disponibili."
        )
        self.reuse_previous_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=(7, 10), sticky="w"
        )
        row += 1

        self._build_diagnostics_launcher(row)

    def _build_diagnostics_launcher(self, row: int) -> None:
        card = ctk.CTkFrame(self.left_panel)
        card.grid(
            row=row,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=8,
            pady=(10, 8),
        )
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="Diagnostica MP3",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        ctk.CTkLabel(
            card,
            text="Apri la finestra dedicata per analisi, riparazione e report.",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        button = ctk.CTkButton(
            card,
            text="Apri Diagnostica e Riparazione MP3",
            command=self.open_diagnostics_window,
            height=34,
        )
        button.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _build_diagnostics_section(self, parent, start_row: int = 0) -> None:
        diag_card = ctk.CTkFrame(parent)
        diag_card.grid(
            row=start_row,
            column=0,
            sticky="ew",
            padx=10,
            pady=10,
        )
        diag_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            diag_card,
            text="Diagnostica MP3",
            font=ctk.CTkFont(size=15, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))

        ctk.CTkLabel(diag_card, text="Cartella di input").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.diagnostics_input_entry = ctk.CTkEntry(diag_card)
        self.diagnostics_input_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        ctk.CTkButton(diag_card, text="Sfoglia", width=84, command=self.select_diagnostics_input).grid(
            row=1, column=2, sticky="e", padx=10, pady=5
        )

        self.diagnostics_subfolders_checkbox = ctk.CTkCheckBox(
            diag_card,
            text="Ricerca nelle sottocartelle",
            variable=self.diagnostics_include_subfolders_var,
        )
        self.diagnostics_subfolders_checkbox.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=4)

        self.diagnostics_winlive_group_label = ctk.CTkLabel(
            diag_card,
            text="Verifica WinLive",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.diagnostics_winlive_group_label.grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 2))

        self.diagnostics_winlive_checkbox = ctk.CTkCheckBox(
            diag_card,
            text="Verifica TAG WinLive",
            variable=self.diagnostics_verify_winlive_var,
            command=self._on_diagnostics_winlive_toggle,
        )
        self.diagnostics_winlive_checkbox.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        self._add_tooltip(
            self.diagnostics_winlive_checkbox,
            "Controlla la presenza e la correttezza\n"
            "dei TAG WinLive (testo e accordi)\n"
            "e include i risultati nei report.",
        )

        self.diagnostics_winlive_autocorrect_checkbox = ctk.CTkCheckBox(
            diag_card,
            text="Correggi automaticamente\ngli errori normalizzabili",
            variable=self.diagnostics_winlive_autocorrect_var,
            command=self._on_diagnostics_winlive_autocorrect_toggle,
        )
        self.diagnostics_winlive_autocorrect_checkbox.grid(row=5, column=0, columnspan=3, sticky="w", padx=30, pady=(0, 4))
        self._add_tooltip(
            self.diagnostics_winlive_autocorrect_checkbox,
            "Applica esclusivamente\n"
            "le normalizzazioni sicure\n"
            "previste dal motore WinLive.",
        )
        self._sync_diagnostics_winlive_controls_state()

        ctk.CTkLabel(diag_card, text="Cartella di output").grid(row=6, column=0, sticky="w", padx=10, pady=5)
        self.diagnostics_output_entry = ctk.CTkEntry(diag_card)
        self.diagnostics_output_entry.grid(row=6, column=1, sticky="ew", padx=6, pady=5)
        ctk.CTkButton(diag_card, text="Sfoglia", width=84, command=self.select_diagnostics_output).grid(
            row=6, column=2, sticky="e", padx=10, pady=5
        )

        ctk.CTkLabel(diag_card, text="Al termine dell'analisi:").grid(
            row=7,
            column=0,
            sticky="w",
            padx=10,
            pady=(8, 2),
        )
        self.diagnostics_placement_copy_radio = ctk.CTkRadioButton(
            diag_card,
            text="Copia i file nelle cartelle di categoria",
            variable=self.diagnostics_placement_mode_var,
            value="copy",
            command=self._on_diagnostics_placement_mode_changed,
        )
        self.diagnostics_placement_copy_radio.grid(row=8, column=0, columnspan=3, sticky="w", padx=10, pady=2)

        self.diagnostics_placement_move_radio = ctk.CTkRadioButton(
            diag_card,
            text="Sposta i file nelle cartelle di categoria",
            variable=self.diagnostics_placement_mode_var,
            value="move",
            command=self._on_diagnostics_placement_mode_changed,
        )
        self.diagnostics_placement_move_radio.grid(row=9, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))

        buttons_frame = ctk.CTkFrame(diag_card, fg_color="transparent")
        buttons_frame.grid(row=10, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 4))
        buttons_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.diagnostics_analyze_button = ctk.CTkButton(
            buttons_frame,
            text="Analizza",
            command=self.start_diagnostics_analysis,
        )
        self.diagnostics_analyze_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.diagnostics_repair_button = ctk.CTkButton(
            buttons_frame,
            text="Analizza e Ripara",
            command=self.start_diagnostics_repair,
        )
        self.diagnostics_repair_button.grid(row=0, column=1, sticky="ew", padx=4)

        self.diagnostics_reverify_button = ctk.CTkButton(
            buttons_frame,
            text="Riverifica file problematici",
            command=self.start_selective_reverify,
        )
        self.diagnostics_reverify_button.grid(row=0, column=2, sticky="ew", padx=4)

        self.diagnostics_stop_button = ctk.CTkButton(
            buttons_frame,
            text="Interrompi",
            state="disabled",
            command=self.stop_diagnostics,
        )
        self.diagnostics_stop_button.grid(row=0, column=3, sticky="ew", padx=(4, 0))

        self.diagnostics_progress = ctk.CTkProgressBar(diag_card)
        self.diagnostics_progress.grid(row=11, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 4))
        self.diagnostics_progress.set(0)

        self.diagnostics_status_label = ctk.CTkLabel(diag_card, text="Pronto", anchor="w")
        self.diagnostics_status_label.grid(row=12, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 2))

        counters_frame = ctk.CTkFrame(diag_card, fg_color="transparent")
        counters_frame.grid(row=13, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        counters_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.diagnostics_count_label = ctk.CTkLabel(counters_frame, text="File analizzati: 0 / 0", anchor="w")
        self.diagnostics_count_label.grid(row=0, column=0, sticky="w")
        self.diagnostics_elapsed_label = ctk.CTkLabel(counters_frame, text="Tempo: 00:00:00", anchor="w")
        self.diagnostics_elapsed_label.grid(row=0, column=1, sticky="w")
        self.diagnostics_eta_label = ctk.CTkLabel(counters_frame, text="Tempo stimato: --", anchor="w")
        self.diagnostics_eta_label.grid(row=0, column=2, sticky="w")

        self.diagnostics_log_box = ctk.CTkTextbox(diag_card, height=130)
        self.diagnostics_log_box.grid(row=14, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 10))
        self.diagnostics_log_box.configure(state="disabled")

    def _on_diagnostics_placement_mode_changed(self) -> None:
        self.save_settings()

    def _sync_diagnostics_winlive_controls_state(self) -> None:
        main_enabled = bool(self.diagnostics_verify_winlive_var.get())
        if not main_enabled and bool(self.diagnostics_winlive_autocorrect_var.get()):
            self.diagnostics_winlive_autocorrect_var.set(False)

        if hasattr(self, "diagnostics_winlive_autocorrect_checkbox"):
            self.diagnostics_winlive_autocorrect_checkbox.configure(
                state="normal" if main_enabled else "disabled"
            )

    def _on_diagnostics_winlive_toggle(self) -> None:
        self._sync_diagnostics_winlive_controls_state()
        self.save_settings()

    def _on_diagnostics_winlive_autocorrect_toggle(self) -> None:
        self.save_settings()

    def _build_right_panel(self) -> None:
        tracks_header = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        tracks_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        tracks_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tracks_header,
            text="Brani nel mix",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        self.tracks_found_label = ctk.CTkLabel(
            tracks_header,
            text="Brani trovati: 0",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.tracks_found_label.grid(row=0, column=1, sticky="e")

        self.track_filter_combo = ctk.CTkComboBox(
            tracks_header,
            width=180,
            values=[FILTER_ALL, FILTER_PERFECT, FILTER_REPAIRED, FILTER_UNRECOVERABLE],
            variable=self.track_filter_var,
            command=self._on_track_filter_change,
        )
        self.track_filter_combo.grid(row=0, column=2, padx=(8, 0), sticky="e")
        self._add_tooltip(self.track_filter_combo, "Filtra la lista brani per stato di integrita.")

        track_frame = ctk.CTkFrame(self.right_panel)
        track_frame.grid(
            row=1, column=0, sticky="nsew",
            padx=10, pady=(4, 6)
        )
        track_frame.configure(height=320)
        track_frame.grid_propagate(False)
        track_frame.grid_columnconfigure(0, weight=1)
        track_frame.grid_rowconfigure(0, weight=1)

        self.track_list = tk.Listbox(
            track_frame,
            selectmode=SINGLE,
            exportselection=False,
            activestyle="dotbox",
            font=("Segoe UI", 16),
            height=14,
            borderwidth=0,
            highlightthickness=0
        )
        self.track_list.grid(
            row=0, column=0, sticky="nsew",
            padx=(8, 4), pady=(8, 4)
        )
        self.track_list_tooltip = self._add_tooltip(
            self.track_list,
            "Elenco e ordine dei brani che verranno inseriti nel mix.\n"
            "[Integrita: -] File non ancora analizzato oppure risultato diagnostico non disponibile."
        )

        track_scrollbar = ctk.CTkScrollbar(
            track_frame,
            command=self.track_list.yview
        )
        track_scrollbar.grid(
            row=0, column=1, sticky="ns",
            padx=(0, 8), pady=(8, 4)
        )
        self.track_list.configure(
            yscrollcommand=track_scrollbar.set
        )

        self.track_list.bind(
            "<ButtonPress-1>",
            self._drag_start
        )
        self.track_list.bind(
            "<B1-Motion>",
            self._drag_motion
        )
        self.track_list.bind(
            "<ButtonRelease-1>",
            self._drag_end
        )
        self.track_list.bind(
            "<Delete>",
            self.delete_selected_track
        )
        self.track_list.bind("<<ListboxSelect>>", self._on_track_selection_change)

        reorder_frame = ctk.CTkFrame(
            track_frame,
            fg_color="transparent"
        )
        reorder_frame.grid(
            row=1, column=0, columnspan=2,
            sticky="ew",
            padx=8, pady=(2, 8)
        )
        for column in range(5):
            reorder_frame.grid_columnconfigure(column, weight=1)

        self.move_up_button = ctk.CTkButton(
            reorder_frame,
            text="Sposta su",
            height=28,
            command=self.move_track_up
        )
        self.move_up_button.grid(
            row=0, column=0,
            sticky="ew",
            padx=(0, 3)
        )
        self._add_tooltip(
            self.move_up_button,
            "Sposta verso l'alto il brano selezionato nell'ordine del mix."
        )

        self.move_down_button = ctk.CTkButton(
            reorder_frame,
            text="Sposta giù",
            height=28,
            command=self.move_track_down
        )
        self.move_down_button.grid(
            row=0, column=1,
            sticky="ew",
            padx=3
        )
        self._add_tooltip(
            self.move_down_button,
            "Sposta verso il basso il brano selezionato nell'ordine del mix."
        )

        self.sort_button = ctk.CTkButton(
            reorder_frame,
            text="Ordina A-Z",
            height=28,
            command=self.sort_tracks_alphabetically
        )
        self.sort_button.grid(
            row=0, column=2,
            sticky="ew",
            padx=3
        )
        self._add_tooltip(
            self.sort_button,
            "Ordina alfabeticamente i brani del mix."
        )

        self.shuffle_button = ctk.CTkButton(
            reorder_frame,
            text="Casuale",
            height=28,
            command=self.shuffle_track_list
        )
        self.shuffle_button.grid(
            row=0, column=3,
            sticky="ew",
            padx=3
        )
        self._add_tooltip(
            self.shuffle_button,
            "Mescola casualmente l’ordine dei brani prima della creazione del mix."
        )

        self.delete_track_button = ctk.CTkButton(
            reorder_frame,
            text="Elimina",
            height=28,
            command=self.delete_selected_track
        )
        self.delete_track_button.grid(
            row=0, column=4,
            sticky="ew",
            padx=(3, 0)
        )
        self._add_tooltip(
            self.delete_track_button,
            "Rimuove il brano selezionato dal mix senza cancellare il file dal disco."
        )

        clip_buttons_frame = ctk.CTkFrame(track_frame, fg_color="transparent")
        clip_buttons_frame.grid(
            row=2, column=0, columnspan=2,
            sticky="ew",
            padx=8, pady=(2, 4)
        )
        clip_buttons_frame.grid_columnconfigure((0, 1), weight=1)

        self.set_clip_button = ctk.CTkButton(
            clip_buttons_frame,
            text="Imposta clip ad hoc",
            height=28,
            command=self.set_test_clip
        )
        self.set_clip_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._add_tooltip(
            self.set_clip_button,
            "Permette di scegliere manualmente un intervallo personalizzato da utilizzare per il brano selezionato."
        )

        self.clear_clip_button = ctk.CTkButton(
            clip_buttons_frame,
            text="Rimuovi clip ad hoc",
            height=28,
            command=self.clear_custom_clip
        )
        self.clear_clip_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self._add_tooltip(
            self.clear_clip_button,
            "Rimuove l'intervallo personalizzato del brano selezionato e ripristina le impostazioni globali."
        )

        extraction_frame = ctk.CTkFrame(track_frame, fg_color="transparent")
        extraction_frame.grid(
            row=3, column=0, columnspan=2,
            sticky="ew",
            padx=8, pady=(0, 4)
        )
        extraction_frame.grid_columnconfigure(0, weight=1)

        self.extract_song_button = ctk.CTkButton(
            extraction_frame,
            text="Estrai Song",
            height=28,
            command=self.extract_songs
        )
        self.extract_song_button.grid(row=0, column=0, sticky="ew")
        self._extract_song_tooltip = self._add_tooltip(
            self.extract_song_button,
            EXTRACT_SONG_TOOLTIP
        )

        preview_frame = ctk.CTkFrame(self.right_panel)
        preview_frame.grid(
            row=2, column=0, sticky="ew",
            padx=10, pady=(2, 4)
        )
        preview_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            preview_frame,
            text="ANTEPRIMA",
            font=ctk.CTkFont(size=15, weight="bold")
        ).grid(row=0, column=0, columnspan=2, pady=(8, 6))

        self.preview_tracks_label = self._preview_item(
            preview_frame, 1, 0, "MP3", "0"
        )
        self.preview_duration_label = self._preview_item(
            preview_frame, 1, 1, "Durata stimata", "00:00:00"
        )
        self.preview_size_label = self._preview_item(
            preview_frame, 2, 0, "Dimensione prevista", "0 MB"
        )
        self.preview_bitrate_label = self._preview_item(
            preview_frame, 2, 1, "Bitrate", "320 kbps"
        )

        progress_frame = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        progress_frame.grid(
            row=3, column=0, sticky="ew",
            padx=10, pady=(4, 2)
        )
        progress_frame.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(progress_frame)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.progress.set(0)

        self.percent_label = ctk.CTkLabel(
            progress_frame,
            text="0%",
            width=45
        )
        self.percent_label.grid(row=0, column=1)

        timing_frame = ctk.CTkFrame(self.right_panel)
        timing_frame.grid(
            row=4, column=0, sticky="ew",
            padx=10, pady=(2, 4)
        )
        timing_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.counter_label = self._preview_item(
            timing_frame, 0, 0, "Avanzamento", "0 / 0"
        )
        self.elapsed_label = self._preview_item(
            timing_frame, 0, 1, "Trascorso", "00:00:00"
        )
        self.remaining_label = self._preview_item(
            timing_frame, 0, 2, "Residuo", "--:--:--"
        )

        self.status_label = ctk.CTkLabel(
            self.right_panel,
            text="Pronto",
            wraplength=380,
            justify="left"
        )
        self.status_label.grid(
            row=5, column=0, sticky="ew",
            padx=10, pady=(4, 4)
        )

        ctk.CTkLabel(
            self.right_panel,
            text="Log",
            font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=6, column=0, sticky="w", padx=10, pady=(4, 0))

        self.log_box = ctk.CTkTextbox(
            self.right_panel,
            height=110
        )
        self.log_box.grid(
            row=7, column=0, sticky="ew",
            padx=10, pady=(2, 10)
        )
        self.log_box.configure(state="disabled")
        self._append_log("MixCreator PRO pronto.")
        self._update_tracks_count()

    def _preview_item(self, parent, row, column, title, value):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=column, sticky="ew", padx=8, pady=5)

        ctk.CTkLabel(
            frame,
            text=title,
            font=ctk.CTkFont(size=12)
        ).pack(anchor="w")

        label = ctk.CTkLabel(
            frame,
            text=value,
            font=ctk.CTkFont(size=15, weight="bold")
        )
        label.pack(anchor="w")

        return label

    def _add_folder_row(
        self,
        row: int,
        label: str,
        command,
        refresh_command=None,
        browse_tooltip: str | None = None,
        refresh_tooltip: str | None = None,
        entry_tooltip: str | None = None
    ):
        ctk.CTkLabel(self.left_panel, text=label).grid(
            row=row, column=0, padx=10, pady=8, sticky="w"
        )

        entry = ctk.CTkEntry(self.left_panel)
        entry.grid(
            row=row, column=1, padx=5, pady=8, sticky="ew"
        )
        if entry_tooltip is not None:
            self._add_tooltip(entry, entry_tooltip)

        buttons_frame = ctk.CTkFrame(
            self.left_panel,
            fg_color="transparent"
        )
        buttons_frame.grid(
            row=row,
            column=2,
            padx=10,
            pady=8,
            sticky="e"
        )

        browse_button = ctk.CTkButton(
            buttons_frame,
            text="Sfoglia",
            width=82,
            command=command
        )
        browse_button.grid(row=0, column=0, padx=(0, 4))
        if browse_tooltip is not None:
            self._add_tooltip(browse_button, browse_tooltip)

        if refresh_command is not None:
            refresh_button = ctk.CTkButton(
                buttons_frame,
                text="Aggiorna",
                width=82,
                command=refresh_command
            )
            refresh_button.grid(row=0, column=1)
            if refresh_tooltip is not None:
                self._add_tooltip(refresh_button, refresh_tooltip)

            if label == "Cartella MP3":
                self.refresh_input_button = refresh_button

        return entry

    def _add_slider(
        self,
        row: int,
        label: str,
        minimum: int,
        maximum: int,
        steps: int
    ):
        ctk.CTkLabel(self.left_panel, text=label).grid(
            row=row, column=0, padx=10, pady=9, sticky="w"
        )

        slider = ctk.CTkSlider(
            self.left_panel,
            from_=minimum,
            to=maximum,
            number_of_steps=steps
        )
        slider.grid(
            row=row, column=1, padx=5, pady=9, sticky="ew"
        )

        value_label = ctk.CTkLabel(
            self.left_panel,
            text=f"{minimum} s",
            width=65
        )
        value_label.grid(row=row, column=2, padx=10)

        slider.configure(
            command=lambda value: value_label.configure(
                text=f"{int(round(value))} s"
            )
        )

        return slider, value_label

    def _load_settings_into_ui(self) -> None:
        self._suspend_project_dirty_tracking = True
        try:
            self.input_entry.insert(0, self.input_folder)
            self.output_entry.insert(0, self.output_folder)
            self._set_diagnostics_entry_values(
                input_folder=self.input_folder,
                output_folder=self.output_folder,
            )
            self.output_name_entry.insert(0, self.settings["output_name"])

            self._set_slider(self.clip_slider, self.clip_value_label, self.settings["clip_seconds"])
            self._set_slider(self.crossfade_slider, self.crossfade_value_label, self.settings["crossfade_seconds"])
            self._set_slider(self.fade_in_slider, self.fade_in_value_label, self.settings["fade_in_seconds"])
            self._set_slider(self.fade_out_slider, self.fade_out_value_label, self.settings["fade_out_seconds"])

            self.bitrate_combo.set(self.settings["bitrate"])
            self.cut_mode_combo.set(
                CUT_MODE_VALUES.get(self.settings["cut_mode"], "Inizio del brano")
            )

            if self.settings["normalize_audio"]:
                self.normalize_checkbox.select()
            if self.settings["random_order"]:
                self.random_checkbox.select()
            if self.settings["continue_short_tracks"]:
                self.short_checkbox.select()

            self.exclude_unrecoverable_var.set(bool(self.settings.get("exclude_unrecoverable_from_mix", False)))
            if self.exclude_unrecoverable_var.get():
                self.exclude_unrecoverable_checkbox.select()
            else:
                self.exclude_unrecoverable_checkbox.deselect()

            self.diagnostics_verify_winlive_var.set(bool(self.settings.get("diagnostics_verify_winlive", False)))
            self.diagnostics_winlive_autocorrect_var.set(bool(self.settings.get("diagnostics_winlive_autocorrect", False)))
            self._sync_diagnostics_winlive_controls_state()

            placement_mode = str(self.settings.get("diagnostics_placement_mode", "copy")).strip().lower()
            if placement_mode not in ("copy", "move"):
                placement_mode = "copy"
            self.diagnostics_placement_mode_var.set(placement_mode)
            self.diagnostics_verify_winlive_var.set(bool(self.settings.get("diagnostics_verify_winlive", False)))
            self.diagnostics_winlive_autocorrect_var.set(bool(self.settings.get("diagnostics_winlive_autocorrect", False)))
            self._sync_diagnostics_winlive_controls_state()

            self.reuse_previous_clips_var.set(False)
            self.appearance_combo.set(self.settings["appearance_mode"])
            self._on_cut_mode_change(self.cut_mode_combo.get())
            self.update_preview()
            self._refresh_reuse_previous_option(select_if_available=False)
        finally:
            self._suspend_project_dirty_tracking = False

    @staticmethod
    def _set_slider(slider, label, value: int) -> None:
        slider.set(value)
        label.configure(text=f"{int(value)} s")

    def _update_window_title(self) -> None:
        dirty_marker = " *" if self.project_dirty else ""
        if self.project_name:
            self.title(f"MixCreator PRO {APP_VERSION} - {self.project_name}{dirty_marker}")
        else:
            self.title(f"MixCreator PRO {APP_VERSION}")
        if hasattr(self, "project_status_label"):
            project_text = self.project_name if self.project_name else "Nessuno"
            self.project_status_label.configure(text=f"Progetto: {project_text}{dirty_marker}")

    def _update_tracks_count(self) -> None:
        self.track_count = len(self.ordered_track_names)
        if hasattr(self, "tracks_found_label"):
            visible = len(self._display_track_names)
            if not self._display_track_names and self.track_filter_var.get().strip() == FILTER_ALL:
                visible = self.track_count
            if visible != self.track_count:
                self.tracks_found_label.configure(text=f"Brani trovati: {self.track_count} (visibili: {visible})")
            else:
                self.tracks_found_label.configure(text=f"Brani trovati: {self.track_count}")

    def _update_controls_state(self) -> None:
        has_folder = bool(self.input_entry.get().strip()) if hasattr(self, "input_entry") else False
        has_tracks = self.track_count > 0
        has_selection = bool(self.track_list.curselection()) if hasattr(self, "track_list") else False
        is_mix_running = self.worker.is_running if hasattr(self, "worker") else False
        is_extract_running = self.extract_worker.is_running if hasattr(self, "extract_worker") else False
        is_diag_running = self.diagnostics_worker.is_running if hasattr(self, "diagnostics_worker") else False
        is_busy = is_mix_running or is_extract_running or is_diag_running

        if hasattr(self, "create_button"):
            self.create_button.configure(state="normal" if has_tracks and not is_busy else "disabled")

        state_requires_selection = "normal" if has_tracks and has_selection else "disabled"
        for attr in ("delete_track_button", "move_up_button", "move_down_button", "set_clip_button", "clear_clip_button"):
            button = getattr(self, attr, None)
            if button is not None:
                button.configure(state=state_requires_selection)

        for attr in ("sort_button", "shuffle_button"):
            button = getattr(self, attr, None)
            if button is not None:
                button.configure(state="normal" if has_tracks else "disabled")

        if hasattr(self, "refresh_input_button"):
            self.refresh_input_button.configure(state="normal" if has_folder else "disabled")

        if hasattr(self, "save_project_button"):
            self.save_project_button.configure(state="normal" if has_folder else "disabled")

        if hasattr(self, "save_project_as_button"):
            self.save_project_as_button.configure(state="normal" if has_folder else "disabled")

        if hasattr(self, "extract_song_button"):
            self.extract_song_button.configure(state="normal")
            if self._extract_song_tooltip is not None:
                self._extract_song_tooltip.text = EXTRACT_SONG_TOOLTIP

        if hasattr(self, "diagnostics_analyze_button"):
            self.diagnostics_analyze_button.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_repair_button"):
            self.diagnostics_repair_button.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_reverify_button"):
            self.diagnostics_reverify_button.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_stop_button"):
            self.diagnostics_stop_button.configure(state="normal" if is_diag_running else "disabled")
        if hasattr(self, "diagnostics_placement_copy_radio"):
            self.diagnostics_placement_copy_radio.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_placement_move_radio"):
            self.diagnostics_placement_move_radio.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_winlive_checkbox"):
            self.diagnostics_winlive_checkbox.configure(state="disabled" if is_diag_running else "normal")
        if hasattr(self, "diagnostics_winlive_autocorrect_checkbox"):
            can_edit_autocorrect = (not is_diag_running) and bool(self.diagnostics_verify_winlive_var.get())
            self.diagnostics_winlive_autocorrect_checkbox.configure(
                state="normal" if can_edit_autocorrect else "disabled"
            )

    def _mark_project_dirty(self) -> None:
        if self._suspend_project_dirty_tracking:
            return

        if not self.project_dirty:
            self.project_dirty = True
            self._update_window_title()

    def _set_project_clean(
        self,
        project_path: str | None,
        project_name: str,
        source_folder: str
    ) -> None:
        self.current_project_path = project_path
        self.project_name = project_name
        self.project_source_folder = source_folder
        self.project_dirty = False
        self._update_window_title()
        self._update_controls_state()

    def _collect_project_settings(self) -> dict[str, object]:
        return {
            "clip_seconds": int(round(self.clip_slider.get())),
            "crossfade_seconds": int(round(self.crossfade_slider.get())),
            "fade_in_seconds": int(round(self.fade_in_slider.get())),
            "fade_out_seconds": int(round(self.fade_out_slider.get())),
            "bitrate": self.bitrate_combo.get(),
            "cut_mode": CUT_MODE_LABELS.get(self.cut_mode_combo.get(), "inizio"),
            "random_order": bool(self.random_checkbox.get()),
            "normalize_audio": bool(self.normalize_checkbox.get()),
            "continue_short_tracks": bool(self.short_checkbox.get()),
            "exclude_unrecoverable_from_mix": bool(self.exclude_unrecoverable_var.get()),
            "output_name": self.output_name_entry.get().strip() or "MixFinale",
            "output_folder": self.output_entry.get().strip(),
            "application_version": APP_VERSION
        }

    @staticmethod
    def _safe_int(value: Any, default: int | None = None) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _has_new_temporal_fields(item: dict[str, Any]) -> bool:
        required = (
            "source_path",
            "source_start_ms",
            "source_end_ms",
            "clip_duration_ms",
            "mix_start_ms",
            "mix_end_ms",
            "crossfade_in_ms",
            "crossfade_out_ms",
        )
        return all(key in item for key in required)

    @classmethod
    def _normalize_new_mix_track(cls, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        file_name = str(item.get("file_name") or item.get("relative_path") or "").strip()
        source_path = str(item.get("source_path") or "").strip()
        if not file_name or not source_path:
            return None

        source_start_ms = cls._safe_int(item.get("source_start_ms"))
        source_end_ms = cls._safe_int(item.get("source_end_ms"))
        clip_duration_ms = cls._safe_int(item.get("clip_duration_ms"))
        mix_start_ms = cls._safe_int(item.get("mix_start_ms"))
        mix_end_ms = cls._safe_int(item.get("mix_end_ms"))
        crossfade_in_ms = cls._safe_int(item.get("crossfade_in_ms"), 0)
        crossfade_out_ms = cls._safe_int(item.get("crossfade_out_ms"), 0)

        if (
            source_start_ms is None
            or source_end_ms is None
            or clip_duration_ms is None
            or mix_start_ms is None
            or mix_end_ms is None
            or crossfade_in_ms is None
            or crossfade_out_ms is None
        ):
            return None

        if (
            source_start_ms < 0
            or source_end_ms <= source_start_ms
            or clip_duration_ms <= 0
            or mix_start_ms < 0
            or mix_end_ms <= mix_start_ms
            or crossfade_in_ms < 0
            or crossfade_out_ms < 0
        ):
            return None

        fade_in_ms = cls._safe_int(item.get("fade_in_ms"), 0)
        fade_out_ms = cls._safe_int(item.get("fade_out_ms"), 0)
        mix_order = cls._safe_int(item.get("mix_order"), 0)

        return {
            "file_name": Path(file_name).as_posix(),
            "source_path": source_path,
            "source_start_ms": source_start_ms,
            "source_end_ms": source_end_ms,
            "clip_duration_ms": clip_duration_ms,
            "mix_start_ms": mix_start_ms,
            "mix_end_ms": mix_end_ms,
            "crossfade_in_ms": crossfade_in_ms,
            "crossfade_out_ms": crossfade_out_ms,
            "fade_in_ms": max(0, fade_in_ms or 0),
            "fade_out_ms": max(0, fade_out_ms or 0),
            "mix_order": max(0, mix_order or 0),
            "source_mode": str(item.get("source_mode", "calculated")),
            "manual_clip": bool(item.get("manual_clip", False)),
            # Campi legacy mantenuti per compatibilita con il riuso clip.
            "start_ms": source_start_ms,
            "duration_ms": clip_duration_ms,
        }

    @staticmethod
    def _normalize_previous_mix_track(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        file_name = str(item.get("file_name") or item.get("relative_path") or "").strip()
        if not file_name:
            return None

        try:
            start_ms = int(item.get("start_ms", -1))
            duration_ms = int(item.get("duration_ms", -1))
        except (TypeError, ValueError):
            return None

        if start_ms < 0 or duration_ms <= 0:
            return None

        return {
            "file_name": Path(file_name).as_posix(),
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "source_mode": str(item.get("source_mode", "calculated")),
            "manual_clip": bool(item.get("manual_clip", False)),
        }

    @classmethod
    def _normalize_saved_mix_track(cls, item: Any) -> dict[str, Any] | None:
        normalized_new = cls._normalize_new_mix_track(item)
        if normalized_new is not None:
            return normalized_new
        return cls._normalize_previous_mix_track(item)

    def _has_valid_last_mix_temporal_data(self) -> bool:
        if not isinstance(self.last_generated_mix_data, dict):
            return False

        tracks = self.last_generated_mix_data.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            return False

        for item in tracks:
            if self._normalize_new_mix_track(item) is None:
                return False

        return True

    def _build_track_mix_times_index(self) -> dict[str, int]:
        if not self._has_valid_last_mix_temporal_data():
            return {}

        tracks = self.last_generated_mix_data.get("tracks", [])
        index: dict[str, int] = {}
        for item in tracks:
            normalized = self._normalize_new_mix_track(item)
            if normalized is None:
                continue
            file_name = str(normalized["file_name"])
            mix_start_ms = int(normalized["mix_start_ms"])
            index[file_name] = mix_start_ms
        return index

    def _refresh_track_mix_times_index(self) -> None:
        self._track_mix_times_ms = self._build_track_mix_times_index()

    def _get_ordered_new_mix_tracks(self) -> list[dict[str, Any]]:
        if not self._has_valid_last_mix_temporal_data():
            return []

        tracks = self.last_generated_mix_data.get("tracks", [])
        normalized_tracks: list[dict[str, Any]] = []
        for item in tracks:
            normalized = self._normalize_new_mix_track(item)
            if normalized is not None:
                normalized_tracks.append(normalized)

        normalized_tracks.sort(
            key=lambda item: (
                int(item.get("mix_order", 0)),
                int(item.get("mix_start_ms", 0)),
                str(item.get("file_name", "")).lower(),
            )
        )
        return normalized_tracks

    def _sanitize_last_generated_mix(self, data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None

        tracks_data = data.get("tracks")
        if not isinstance(tracks_data, list):
            return None

        normalized_tracks: list[dict[str, Any]] = []
        for item in tracks_data:
            normalized = self._normalize_saved_mix_track(item)
            if normalized is not None:
                normalized_tracks.append(normalized)

        if not normalized_tracks:
            return None

        return {
            "created_at": str(data.get("created_at", "")),
            "cut_mode": str(data.get("cut_mode", "")),
            "tracks": normalized_tracks,
        }

    def _build_reusable_previous_clips(self) -> dict[str, dict[str, Any]]:
        if not self.last_generated_mix_data:
            return {}

        tracks = self.last_generated_mix_data.get("tracks")
        if not isinstance(tracks, list):
            return {}

        valid_current = set(self.ordered_track_names)
        reusable: dict[str, dict[str, Any]] = {}
        for item in tracks:
            normalized = self._normalize_saved_mix_track(item)
            if normalized is None:
                continue
            file_name = str(normalized["file_name"])
            if file_name in valid_current:
                reusable[file_name] = normalized

        return reusable

    def _refresh_reuse_previous_option(self, select_if_available: bool) -> None:
        self._reusable_previous_clips = self._build_reusable_previous_clips()

        if not hasattr(self, "reuse_previous_checkbox"):
            return

        has_reusable = bool(self._reusable_previous_clips)
        if has_reusable:
            self.reuse_previous_checkbox.configure(state="normal")
            if select_if_available:
                self.reuse_previous_clips_var.set(True)
        else:
            self.reuse_previous_clips_var.set(False)
            self.reuse_previous_checkbox.configure(state="disabled")

    def _build_project_track_payload(self) -> list[dict[str, object]]:
        source_folder = Path(self.input_entry.get().strip()).expanduser()
        payload: list[dict[str, object]] = []

        for position, file_name in enumerate(self.ordered_track_names):
            relative_path = Path(file_name).as_posix()
            file_path = (source_folder / relative_path).resolve()
            clip_info = self.track_clip_info.get(file_name, ClipInfo())

            size_bytes: int | None = None
            modified_ts: float | None = None
            if file_path.is_file():
                try:
                    stat = file_path.stat()
                    size_bytes = int(stat.st_size)
                    modified_ts = float(stat.st_mtime)
                except OSError:
                    size_bytes = None
                    modified_ts = None

            duration_ms = None
            if clip_info.use_custom_clip and clip_info.clip_end_ms > 0:
                duration_ms = int(clip_info.clip_end_ms)

            payload.append(
                {
                    "file_name": Path(file_name).name,
                    "relative_path": relative_path,
                    "absolute_path_original": str(file_path),
                    "position": position,
                    "size_bytes": size_bytes,
                    "modified_timestamp": modified_ts,
                    "duration_ms": duration_ms,
                    "included": True,
                    "in_ms": int(clip_info.clip_start_ms),
                    "out_ms": int(clip_info.clip_end_ms),
                    "clip_duration_ms": (
                        int(clip_info.clip_end_ms - clip_info.clip_start_ms)
                        if clip_info.use_custom_clip
                        else None
                    ),
                    "has_custom_clip": bool(clip_info.use_custom_clip),
                    "cut_mode": CUT_MODE_LABELS.get(self.cut_mode_combo.get(), "inizio"),
                    "full_track": CUT_MODE_LABELS.get(self.cut_mode_combo.get(), "inizio") == "intero",
                    "clip_info": clip_info.to_dict()
                }
            )

        return payload

    def _apply_project_settings_to_ui(self, settings: dict[str, object]) -> None:
        def as_int(value: object, fallback: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return fallback

        self._suspend_project_dirty_tracking = True
        try:
            self._set_slider(self.clip_slider, self.clip_value_label, as_int(settings.get("clip_seconds"), int(round(self.clip_slider.get()))))
            self._set_slider(self.crossfade_slider, self.crossfade_value_label, as_int(settings.get("crossfade_seconds"), int(round(self.crossfade_slider.get()))))
            self._set_slider(self.fade_in_slider, self.fade_in_value_label, as_int(settings.get("fade_in_seconds"), int(round(self.fade_in_slider.get()))))
            self._set_slider(self.fade_out_slider, self.fade_out_value_label, as_int(settings.get("fade_out_seconds"), int(round(self.fade_out_slider.get()))))

            bitrate = str(settings.get("bitrate", self.bitrate_combo.get() or "320k"))
            self.bitrate_combo.set(bitrate)

            cut_mode_value = str(settings.get("cut_mode", CUT_MODE_LABELS.get(self.cut_mode_combo.get(), "inizio")))
            self.cut_mode_combo.set(CUT_MODE_VALUES.get(cut_mode_value, "Inizio del brano"))

            if bool(settings.get("normalize_audio", False)):
                self.normalize_checkbox.select()
            else:
                self.normalize_checkbox.deselect()

            if bool(settings.get("random_order", False)):
                self.random_checkbox.select()
            else:
                self.random_checkbox.deselect()

            if bool(settings.get("continue_short_tracks", False)):
                self.short_checkbox.select()
            else:
                self.short_checkbox.deselect()

            self.exclude_unrecoverable_var.set(bool(settings.get("exclude_unrecoverable_from_mix", False)))
            if self.exclude_unrecoverable_var.get():
                self.exclude_unrecoverable_checkbox.select()
            else:
                self.exclude_unrecoverable_checkbox.deselect()

            output_name = str(settings.get("output_name", self.output_name_entry.get().strip() or "MixFinale"))
            self._replace_entry(self.output_name_entry, output_name)

            output_folder = str(settings.get("output_folder", self.output_entry.get().strip()))
            if output_folder:
                self.output_folder = output_folder
                self._replace_entry(self.output_entry, output_folder)

            self._on_cut_mode_change(self.cut_mode_combo.get())
            self.update_preview()
        finally:
            self._suspend_project_dirty_tracking = False

    def _confirm_save_if_dirty(self) -> bool:
        if not self.project_dirty:
            return True

        answer = messagebox.askyesnocancel(
            "Modifiche non salvate",
            "Il progetto contiene modifiche non salvate. Vuoi salvarle?"
        )

        if answer is None:
            return False

        if answer is False:
            return True

        return self.save_project()

    def _get_selected_project_file_for_open(self) -> str:
        return filedialog.askopenfilename(
            title="Apri progetto MixCreatorPro",
            filetypes=[("MixCreatorPro Project", f"*{PROJECT_EXTENSION}"), ("Tutti i file", "*.*")]
        )

    def _get_selected_project_file_for_save(self) -> str:
        initial_name = self.project_name if self.project_name and self.project_name != "Nuovo progetto" else "NuovoProgetto"
        return filedialog.asksaveasfilename(
            title="Salva progetto MixCreatorPro",
            defaultextension=PROJECT_EXTENSION,
            initialfile=initial_name,
            filetypes=[("MixCreatorPro Project", f"*{PROJECT_EXTENSION}"), ("Tutti i file", "*.*")]
        )

    def new_project(self) -> None:
        if not self._confirm_save_if_dirty():
            return

        if self.worker.is_running:
            messagebox.showwarning("Operazione in corso", "Impossibile creare un nuovo progetto durante l'elaborazione del mix.")
            return

        self._reset_to_initial_state()

    def _reset_to_initial_state(self) -> None:
        self._suspend_project_dirty_tracking = True
        try:
            self.current_project_path = None
            self.project_name = ""
            self.project_source_folder = ""
            self.project_dirty = False

            self.input_folder = ""
            self.output_folder = ""
            self._replace_entry(self.input_entry, "")
            self._replace_entry(self.output_entry, "")
            self._set_diagnostics_entry_values(input_folder="", output_folder="")
            self._replace_entry(self.output_name_entry, self.settings.get("output_name", "MixFinale"))

            self.ordered_track_names = []
            self.track_clip_info = {}
            self._drag_source_index = None
            self._refresh_track_list_box()
            self.track_list.selection_clear(0, END)

            self._set_slider(self.clip_slider, self.clip_value_label, self.settings["clip_seconds"])
            self._set_slider(self.crossfade_slider, self.crossfade_value_label, self.settings["crossfade_seconds"])
            self._set_slider(self.fade_in_slider, self.fade_in_value_label, self.settings["fade_in_seconds"])
            self._set_slider(self.fade_out_slider, self.fade_out_value_label, self.settings["fade_out_seconds"])
            self.bitrate_combo.set(self.settings["bitrate"])
            self.cut_mode_combo.set(CUT_MODE_VALUES.get(self.settings["cut_mode"], "Inizio del brano"))

            if self.settings["normalize_audio"]:
                self.normalize_checkbox.select()
            else:
                self.normalize_checkbox.deselect()

            if self.settings["random_order"]:
                self.random_checkbox.select()
            else:
                self.random_checkbox.deselect()

            if self.settings["continue_short_tracks"]:
                self.short_checkbox.select()
            else:
                self.short_checkbox.deselect()

            self.exclude_unrecoverable_var.set(bool(self.settings.get("exclude_unrecoverable_from_mix", False)))
            if self.exclude_unrecoverable_var.get():
                self.exclude_unrecoverable_checkbox.select()
            else:
                self.exclude_unrecoverable_checkbox.deselect()

            self.last_generated_mix_data = None
            self._reusable_previous_clips = {}
            self._track_mix_times_ms = {}
            self._diagnostics_integrity_by_file = {}
            self._diagnostics_status_by_file = {}
            self._diagnostics_path_index = {}
            self._diagnostics_stable_index = {}
            self.track_filter_var.set(FILTER_ALL)
            self.reuse_previous_clips_var.set(False)
            self._refresh_reuse_previous_option(select_if_available=False)

            self._on_cut_mode_change(self.cut_mode_combo.get())
            self.update_preview()

            self.progress.set(0)
            self.percent_label.configure(text="0%")
            self.counter_label.configure(text="0 / 0")
            self.elapsed_label.configure(text="00:00:00")
            self.remaining_label.configure(text="--:--:--")
            self.status_label.configure(text="Pronto")
            self.status_bar_label.configure(text="Pronto")

            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")
            self._append_log("MixCreator PRO pronto.")
        finally:
            self._suspend_project_dirty_tracking = False

        self._update_window_title()
        self._update_tracks_count()
        self._update_controls_state()

    def open_project(self) -> None:
        if not self._confirm_save_if_dirty():
            return

        if self.worker.is_running:
            messagebox.showwarning("Operazione in corso", "Impossibile aprire un progetto durante l'elaborazione del mix.")
            return

        project_path = self._get_selected_project_file_for_open()
        if not project_path:
            return

        self._open_project_from_path(project_path)

    def _open_project_from_path(self, project_path: str) -> None:
        try:
            loaded = load_project_file(project_path)
            source_folder = Path(str(loaded.get("source_folder", ""))).expanduser()

            selected_folder = None
            if not source_folder.is_dir():
                selected_folder = filedialog.askdirectory(
                    title="Cartella progetto non trovata. Seleziona la nuova posizione"
                )
                if not selected_folder:
                    return

            result = resolve_project_files(loaded, selected_folder=selected_folder)

        except ProjectValidationError as error:
            messagebox.showerror("Progetto non valido", str(error))
            return
        except ProjectResolutionError as error:
            messagebox.showerror("Errore apertura progetto", str(error))
            return
        except ProjectManagerError as error:
            messagebox.showerror("Errore apertura progetto", str(error))
            return

        new_order: list[str] = []
        new_clip_info: dict[str, ClipInfo] = {}
        for item in result.tracks:
            file_name = str(item["file_name"])
            new_order.append(file_name)
            clip_info = item.get("clip_info")
            if isinstance(clip_info, ClipInfo):
                new_clip_info[file_name] = clip_info
            else:
                new_clip_info[file_name] = ClipInfo.from_dict(clip_info)

        self._suspend_project_dirty_tracking = True
        try:
            self.input_folder = result.source_folder
            self.project_source_folder = result.source_folder
            self._replace_entry(self.input_entry, result.source_folder)
            self._set_diagnostics_entry_values(input_folder=result.source_folder)

            output_folder = str(result.settings.get("output_folder", self.output_entry.get().strip() or result.source_folder))
            self.output_folder = output_folder or result.source_folder
            self._replace_entry(self.output_entry, self.output_folder)
            self._set_diagnostics_entry_values(output_folder=self.output_folder)

            self._apply_project_settings_to_ui(result.settings)

            self.ordered_track_names = new_order
            self.track_clip_info = new_clip_info
            self.last_generated_mix_data = self._sanitize_last_generated_mix(loaded.get("last_generated_mix"))
            self._load_latest_diagnostics_index(preferred_output_folder=self.output_folder)
            self._refresh_track_mix_times_index()
            self._refresh_track_list_box()
            self._refresh_reuse_previous_option(select_if_available=True)
            self.update_preview()
            self.status_label.configure(text=f"{self.track_count} MP3 inclusi nel mix")
        finally:
            self._suspend_project_dirty_tracking = False

        project_file = Path(project_path).with_suffix(PROJECT_EXTENSION)
        self._set_project_clean(str(project_file), project_file.stem, result.source_folder)
        self._update_tracks_count()
        self._update_controls_state()
        self.status_bar_label.configure(text="Progetto caricato")
        self._append_log(
            "Progetto caricato: "
            f"{len(new_order)} ripristinati, "
            f"{len(result.missing_files)} mancanti, "
            f"{len(result.new_files)} nuovi, "
            f"{len(result.modified_files)} modificati."
        )

        if result.missing_files or result.new_files or result.modified_files or result.warnings:
            summary_lines = [
                "Progetto caricato.",
                "",
                f"Brani ripristinati: {len(new_order)}",
                f"Brani mancanti: {len(result.missing_files)}",
                f"Nuovi brani trovati: {len(result.new_files)}",
                f"Brani modificati: {len(result.modified_files)}"
            ]

            if result.missing_files:
                summary_lines.append("")
                summary_lines.append("Elenco file mancanti:")
                summary_lines.extend(f"- {name}" for name in result.missing_files)

            if result.warnings:
                summary_lines.append("")
                summary_lines.append("Avvisi:")
                summary_lines.extend(f"- {warning}" for warning in result.warnings)

            messagebox.showinfo("Riepilogo caricamento progetto", "\n".join(summary_lines))

    def save_project(self) -> bool:
        if self.current_project_path:
            return self._save_project_to_path(self.current_project_path)
        return self.save_project_as()

    def save_project_as(self) -> bool:
        selected = self._get_selected_project_file_for_save()
        if not selected:
            return False
        return self._save_project_to_path(selected)

    def _save_project_to_path(self, path: str) -> bool:
        source_folder = self.input_entry.get().strip()
        if not source_folder or not Path(source_folder).is_dir():
            messagebox.showerror("Errore", "Seleziona una cartella sorgente valida prima di salvare il progetto.")
            return False

        tracks = self._build_project_track_payload()
        settings_payload = self._collect_project_settings()

        try:
            saved_path = save_project_file(
                project_path=path,
                source_folder=source_folder,
                tracks=tracks,
                project_settings=settings_payload,
                last_generated_mix=self.last_generated_mix_data,
            )
        except ProjectManagerError as error:
            messagebox.showerror("Errore salvataggio progetto", str(error))
            return False

        self._set_project_clean(str(saved_path), saved_path.stem, source_folder)
        self.status_bar_label.configure(text="Progetto salvato")
        self._append_log(f"Progetto salvato: {saved_path}")
        return True

    def _on_clip_change(self, value: float) -> None:
        self.clip_value_label.configure(text=f"{int(round(value))} s")
        self.update_preview()
        self._mark_project_dirty()

    def _on_crossfade_change(self, value: float) -> None:
        self.crossfade_value_label.configure(text=f"{int(round(value))} s")
        self.update_preview()
        self._mark_project_dirty()

    def _on_fade_in_change(self, value: float) -> None:
        self.fade_in_value_label.configure(text=f"{int(round(value))} s")
        self.update_preview()
        self._mark_project_dirty()

    def _on_fade_out_change(self, value: float) -> None:
        self.fade_out_value_label.configure(text=f"{int(round(value))} s")
        self.update_preview()
        self._mark_project_dirty()

    def _on_bitrate_change(self, _value: str) -> None:
        self.update_preview()
        self._mark_project_dirty()

    def _on_project_setting_toggled(self) -> None:
        self.update_preview()
        self._mark_project_dirty()

    def _on_reuse_previous_toggled(self) -> None:
        self._update_controls_state()

    def _on_output_name_changed(self, _event=None) -> None:
        self._mark_project_dirty()
        self._update_controls_state()

    def _on_cut_mode_change(self, _value: str) -> None:
        full_track_mode = (
            CUT_MODE_LABELS.get(
                self.cut_mode_combo.get(),
                "inizio"
            ) == "intero"
        )

        if full_track_mode:
            self.clip_slider.configure(state="disabled")
            self.clip_value_label.configure(text="Intero")
        else:
            self.clip_slider.configure(state="normal")
            self.clip_value_label.configure(
                text=f"{int(round(self.clip_slider.get()))} s"
            )

        self.update_preview()
        self._mark_project_dirty()

    def select_input(self) -> None:
        if not self._confirm_save_if_dirty():
            return

        folder = filedialog.askdirectory(
            title="Seleziona la cartella contenente gli MP3"
        )
        if not folder:
            return

        self.input_folder = folder
        self._replace_entry(self.input_entry, folder)
        self._set_diagnostics_entry_values(input_folder=folder)

        if not self.output_folder:
            self.output_folder = folder
            self._replace_entry(self.output_entry, folder)
            self._set_diagnostics_entry_values(output_folder=folder)

        self.load_mp3_list(mark_dirty=True)
        self.project_source_folder = folder
        self._update_controls_state()
        self.save_settings()

    def select_output(self) -> None:
        folder = filedialog.askdirectory(
            title="Seleziona la cartella di destinazione"
        )
        if not folder:
            return

        self.output_folder = folder
        self._replace_entry(self.output_entry, folder)
        self._set_diagnostics_entry_values(output_folder=folder)
        self._mark_project_dirty()
        self._update_controls_state()
        self.save_settings()

    def open_diagnostics_window(self) -> None:
        if self.diagnostics_window is not None and self.diagnostics_window.winfo_exists():
            self.diagnostics_window.deiconify()
            self._raise_diagnostics_window()
            return

        window = ctk.CTkToplevel(self)
        window.title("Diagnostica e Riparazione MP3")
        window.geometry("920x660")
        window.minsize(860, 620)
        window.resizable(True, True)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_diagnostics_window)
        window.transient(self)
        try:
            window.wm_attributes("-topmost", True)
            window.wm_attributes("-topmost", False)
        except Exception:
            pass
        self.diagnostics_window = window

        container = ctk.CTkFrame(window, fg_color="transparent")
        container.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        container.grid_columnconfigure(0, weight=1)

        self._build_diagnostics_section(container, start_row=0)
        self._set_diagnostics_entry_values(
            input_folder=self.input_entry.get().strip(),
            output_folder=self.output_entry.get().strip(),
        )
        self._raise_diagnostics_window()
        self._update_controls_state()

    def _close_diagnostics_window(self) -> None:
        if self.diagnostics_worker.is_running:
            should_close = messagebox.askyesno(
                "Diagnostica MP3",
                "Una diagnostica e in esecuzione. Vuoi nascondere la finestra?\n"
                "Potrai riaprirla durante l'elaborazione.",
                parent=self._diagnostics_dialog_parent(),
            )
            if not should_close:
                return
            if self.diagnostics_window is not None:
                self.diagnostics_window.withdraw()
            return

        if self.diagnostics_window is not None:
            try:
                self.diagnostics_window.destroy()
            except Exception:
                pass
        self.diagnostics_window = None

    def _set_diagnostics_entry_values(
        self,
        *,
        input_folder: str | None = None,
        output_folder: str | None = None,
    ) -> None:
        if input_folder is not None and hasattr(self, "diagnostics_input_entry"):
            self._replace_entry(self.diagnostics_input_entry, input_folder)
        if output_folder is not None and hasattr(self, "diagnostics_output_entry"):
            self._replace_entry(self.diagnostics_output_entry, output_folder)

    def _diagnostics_input_value(self) -> str:
        if hasattr(self, "diagnostics_input_entry"):
            return self.diagnostics_input_entry.get().strip()
        return ""

    def _diagnostics_dialog_parent(self):
        if self.diagnostics_window is not None and self.diagnostics_window.winfo_exists():
            return self.diagnostics_window
        return self

    def _raise_diagnostics_window(self) -> None:
        if self.diagnostics_window is None or not self.diagnostics_window.winfo_exists():
            return
        try:
            self.diagnostics_window.transient(self)
        except Exception:
            pass
        try:
            self.diagnostics_window.lift()
            self.diagnostics_window.focus_force()
            self.diagnostics_window.wm_attributes("-topmost", True)
            self.diagnostics_window.wm_attributes("-topmost", False)
        except Exception:
            try:
                self.diagnostics_window.focus_set()
            except Exception:
                pass

    def _diagnostics_output_value(self) -> str:
        if hasattr(self, "diagnostics_output_entry"):
            return self.diagnostics_output_entry.get().strip()
        return ""

    def _selective_reverify_report_picker(self) -> Path | None:
        last_path = str(self.settings.get("diagnostics_last_reverify_csv", "") or "").strip()
        initial_dir = ""
        initial_file = ""
        if last_path:
            last = Path(last_path).expanduser()
            if last.is_file():
                initial_dir = str(last.parent)
                initial_file = last.name
            elif last.parent.is_dir():
                initial_dir = str(last.parent)

        if not initial_dir:
            output = self._diagnostics_output_value() or self.output_entry.get().strip()
            if output and Path(output).is_dir():
                initial_dir = output

        selected = filedialog.askopenfilename(
            title="Seleziona report CSV della diagnostica precedente",
            parent=self._diagnostics_dialog_parent(),
            filetypes=[("CSV", "*.csv"), ("Tutti i file", "*.*")],
            initialdir=initial_dir or None,
            initialfile=initial_file or None,
        )
        if not selected:
            return None

        selected_path = Path(selected).expanduser().resolve()
        self.settings["diagnostics_last_reverify_csv"] = str(selected_path)
        self.save_settings()
        return selected_path

    def _show_selective_reverify_summary(self, selection: SelectiveReverifySelection) -> bool:
        dialog = ctk.CTkToplevel(self._diagnostics_dialog_parent())
        dialog.title("Riverifica file problematici")
        dialog.geometry("760x420")
        dialog.resizable(False, False)
        dialog.transient(self._diagnostics_dialog_parent())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(
            dialog,
            text="Riepilogo pre-avvio riverifica",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        summary_box = ctk.CTkTextbox(dialog, height=260)
        summary_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        summary_box.insert(
            "end",
            "\n".join(
                [
                    f"Report selezionato: {selection.report_csv_path}",
                    f"Righe totali: {selection.total_rows}",
                    f"Precedentemente Riparati: {selection.repaired_rows}",
                    f"Precedentemente Non recuperabili: {selection.unrecoverable_rows}",
                    f"Duplicati esclusi: {selection.duplicates_excluded}",
                    f"Originali mancanti: {len(selection.missing_originals)}",
                    f"Originali validi: {len(selection.valid_original_files)}",
                    f"Numero finale da riverificare: {selection.final_reverify_count}",
                ]
            ),
        )
        summary_box.configure(state="disabled")

        result = {"start": False}

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        buttons.grid_columnconfigure((0, 1), weight=1)

        def _cancel() -> None:
            result["start"] = False
            dialog.destroy()

        def _start() -> None:
            result["start"] = True
            dialog.destroy()

        start_button = ctk.CTkButton(
            buttons,
            text="Avvia riverifica",
            command=_start,
            state="normal" if selection.final_reverify_count > 0 else "disabled",
        )
        start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        cancel_button = ctk.CTkButton(
            buttons,
            text="Annulla",
            command=_cancel,
        )
        cancel_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        self.wait_window(dialog)
        return bool(result["start"])

    def _selective_reverify_output_picker(self) -> str:
        initial = self._diagnostics_output_value() or self.output_entry.get().strip()
        selected = filedialog.askdirectory(
            title="Seleziona cartella output riverifica file problematici",
            parent=self._diagnostics_dialog_parent(),
            initialdir=initial or None,
        )
        if not selected:
            return ""
        return str(Path(selected).expanduser().resolve())

    def _confirm_selective_reverify_output(self, selection: SelectiveReverifySelection, output_folder: str) -> bool:
        out_path = Path(output_folder).expanduser().resolve()
        report_dir = selection.report_csv_path.parent.resolve()
        report_root = report_dir.parent.resolve()

        if self._is_same_or_subpath(out_path, report_dir) or self._is_same_or_subpath(out_path, report_root):
            confirm = messagebox.askyesno(
                "Riverifica file problematici",
                "La cartella di output coincide con la cartella del report precedente "
                "o con una sua sottocartella. Vuoi continuare comunque?",
                parent=self._diagnostics_dialog_parent(),
            )
            if not confirm:
                return False

        source_dirs = {path.parent.resolve() for path in selection.valid_original_files}
        if any(out_path == source_dir for source_dir in source_dirs):
            confirm = messagebox.askyesno(
                "Riverifica file problematici",
                "La cartella di output coincide con una cartella sorgente dei file originali. "
                "Vuoi continuare comunque?",
                parent=self._diagnostics_dialog_parent(),
            )
            if not confirm:
                return False

        return True

    @staticmethod
    def _is_same_or_subpath(path: Path, root: Path) -> bool:
        try:
            resolved_path = path.resolve()
            resolved_root = root.resolve()
        except Exception:
            return False

        return resolved_path == resolved_root or MixCreatorApp._is_relative_to(resolved_path, resolved_root)

    @staticmethod
    def _common_parent_for_files(paths: list[Path]) -> Path | None:
        if not paths:
            return None
        try:
            common = os.path.commonpath([str(path.resolve().parent) for path in paths])
            candidate = Path(common).resolve()
            if candidate.is_dir():
                return candidate
        except Exception:
            return None
        return None

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def select_diagnostics_input(self) -> None:
        folder = filedialog.askdirectory(
            title="Seleziona cartella input diagnostica MP3",
            parent=self._diagnostics_dialog_parent(),
        )
        if not folder:
            self._raise_diagnostics_window()
            return
        self._set_diagnostics_entry_values(input_folder=folder)
        self._raise_diagnostics_window()

    def select_diagnostics_output(self) -> None:
        folder = filedialog.askdirectory(
            title="Seleziona cartella output diagnostica MP3",
            parent=self._diagnostics_dialog_parent(),
        )
        if not folder:
            self._raise_diagnostics_window()
            return
        self._set_diagnostics_entry_values(output_folder=folder)
        self._raise_diagnostics_window()

    def start_diagnostics_analysis(self) -> None:
        self._start_diagnostics_worker(repair_mode=False)

    def start_diagnostics_repair(self) -> None:
        self._start_diagnostics_worker(repair_mode=True)

    def start_selective_reverify(self) -> None:
        if self.diagnostics_worker.is_running:
            messagebox.showwarning(
                "Diagnostica MP3",
                "Una diagnostica e gia in esecuzione.",
                parent=self._diagnostics_dialog_parent(),
            )
            return

        csv_path = self._selective_reverify_report_picker()
        if csv_path is None:
            self._raise_diagnostics_window()
            return

        try:
            selection = prepare_selective_reverify_selection(csv_path)
        except SelectiveReverifyError as error:
            messagebox.showerror("Riverifica file problematici", str(error), parent=self._diagnostics_dialog_parent())
            self._raise_diagnostics_window()
            return

        if (selection.repaired_rows + selection.unrecoverable_rows) == 0:
            messagebox.showinfo(
                "Riverifica file problematici",
                "Nessun file problematico da riverificare nel report selezionato.",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

        should_start = self._show_selective_reverify_summary(selection)
        if not should_start:
            self._raise_diagnostics_window()
            return

        output_folder = self._selective_reverify_output_picker()
        if not output_folder:
            self._raise_diagnostics_window()
            return

        if not self._confirm_selective_reverify_output(selection, output_folder):
            self._raise_diagnostics_window()
            return

        selected_files = list(selection.valid_original_files)
        source_folder = self._common_parent_for_files(selected_files)
        if source_folder is None and selected_files:
            source_folder = selected_files[0].parent

        if source_folder is None:
            messagebox.showerror(
                "Riverifica file problematici",
                "Nessun file originale valido disponibile per l'avvio.",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

        self.diagnostics_reverify_selection = selection
        self._start_diagnostics_worker(
            repair_mode=True,
            input_folder=str(source_folder),
            output_folder=output_folder,
            include_subfolders=True,
            selected_input_files=selected_files,
            run_mode="selective_reverify",
            start_message="Avvio riverifica file problematici...",
            log_message="Avvio riverifica file problematici.",
        )

    def _start_diagnostics_worker(
        self,
        repair_mode: bool,
        *,
        input_folder: str | None = None,
        output_folder: str | None = None,
        include_subfolders: bool | None = None,
        selected_input_files: list[Path] | None = None,
        run_mode: str = "normal",
        start_message: str = "Avvio diagnostica MP3...",
        log_message: str = "Avvio diagnostica MP3.",
    ) -> None:
        if self.diagnostics_worker.is_running:
            messagebox.showwarning(
                "Diagnostica MP3",
                "Una diagnostica e gia in esecuzione.",
                parent=self._diagnostics_dialog_parent(),
            )
            return

        if not hasattr(self, "diagnostics_progress"):
            self.open_diagnostics_window()

        selected_input = input_folder or self._diagnostics_input_value() or self.input_entry.get().strip()
        selected_output = output_folder or self._diagnostics_output_value() or self.output_entry.get().strip()
        selected_subfolders = bool(self.diagnostics_include_subfolders_var.get()) if include_subfolders is None else bool(include_subfolders)
        placement_mode = str(self.diagnostics_placement_mode_var.get() or "copy").strip().lower()
        if placement_mode not in ("copy", "move"):
            placement_mode = "copy"

        if not selected_input or not Path(selected_input).is_dir():
            messagebox.showerror(
                "Diagnostica MP3",
                "Seleziona una cartella di input valida.",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

        if not selected_output:
            messagebox.showerror(
                "Diagnostica MP3",
                "Seleziona una cartella di output valida.",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

        try:
            Path(selected_output).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror(
                "Diagnostica MP3",
                f"Impossibile creare la cartella output:\n{error}",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

        self.diagnostics_run_mode = run_mode
        self._set_diagnostics_entry_values(input_folder=selected_input, output_folder=selected_output)
        self.settings["diagnostics_placement_mode"] = placement_mode
        self.save_settings()

        self.diagnostics_worker_total = 0
        self.diagnostics_last_progress = 0
        self.diagnostics_worker_start_time = time.monotonic()
        self.diagnostics_progress.set(0)
        self.diagnostics_status_label.configure(text=start_message)
        self.diagnostics_count_label.configure(text="File analizzati: 0 / 0")
        self.diagnostics_elapsed_label.configure(text="Tempo: 00:00:00")
        self.diagnostics_eta_label.configure(text="Tempo stimato: calcolo in corso...")
        self._append_diagnostics_log(log_message)
        self._start_diagnostics_timer()

        try:
            self.diagnostics_worker.start(
                input_folder=selected_input,
                include_subfolders=selected_subfolders,
                output_folder=selected_output,
                repair_mode=repair_mode,
                placement_mode=placement_mode,
                selected_input_files=selected_input_files,
            )
        except Exception as error:
            self.diagnostics_run_mode = "normal"
            self.diagnostics_reverify_selection = None
            self._stop_diagnostics_timer()
            messagebox.showerror("Diagnostica MP3", str(error), parent=self._diagnostics_dialog_parent())
            self._raise_diagnostics_window()
            return

        self._update_controls_state()

    def stop_diagnostics(self) -> None:
        if not self.diagnostics_worker.is_running:
            return
        self.diagnostics_worker.cancel()
        self._append_diagnostics_log("Richiesta interruzione diagnostica.")

    def _diagnostics_worker_progress(self, current: int, total: int, message: str) -> None:
        self.after(0, self._handle_diagnostics_worker_progress, current, total, message)

    def _handle_diagnostics_worker_progress(self, current: int, total: int, message: str) -> None:
        self.diagnostics_worker_total = max(self.diagnostics_worker_total, int(total))
        self.diagnostics_last_progress = max(0, min(int(current), int(total) if total > 0 else int(current)))

        if total > 0:
            percent = max(0.0, min(1.0, current / float(total)))
            self.diagnostics_progress.set(percent)
            self.diagnostics_count_label.configure(text=f"File analizzati: {max(0, current)} / {total}")

        self.diagnostics_status_label.configure(text=message)
        self._append_diagnostics_log(message)
        self._update_diagnostics_eta()

    def _diagnostics_worker_completed(self, payload: dict[str, Any]) -> None:
        self.after(0, self._handle_diagnostics_worker_completed, payload)

    def _handle_diagnostics_worker_completed(self, payload: dict[str, Any]) -> None:
        self._stop_diagnostics_timer()
        self.diagnostics_progress.set(1)
        self.diagnostics_status_label.configure(text="Diagnostica MP3 completata")

        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        report_paths = payload.get("report_paths", {}) if isinstance(payload, dict) else {}
        self._append_diagnostics_log("Diagnostica completata.")
        self._append_diagnostics_log(f"Report CSV: {report_paths.get('csv', '')}")
        self._append_diagnostics_log(f"Report XLSX: {report_paths.get('xlsx', '')}")
        self._append_diagnostics_log(f"Report HTML: {report_paths.get('html', '')}")

        comparative_path = ""
        comparative_error = ""
        if self.diagnostics_run_mode == "selective_reverify":
            try:
                comparative_path = self._write_selective_comparative_report(report_paths)
                if comparative_path:
                    self._append_diagnostics_log(f"Report comparativo: {comparative_path}")
            except Exception as error:
                comparative_error = str(error)
                self._append_diagnostics_log(f"ERRORE report comparativo: {comparative_error}")

        self._load_latest_diagnostics_index(preferred_output_folder=self._diagnostics_output_value())
        self._refresh_track_list_box()
        self.update_preview()

        title = "Riverifica file problematici" if self.diagnostics_run_mode == "selective_reverify" else "Diagnostica MP3"
        base_message = (
            "Diagnostica completata.\n\n"
            f"File già rilevati OK: {summary.get('category_ok_files', 0)}\n"
            f"File riparati: {summary.get('category_repaired_files', 0)}\n"
            f"File non recuperabili: {summary.get('category_unrecoverable_files', 0)}\n\n"
            f"Anomalie tecniche ignorate in zone silenziose: {summary.get('ignored_silent_anomalies', 0)}\n"
            f"Totale analizzati: {summary.get('analyzed_files', 0)}"
        )

        if comparative_path:
            base_message += f"\n\nReport comparativo: {comparative_path}"

        self._update_controls_state()
        messagebox.showinfo(title, base_message, parent=self._diagnostics_dialog_parent())

        if comparative_error:
            messagebox.showwarning(
                title,
                "La diagnostica è stata completata, ma la scrittura del report comparativo è fallita.\n\n"
                f"Dettaglio: {comparative_error}",
                parent=self._diagnostics_dialog_parent(),
            )

        self.diagnostics_run_mode = "normal"
        self.diagnostics_reverify_selection = None
        self.diagnostics_eta_label.configure(text="Tempo stimato: completato")
        self._raise_diagnostics_window()

    def _diagnostics_worker_error(self, message: str) -> None:
        self.after(0, self._handle_diagnostics_worker_error, message)

    def _handle_diagnostics_worker_error(self, message: str) -> None:
        self._stop_diagnostics_timer()
        self.diagnostics_status_label.configure(text="Errore diagnostica MP3")
        self.diagnostics_eta_label.configure(text="Tempo stimato: non disponibile")
        self._append_diagnostics_log(f"ERRORE: {message}")
        self.diagnostics_run_mode = "normal"
        self.diagnostics_reverify_selection = None
        self._update_controls_state()
        messagebox.showerror("Diagnostica MP3", message, parent=self._diagnostics_dialog_parent())
        self._raise_diagnostics_window()

    def _diagnostics_worker_cancelled(self, message: str) -> None:
        self.after(0, self._handle_diagnostics_worker_cancelled, message)

    def _handle_diagnostics_worker_cancelled(self, message: str) -> None:
        self._stop_diagnostics_timer()
        self.diagnostics_status_label.configure(text="Diagnostica MP3 interrotta")
        self.diagnostics_eta_label.configure(text="Tempo stimato: annullato")
        self._append_diagnostics_log(message)
        self.diagnostics_run_mode = "normal"
        self.diagnostics_reverify_selection = None
        self._update_controls_state()

    def _write_selective_comparative_report(self, report_paths: dict[str, Any]) -> str:
        selection = self.diagnostics_reverify_selection
        if selection is None:
            return ""

        summary_path_text = str(report_paths.get("csv_summary", "")).strip()
        if not summary_path_text:
            return ""

        summary_path = Path(summary_path_text)
        if not summary_path.is_file():
            return ""

        with summary_path.open("r", encoding="utf-8", newline="") as handle:
            summary_rows = list(csv.DictReader(handle))

        by_original_path: dict[str, dict[str, str]] = {}
        for row in summary_rows:
            key = self._normalize_path_key(str(row.get("Percorso originale", "")).strip())
            if key and key not in by_original_path:
                by_original_path[key] = row

        missing_reason_by_key: dict[str, str] = {}
        for missing in selection.missing_originals:
            key = self._normalize_path_key(missing.row.original_path)
            missing_reason_by_key[key] = missing.reason

        output_rows: list[dict[str, str]] = []
        for previous in selection.selected_rows:
            key = self._normalize_path_key(previous.original_path)
            current = by_original_path.get(key)

            if current is None:
                new_status = "Originale non trovato"
                new_category = ""
                new_significant_end = ""
                new_trailing = ""
                output_path = ""
                reason = missing_reason_by_key.get(key, "Originale non trovato")
            else:
                new_status = str(current.get("Stato finale file", "")).strip()
                new_category = str(current.get("Categoria finale", "")).strip()
                new_significant_end = str(current.get("Fine audio significativo", "")).strip()
                new_trailing = str(current.get("Silenzio finale (ms)", "")).strip()
                output_path = str(current.get("Percorso finale", "")).strip()

                changes: list[str] = []
                if previous.previous_status != new_status:
                    changes.append("Stato aggiornato")
                if previous.previous_category != new_category:
                    changes.append("Categoria aggiornata")
                if previous.previous_significant_end != new_significant_end:
                    changes.append("Fine audio significativo aggiornata")
                if previous.previous_trailing_silence_ms != new_trailing:
                    changes.append("Silenzio finale aggiornato")
                reason = "; ".join(changes) if changes else "Nessun cambiamento"

            changed = "SI" if (
                previous.previous_status != new_status
                or previous.previous_category != new_category
                or previous.previous_significant_end != new_significant_end
                or previous.previous_trailing_silence_ms != new_trailing
            ) else "NO"

            output_rows.append(
                {
                    "File": previous.file_name,
                    "Percorso originale": previous.original_path,
                    "Stato precedente": previous.previous_status,
                    "Stato nuovo": new_status,
                    "Categoria precedente": previous.previous_category,
                    "Categoria nuova": new_category,
                    "Fine audio significativo precedente": previous.previous_significant_end,
                    "Fine audio significativo nuova": new_significant_end,
                    "Silenzio finale precedente (ms)": previous.previous_trailing_silence_ms,
                    "Silenzio finale nuovo (ms)": new_trailing,
                    "Esito cambiato Sì/No": changed,
                    "Motivo del cambiamento": reason,
                    "Percorso output nuovo": output_path,
                }
            )

        comparative_path = summary_path.parent / "Riverifica_Comparativa.csv"
        fieldnames = [
            "File",
            "Percorso originale",
            "Stato precedente",
            "Stato nuovo",
            "Categoria precedente",
            "Categoria nuova",
            "Fine audio significativo precedente",
            "Fine audio significativo nuova",
            "Silenzio finale precedente (ms)",
            "Silenzio finale nuovo (ms)",
            "Esito cambiato Sì/No",
            "Motivo del cambiamento",
            "Percorso output nuovo",
        ]
        with comparative_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_rows)

        return str(comparative_path)

    def _start_diagnostics_timer(self) -> None:
        if self.diagnostics_timer_job is not None:
            self.after_cancel(self.diagnostics_timer_job)
        self._tick_diagnostics_timer()

    def _stop_diagnostics_timer(self) -> None:
        if self.diagnostics_timer_job is not None:
            self.after_cancel(self.diagnostics_timer_job)
            self.diagnostics_timer_job = None
        self.diagnostics_worker_start_time = None

    def _tick_diagnostics_timer(self) -> None:
        if self.diagnostics_worker_start_time is None:
            self.diagnostics_timer_job = None
            return

        elapsed = max(0.0, time.monotonic() - self.diagnostics_worker_start_time)
        self.diagnostics_elapsed_label.configure(text=f"Tempo: {self._format_duration(elapsed)}")
        self._update_diagnostics_eta()
        self.diagnostics_timer_job = self.after(1000, self._tick_diagnostics_timer)

    def _update_diagnostics_eta(self) -> None:
        if self.diagnostics_worker_start_time is None:
            self.diagnostics_eta_label.configure(text="Tempo stimato: --")
            return

        total = self.diagnostics_worker_total
        progress = self.diagnostics_last_progress
        if total <= 0 or progress <= 0:
            self.diagnostics_eta_label.configure(text="Tempo stimato: calcolo in corso...")
            return

        elapsed = max(0.0, time.monotonic() - self.diagnostics_worker_start_time)
        avg_per_file = elapsed / float(progress)
        remaining_files = max(0, total - progress)
        remaining = max(0.0, avg_per_file * float(remaining_files))
        if remaining < 60:
            seconds_text = int(round(remaining))
            self.diagnostics_eta_label.configure(text=f"Tempo stimato: {seconds_text} secondi")
            return

        if remaining >= 3600:
            self.diagnostics_eta_label.configure(text=f"Tempo stimato: {self._format_duration(remaining)}")
            return

        minutes = int(remaining // 60)
        seconds = int(round(remaining % 60))
        if seconds == 60:
            minutes += 1
            seconds = 0
        self.diagnostics_eta_label.configure(text=f"Tempo stimato: {minutes:02d}:{seconds:02d}")

    def _append_diagnostics_log(self, message: str) -> None:
        if not hasattr(self, "diagnostics_log_box"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.diagnostics_log_box.configure(state="normal")
        self.diagnostics_log_box.insert("end", f"{timestamp}  {message}\n")
        self.diagnostics_log_box.see("end")
        self.diagnostics_log_box.configure(state="disabled")

    def _load_latest_diagnostics_index(self, preferred_output_folder: str | None = None) -> None:
        index_file = self._find_latest_diagnostics_index_file(preferred_output_folder)
        if index_file is None:
            self._diagnostics_integrity_by_file = {}
            self._diagnostics_status_by_file = {}
            self._diagnostics_path_index = {}
            self._diagnostics_stable_index = {}
            self._append_diagnostics_log("IntegrityIndex non trovato.")
            return

        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._diagnostics_integrity_by_file = {}
            self._diagnostics_status_by_file = {}
            self._diagnostics_path_index = {}
            self._diagnostics_stable_index = {}
            self._append_diagnostics_log(f"Lettura IntegrityIndex fallita: {index_file}")
            return

        items = payload.get("items", []) if isinstance(payload, dict) else []
        by_name: dict[str, dict[str, Any]] = {}
        by_path: dict[str, dict[str, Any]] = {}
        by_stable_key: dict[str, dict[str, Any]] = {}
        status_map: dict[str, str] = {}

        self._append_diagnostics_log(f"IntegrityIndex caricato: {index_file}")
        self._append_diagnostics_log(f"Record caricati: {len(items)}")

        for item in items:
            if not isinstance(item, dict):
                continue
            file_name = str(item.get("file_name", "")).strip()
            file_path = str(item.get("file_path", "")).strip()
            normalized_path = str(item.get("normalized_path", "")).strip()
            stable_key = str(item.get("stable_key", "")).strip()
            if not file_name:
                continue
            by_name[file_name] = item
            status_map[file_name] = str(item.get("status", ""))
            if normalized_path:
                by_path[normalized_path] = item
            elif file_path:
                by_path[self._normalize_path_key(file_path)] = item
            if stable_key:
                by_stable_key[stable_key] = item

        self._diagnostics_integrity_by_file = by_name
        self._diagnostics_status_by_file = status_map
        self._diagnostics_path_index = by_path
        self._diagnostics_stable_index = by_stable_key

    def _find_latest_diagnostics_index_file(self, preferred_output_folder: str | None = None) -> Path | None:
        candidates: list[Path] = []

        output_folder = (preferred_output_folder or "").strip()
        if output_folder:
            candidates.append(Path(output_folder).expanduser() / "REPORT" / "IntegrityIndex.json")

        if hasattr(self, "diagnostics_output_entry"):
            entry_folder = self.diagnostics_output_entry.get().strip()
            if entry_folder:
                candidates.append(Path(entry_folder).expanduser() / "REPORT" / "IntegrityIndex.json")

        if hasattr(self, "output_entry"):
            generic_output = self.output_entry.get().strip()
            if generic_output:
                candidates.append(Path(generic_output).expanduser() / "REPORT" / "IntegrityIndex.json")

        if candidates:
            for item in candidates:
                self._append_diagnostics_log(f"Ricerca IntegrityIndex: {item}")

        existing = [path for path in candidates if path.is_file()]
        if not existing:
            return None

        existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        self._append_diagnostics_log(f"IntegrityIndex selezionato: {existing[0]}")
        return existing[0]

    @staticmethod
    def _replace_entry(entry, value: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, value)

    @staticmethod
    def _normalize_path_key(path: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _add_tooltip(self, widget, text: str) -> Tooltip:
        tooltip = Tooltip(widget, text)
        self.tooltips.append(tooltip)
        return tooltip

    def _format_time_with_ms(self, seconds: float) -> str:
        total_ms = int(round(seconds * 1000))
        minutes, remainder = divmod(total_ms, 60000)
        seconds_whole, milliseconds = divmod(remainder, 1000)
        return f"{minutes:02d}:{seconds_whole:02d}.{milliseconds:03d}"

    @staticmethod
    def _format_hhmmss_from_ms(total_ms: int) -> str:
        clamped = max(0, int(total_ms))
        hours = clamped // 3_600_000
        remainder = clamped % 3_600_000
        minutes = remainder // 60_000
        seconds = (remainder % 60_000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_hhmmss_mmm_from_ms(total_ms: int) -> str:
        clamped = max(0, int(total_ms))
        hours = clamped // 3_600_000
        remainder = clamped % 3_600_000
        minutes = remainder // 60_000
        seconds = (remainder % 60_000) // 1000
        milliseconds = clamped % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

    def _mix_start_display_for_track(self, file_name: str) -> str:
        mix_start_ms = self._track_mix_times_ms.get(file_name)
        if mix_start_ms is None:
            return ""
        return self._format_hhmmss_from_ms(mix_start_ms)

    def _integrity_info_for_track(self, file_name: str) -> dict[str, Any] | None:
        if file_name in self._diagnostics_integrity_by_file:
            return self._diagnostics_integrity_by_file[file_name]

        source_folder = self.input_entry.get().strip() if hasattr(self, "input_entry") else ""
        if source_folder:
            candidate_path = (Path(source_folder) / file_name).resolve()
            norm_path = self._normalize_path_key(str(candidate_path))

            info_by_path = self._diagnostics_path_index.get(norm_path)
            if info_by_path is not None:
                return info_by_path

            try:
                stat_info = candidate_path.stat()
                stable_key = f"{norm_path}|{int(stat_info.st_size)}|{int(round(stat_info.st_mtime))}"
                info_by_stable = self._diagnostics_stable_index.get(stable_key)
                if info_by_stable is not None:
                    return info_by_stable
                self._append_diagnostics_log(
                    f"Mancato match integrita: {file_name} | key={stable_key}"
                )
            except OSError:
                self._append_diagnostics_log(
                    f"Mancato match integrita: {file_name} | file non leggibile"
                )

        return None

    def _integrity_display_for_track(self, file_name: str) -> str:
        info = self._integrity_info_for_track(file_name)
        if not isinstance(info, dict):
            return "—"

        try:
            value = int(info.get("integrity_index", -1))
        except (TypeError, ValueError):
            value = -1

        if value < 0:
            return "—"
        return f"{value}%"

    def _is_track_unrecoverable(self, file_name: str) -> bool:
        info = self._integrity_info_for_track(file_name)
        if not isinstance(info, dict):
            return False
        status = str(info.get("status", "")).strip().lower()
        return status == STATUS_UNRECOVERABLE.lower()

    def _filtered_track_names(self) -> list[str]:
        filter_value = self.track_filter_var.get().strip() if hasattr(self, "track_filter_var") else FILTER_ALL
        if filter_value == FILTER_ALL:
            return list(self.ordered_track_names)

        filtered: list[str] = []
        for file_name in self.ordered_track_names:
            info = self._integrity_info_for_track(file_name)
            status = str(info.get("status", "")).strip().lower() if isinstance(info, dict) else ""

            if filter_value == FILTER_PERFECT and status == STATUS_PERFECT.lower():
                filtered.append(file_name)
            elif filter_value == FILTER_REPAIRED and status == STATUS_REPAIRED.lower():
                filtered.append(file_name)
            elif filter_value == FILTER_UNRECOVERABLE and status == STATUS_UNRECOVERABLE.lower():
                filtered.append(file_name)

        return filtered

    def _selected_visible_file_name(self) -> str | None:
        index = self._selected_visible_index()
        if index is None:
            return None
        if index < 0 or index >= len(self._display_track_names):
            return None
        return self._display_track_names[index]

    def _selected_visible_index(self) -> int | None:
        if not hasattr(self, "track_list"):
            return None
        selection = self.track_list.curselection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError, IndexError):
            return None

    def _format_track_list_entry(self, file_name: str) -> str:
        clip_info = self.track_clip_info.get(file_name)
        mix_start_text = self._mix_start_display_for_track(file_name)
        integrity_text = self._integrity_display_for_track(file_name)
        unrecoverable_prefix = "[!NR] " if self._is_track_unrecoverable(file_name) else ""

        base_text = f"{unrecoverable_prefix}{file_name}   [Integrita: {integrity_text}]"
        if mix_start_text:
            base_text = f"{base_text}   {mix_start_text}"

        if clip_info and clip_info.use_custom_clip:
            start = clip_info.clip_start_ms / 1000.0
            end = clip_info.clip_end_ms / 1000.0
            return (
                f"{base_text}   [CLIP "
                f"{self._format_time_with_ms(start)} → "
                f"{self._format_time_with_ms(end)}]"
            )

        return base_text

    def _sync_clip_info(self, file_names: list[str]) -> None:
        updated: dict[str, ClipInfo] = {}

        for file_name in file_names:
            if file_name in self.track_clip_info:
                updated[file_name] = self.track_clip_info[file_name]
            else:
                updated[file_name] = ClipInfo()

        self.track_clip_info = updated

    @staticmethod
    def scan_mp3_files(folder_path: str | Path) -> list[str]:
        return [path.name for path in scan_mp3_files(folder_path)]

    def _refresh_track_list_box(
        self,
        selected_index: int | None = None,
        ensure_visible: bool = False,
    ) -> None:
        old_selection = tuple(self.track_list.curselection()) if hasattr(self, "track_list") else ()
        old_selected_name = None
        if old_selection and 0 <= old_selection[0] < len(self._display_track_names):
            old_selected_name = self._display_track_names[old_selection[0]]
        old_yview = self.track_list.yview() if hasattr(self, "track_list") else ()
        try:
            old_active = int(self.track_list.index("active")) if self.track_list.size() > 0 else None
        except Exception:
            old_active = None

        self._updating_track_list = True
        try:
            self.track_list.delete(0, END)

            display_names = self._filtered_track_names()
            self._display_track_names = display_names

            for index, file_name in enumerate(display_names, start=1):
                self.track_list.insert(
                    END,
                    f"{index:03d} - {self._format_track_list_entry(file_name)}"
                )

                info = self._integrity_info_for_track(file_name)
                status = str(info.get("status", "")).strip().lower() if isinstance(info, dict) else ""
                color = None
                if status == STATUS_PERFECT.lower():
                    color = "#2f8f57"
                elif status == STATUS_REPAIRED.lower():
                    color = "#1f8bff"
                elif status == STATUS_UNRECOVERABLE.lower():
                    color = "#8a8a8a"

                if color is not None:
                    try:
                        self.track_list.itemconfig(index - 1, fg=color)
                    except Exception:
                        pass

            size = self.track_list.size()
            if size > 0:
                target_index = None
                if selected_index is not None:
                    target_index = max(0, min(selected_index, size - 1))
                elif old_selected_name is not None and old_selected_name in display_names:
                    target_index = display_names.index(old_selected_name)
                elif old_selection:
                    preserved = [index for index in old_selection if 0 <= index < size]
                    if preserved:
                        target_index = preserved[0]
                elif old_active is not None and 0 <= old_active < size:
                    target_index = old_active

                self.track_list.selection_clear(0, END)
                if target_index is not None:
                    self.track_list.selection_set(target_index)
                    self.track_list.activate(target_index)
                    self.track_list.focus_set()

                if ensure_visible and target_index is not None:
                    self.track_list.see(target_index)
                elif old_yview:
                    self.track_list.yview_moveto(old_yview[0])
            else:
                self.track_list.selection_clear(0, END)
        finally:
            self._updating_track_list = False

        self._update_tracks_count()
        self._update_controls_state()
        self._refresh_reuse_previous_option(select_if_available=False)
        self._update_selected_track_mix_details()

    def _on_track_filter_change(self, _value: str) -> None:
        self._refresh_track_list_box()

    def refresh_input_folder(self) -> None:
        folder = self.input_entry.get().strip()
        source_changed = folder and folder != self.input_folder

        if source_changed:
            if not self._confirm_save_if_dirty():
                return

        if not folder or not Path(folder).is_dir():
            messagebox.showerror(
                "Errore",
                "La cartella MP3 selezionata non è valida."
            )
            return

        self.input_folder = folder
        self.project_source_folder = folder

        current_order = self.get_ordered_track_names()

        try:
            disk_files = self.scan_mp3_files(folder)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror(
                "Errore",
                f"Impossibile leggere la cartella:\n{error}"
            )
            return

        disk_set = set(disk_files)
        current_set = set(current_order)

        removed = [
            file_name
            for file_name in current_order
            if file_name not in disk_set
        ]

        new_files = [
            file_name
            for file_name in disk_files
            if file_name not in current_set
        ]

        retained = [
            file_name
            for file_name in current_order
            if file_name in disk_set
        ]

        updated_order = retained + new_files
        self._replace_track_order(updated_order)
        self._load_latest_diagnostics_index(preferred_output_folder=self.output_entry.get().strip())
        self.update_preview()

        self.status_label.configure(
            text=f"{self.track_count} MP3 inclusi nel mix"
        )
        self.status_bar_label.configure(
            text=f"{self.track_count} MP3 aggiornati"
        )

        self._append_log(
            "Aggiornamento cartella completato: "
            f"+{len(new_files)} nuovi, "
            f"-{len(removed)} rimossi, "
            f"totale {self.track_count}."
        )

        if new_files or removed:
            self._mark_project_dirty()
        elif source_changed:
            self._mark_project_dirty()

        self._update_tracks_count()
        self._update_controls_state()

    def load_mp3_list(self, mark_dirty: bool = False) -> None:
        try:
            files = self.scan_mp3_files(self.input_folder)
        except (OSError, FileNotFoundError) as error:
            self.status_label.configure(text=f"Errore lettura cartella: {error}")
            return

        self.ordered_track_names = files
        self._sync_clip_info(files)
        self._load_latest_diagnostics_index(preferred_output_folder=self.output_entry.get().strip())
        self._refresh_track_mix_times_index()
        self._refresh_track_list_box()

        self.status_label.configure(text=f"{len(files)} MP3 trovati")
        self.status_bar_label.configure(text=f"{len(files)} MP3 caricati")
        self._append_log(f"Trovati {len(files)} file MP3.")
        self.update_preview()
        self._update_tracks_count()
        self._update_controls_state()

        if mark_dirty:
            self._mark_project_dirty()

    def move_track_up(self) -> None:
        if len(self.ordered_track_names) <= 1:
            return

        selected_file = self._selected_visible_file_name()
        if selected_file is None:
            return

        try:
            index = self.ordered_track_names.index(selected_file)
        except ValueError:
            return

        if index <= 0:
            return

        self.ordered_track_names[index - 1], self.ordered_track_names[index] = (
            self.ordered_track_names[index],
            self.ordered_track_names[index - 1],
        )
        self._refresh_track_list_box(ensure_visible=True)
        self._mark_project_dirty()

    def move_track_down(self) -> None:
        if len(self.ordered_track_names) <= 1:
            return

        selected_file = self._selected_visible_file_name()
        if selected_file is None:
            return

        try:
            index = self.ordered_track_names.index(selected_file)
        except ValueError:
            return

        last_index = len(self.ordered_track_names) - 1

        if index >= last_index:
            return

        self.ordered_track_names[index], self.ordered_track_names[index + 1] = (
            self.ordered_track_names[index + 1],
            self.ordered_track_names[index],
        )
        self._refresh_track_list_box(ensure_visible=True)
        self._mark_project_dirty()

    def _renumber_track_list(self, selected_index: int | None = None) -> None:
        self._sync_clip_info(self.ordered_track_names)
        self._refresh_track_list_box(
            selected_index=selected_index,
            ensure_visible=selected_index is not None,
        )
        self._mark_project_dirty()

    def delete_selected_track(self, _event=None) -> None:
        selected_file = self._selected_visible_file_name()
        if selected_file is None:
            return

        try:
            index = self.ordered_track_names.index(selected_file)
        except ValueError:
            return

        file_name = self.ordered_track_names[index]
        new_index = None
        if len(self.ordered_track_names) > 1:
            new_index = min(index, len(self.ordered_track_names) - 2)

        self.track_clip_info.pop(file_name, None)
        self.ordered_track_names.pop(index)
        self._sync_clip_info(self.ordered_track_names)
        self._refresh_track_list_box()
        self.update_preview()

        if self.track_list.size() > 0:
            visible_index = min(index, self.track_list.size() - 1)
            self.track_list.selection_set(visible_index)
            self.track_list.activate(visible_index)

        self.status_label.configure(
            text=f"{self.track_count} MP3 inclusi nel mix"
        )
        self.status_bar_label.configure(
            text=f"{self.track_count} MP3 inclusi"
        )
        self._append_log(
            f"Rimosso dal mix: {file_name}"
        )
        self._mark_project_dirty()
        self._update_controls_state()

    def set_test_clip(self) -> None:
        selected_file = self._selected_visible_file_name()
        if selected_file is None:
            messagebox.showerror(
                "Errore",
                "Seleziona un solo brano per impostare la clip personalizzata."
            )
            return

        file_name = selected_file
        mp3_path = Path(self.input_folder) / file_name
        clip_info = self.track_clip_info.get(file_name, ClipInfo())

        def on_clip_confirmed(updated_clip: ClipInfo) -> None:
            self.track_clip_info[file_name] = updated_clip
            self._refresh_track_list_box()
            start_seconds = updated_clip.clip_start_ms / 1000.0
            end_seconds = updated_clip.clip_end_ms / 1000.0
            self._append_log(
                f"Clip personalizzata impostata per {file_name}: "
                f"{self._format_time_with_ms(start_seconds)} → "
                f"{self._format_time_with_ms(end_seconds)}"
            )
            self._mark_project_dirty()

        ClipEditorDialog(
            parent=self,
            mp3_path=mp3_path,
            clip_info=clip_info,
            callback=on_clip_confirmed
        )

    def clear_custom_clip(self) -> None:
        selected_file = self._selected_visible_file_name()
        if selected_file is None:
            messagebox.showerror(
                "Errore",
                "Seleziona un solo brano per rimuovere la clip personalizzata."
            )
            return

        file_name = selected_file

        self.track_clip_info[file_name] = ClipInfo()
        self._refresh_track_list_box()
        self._append_log(
            f"Clip personalizzata rimossa per {file_name}."
        )
        self._mark_project_dirty()

    def sort_tracks_alphabetically(self) -> None:
        names = sorted(
            self.get_ordered_track_names(),
            key=str.lower
        )
        self._replace_track_order(names)
        self._append_log("Elenco ordinato alfabeticamente.")
        self._mark_project_dirty()

    def shuffle_track_list(self) -> None:
        import random

        names = self.get_ordered_track_names()
        random.shuffle(names)
        self._replace_track_order(names)
        self._append_log("Elenco mescolato casualmente.")
        self._mark_project_dirty()

    def _replace_track_order(self, names: list[str]) -> None:
        self.ordered_track_names = list(names)
        self._sync_clip_info(self.ordered_track_names)
        self._refresh_track_list_box()
        self._mark_project_dirty()

    def _drag_start(self, event) -> None:
        if self.track_filter_var.get().strip() != FILTER_ALL:
            self._drag_source_index = None
            return

        index = self.track_list.nearest(event.y)

        if 0 <= index < self.track_list.size():
            self._drag_source_index = index
            self.track_list.selection_clear(0, END)
            self.track_list.selection_set(index)

    def _drag_motion(self, event) -> None:
        if self._drag_source_index is None:
            return

        if self.track_filter_var.get().strip() != FILTER_ALL:
            return

        target_index = self.track_list.nearest(event.y)

        if target_index == self._drag_source_index:
            return

        if not (0 <= target_index < self.track_list.size()):
            return

        if self._drag_source_index >= len(self._display_track_names) or target_index >= len(self._display_track_names):
            return

        moved_name = self._display_track_names[self._drag_source_index]
        target_name = self._display_track_names[target_index]

        try:
            old_index = self.ordered_track_names.index(moved_name)
            new_index = self.ordered_track_names.index(target_name)
        except ValueError:
            return

        moved = self.ordered_track_names.pop(old_index)
        self.ordered_track_names.insert(new_index, moved)

        self._drag_source_index = target_index
        self._refresh_track_list_box(selected_index=target_index, ensure_visible=True)

    def _drag_end(self, _event) -> None:
        if self._drag_source_index is not None:
            self._append_log("Ordine brani modificato manualmente.")
            self._mark_project_dirty()

        self._drag_source_index = None
        self._update_controls_state()

    def _on_track_selection_change(self, _event=None) -> None:
        if self._updating_track_list:
            return

        selected_file = self._selected_visible_file_name()
        if selected_file is not None and hasattr(self, "track_list_tooltip"):
            if self._is_track_unrecoverable(selected_file):
                self.track_list_tooltip.text = (
                    "File classificato come non recuperabile dalla diagnostica MP3. "
                    "Puoi mantenerlo visibile ma escluderlo automaticamente dal mix."
                )
            else:
                self.track_list_tooltip.text = "Elenco e ordine dei brani che verranno inseriti nel mix."

        self._update_controls_state()
        self._update_selected_track_mix_details()

    def _update_selected_track_mix_details(self) -> None:
        if not hasattr(self, "track_list"):
            return

        selection = self.track_list.curselection()
        if not selection:
            return

        index = int(selection[0])
        if index < 0 or index >= len(self._display_track_names):
            return

        file_name = self._display_track_names[index]
        mix_start_ms = self._track_mix_times_ms.get(file_name)
        integrity_text = self._integrity_display_for_track(file_name)

        if mix_start_ms is None:
            if self._is_track_unrecoverable(file_name):
                self.status_bar_label.configure(
                    text=f"Integrita: {integrity_text} | File non recuperabile (diagnostica MP3)."
                )
            else:
                self.status_bar_label.configure(text=f"Integrita: {integrity_text}")
            return

        precise = self._format_hhmmss_mmm_from_ms(mix_start_ms)
        self.status_bar_label.configure(text=f"Posizione nel mix: {precise} | Integrita: {integrity_text}")

    def get_ordered_track_names(self) -> list[str]:
        return list(self.ordered_track_names)

    def _effective_mix_track_names(self) -> list[str]:
        names = self.get_ordered_track_names()
        if not bool(self.exclude_unrecoverable_var.get()):
            return names

        return [name for name in names if not self._is_track_unrecoverable(name)]

    def update_preview(self) -> None:
        clip_seconds = int(round(self.clip_slider.get()))
        crossfade_seconds = int(round(self.crossfade_slider.get()))
        bitrate_text = self.bitrate_combo.get() or "320k"
        cut_mode = CUT_MODE_LABELS.get(
            self.cut_mode_combo.get(),
            "inizio"
        )

        bitrate_kbps = self._bitrate_to_number(bitrate_text)

        if cut_mode == "intero":
            self.preview_tracks_label.configure(
                text=str(self.track_count)
            )
            self.preview_duration_label.configure(
                text="Durata reale"
            )
            self.preview_size_label.configure(
                text="Calcolata al mix"
            )
            self.preview_bitrate_label.configure(
                text=f"{bitrate_kbps} kbps"
            )
            return

        if self.track_count <= 0:
            total_seconds = 0
        else:
            total_seconds = (
                self.track_count * clip_seconds
                - max(0, self.track_count - 1) * crossfade_seconds
            )
            total_seconds = max(0, total_seconds)

        estimated_megabytes = (
            total_seconds * bitrate_kbps * 1000 / 8 / 1024 / 1024
        )

        self.preview_tracks_label.configure(text=str(self.track_count))
        self.preview_duration_label.configure(
            text=self._format_duration(total_seconds)
        )
        self.preview_size_label.configure(
            text=f"{estimated_megabytes:.1f} MB"
        )
        self.preview_bitrate_label.configure(
            text=f"{bitrate_kbps} kbps"
        )

    @staticmethod
    def _bitrate_to_number(value: str) -> int:
        try:
            return int(value.lower().replace("k", "").strip())
        except ValueError:
            return 320

    @staticmethod
    def _format_duration(total_seconds: float | int) -> str:
        total = max(0, int(total_seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def start_mix(self) -> None:
        if self.worker.is_running:
            messagebox.showwarning(
                "Operazione in corso",
                "È già in corso la creazione di un mix."
            )
            return

        input_folder = self.input_entry.get().strip()
        output_folder = self.output_entry.get().strip()
        output_name = self.output_name_entry.get().strip()

        if not input_folder or not Path(input_folder).is_dir():
            messagebox.showerror("Errore", "Seleziona una cartella MP3 valida.")
            return

        if not output_folder:
            output_folder = input_folder
            self._replace_entry(self.output_entry, output_folder)

        if not output_name:
            output_name = "MixFinale"
            self._replace_entry(self.output_name_entry, output_name)

        self.input_folder = input_folder
        self.output_folder = output_folder
        self.save_settings()

        self.start_time = time.monotonic()
        self.last_progress_percent = 0

        self.create_button.configure(
            state="disabled",
            text="CREAZIONE IN CORSO..."
        )
        self.cancel_button.configure(state="normal")
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.counter_label.configure(text=f"0 / {self.track_count}")
        self.elapsed_label.configure(text="00:00:00")
        self.remaining_label.configure(text="--:--:--")
        self.status_label.configure(text="Avvio elaborazione...")
        self.status_bar_label.configure(text="Creazione mix in corso...")
        self._append_log("Avvio creazione mix.")

        reuse_enabled = bool(self.reuse_previous_clips_var.get()) and bool(self._reusable_previous_clips)
        if reuse_enabled:
            self._append_log("Utilizza clip generate in precedenza: attivo.")
        else:
            self._append_log("Utilizza clip generate in precedenza: disattivo.")

        effective_track_names = self._effective_mix_track_names()
        excluded_count = len(self.ordered_track_names) - len(effective_track_names)
        if excluded_count > 0:
            self._append_log(
                "Esclusione non recuperabili attiva: "
                f"{excluded_count} brani esclusi dal mix."
            )

        if not effective_track_names:
            self._restore_after_work()
            messagebox.showerror(
                "Errore",
                "Nessun brano disponibile per il mix dopo l'esclusione dei file non recuperabili."
            )
            return

        try:
            self.worker.start(
                input_folder=input_folder,
                output_folder=output_folder,
                output_name=output_name,
                clip_seconds=int(round(self.clip_slider.get())),
                crossfade_seconds=int(round(self.crossfade_slider.get())),
                fade_in_seconds=int(round(self.fade_in_slider.get())),
                fade_out_seconds=int(round(self.fade_out_slider.get())),
                bitrate=self.bitrate_combo.get(),
                cut_mode=CUT_MODE_LABELS.get(
                    self.cut_mode_combo.get(),
                    "inizio"
                ),
                random_order=bool(self.random_checkbox.get()),
                normalize_audio=bool(self.normalize_checkbox.get()),
                ordered_file_names=effective_track_names,
                custom_clips=self._copy_custom_clips(),
                previous_resolved_clips=(
                    self._copy_reusable_previous_clips()
                    if reuse_enabled
                    else None
                ),
            )

            self._start_timer()

        except Exception as error:
            self._restore_after_work()
            messagebox.showerror("Errore", str(error))

    def _copy_custom_clips(self) -> dict[str, ClipInfo]:
        return {
            file_name: clip_info.copy()
            for file_name, clip_info in self.track_clip_info.items()
        }

    def _copy_reusable_previous_clips(self) -> dict[str, dict[str, Any]]:
        return {
            file_name: dict(item)
            for file_name, item in self._reusable_previous_clips.items()
        }

    def _resolve_song_extraction_folder(self) -> Path:
        if self.current_project_path:
            project_path = Path(self.current_project_path)
            return project_path.parent / f"{project_path.stem}_SONG_ESTRATTE"

        output_folder = self.output_entry.get().strip() if hasattr(self, "output_entry") else ""
        base_folder = Path(output_folder) if output_folder else Path(self.input_entry.get().strip())
        base_name = self.project_name.strip() if self.project_name.strip() else (self.output_name_entry.get().strip() or "MixFinale")
        return base_folder / f"{base_name}_SONG_ESTRATTE"

    def _build_song_rows_from_current_order(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        base_folder = Path(self.input_entry.get().strip()) if hasattr(self, "input_entry") else Path()

        for index, file_name in enumerate(self.ordered_track_names, start=1):
            source_path = str((base_folder / file_name).resolve()) if str(base_folder) else file_name
            mix_start_ms = self._track_mix_times_ms.get(file_name)
            mix_time = self._format_hhmmss_from_ms(mix_start_ms) if mix_start_ms is not None else "—"
            rows.append(
                {
                    "number": f"{index:03d}",
                    "title": Path(file_name).name,
                    "path": source_path,
                    "mix_time": mix_time,
                }
            )

        return rows

    def _build_song_rows_from_temporal_tracks(self, tracks_data: list[dict[str, Any]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for index, item in enumerate(tracks_data, start=1):
            file_name = str(item.get("file_name", f"track_{index:03d}.mp3"))
            source_path = str(item.get("source_path", ""))
            mix_start_ms = self._safe_int(item.get("mix_start_ms"))
            mix_time = self._format_hhmmss_from_ms(mix_start_ms) if mix_start_ms is not None else "—"
            rows.append(
                {
                    "number": f"{index:03d}",
                    "title": Path(file_name).name,
                    "path": source_path,
                    "mix_time": mix_time,
                }
            )
        return rows

    def _write_song_list_files(self, target_folder: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
        target_folder.mkdir(parents=True, exist_ok=True)

        txt_path = target_folder / "Elenco_Song.txt"
        csv_path = target_folder / "Elenco_Song.csv"

        missing_paths: list[str] = []
        txt_lines: list[str] = []
        for row in rows:
            txt_lines.append(f"{row['number']} - {row['title']}")
            raw_path = row.get("path", "").strip()
            if raw_path:
                try:
                    if not Path(raw_path).is_file():
                        missing_paths.append(raw_path)
                except OSError:
                    missing_paths.append(raw_path)

        txt_path.write_text("\n".join(txt_lines), encoding="utf-8")

        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Numero", "Titolo", "Percorso file", "Tempo nel mix"])
            for row in rows:
                writer.writerow([
                    row.get("number", ""),
                    row.get("title", ""),
                    row.get("path", ""),
                    row.get("mix_time", "—") or "—",
                ])

        return {
            "txt_path": str(txt_path),
            "csv_path": str(csv_path),
            "missing_paths": missing_paths,
        }

    def _append_extract_log(self, message: str) -> None:
        if self._extract_progress_log is None:
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        self._extract_progress_log.configure(state="normal")
        self._extract_progress_log.insert("end", f"{timestamp}  {message}\n")
        self._extract_progress_log.see("end")
        self._extract_progress_log.configure(state="disabled")

    def _show_extraction_progress_dialog(self, total: int, target_folder: Path) -> None:
        if self._extract_progress_dialog is not None and self._extract_progress_dialog.winfo_exists():
            try:
                self._extract_progress_dialog.destroy()
            except Exception:
                pass

        dialog = ctk.CTkToplevel(self)
        dialog.title("Estrazione Song")
        dialog.geometry("760x420")
        dialog.minsize(700, 360)
        dialog.transient(self)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", self.cancel_song_extraction)

        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(3, weight=1)

        self._extract_progress_dialog = dialog

        self._extract_progress_label = ctk.CTkLabel(
            dialog,
            text=f"Estrazione in corso: 0 / {total}",
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._extract_progress_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))

        self._extract_progress_file_label = ctk.CTkLabel(
            dialog,
            text="File corrente: -",
            anchor="w",
            wraplength=720,
            justify="left",
        )
        self._extract_progress_file_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

        self._extract_progress_bar = ctk.CTkProgressBar(dialog)
        self._extract_progress_bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._extract_progress_bar.set(0)

        self._extract_progress_log = ctk.CTkTextbox(dialog, height=220)
        self._extract_progress_log.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self._extract_progress_log.configure(state="disabled")

        self._extract_progress_cancel_button = ctk.CTkButton(
            dialog,
            text="Annulla",
            width=120,
            command=self.cancel_song_extraction,
        )
        self._extract_progress_cancel_button.grid(row=4, column=0, sticky="e", padx=14, pady=(0, 12))

        self._append_extract_log(f"Cartella output: {target_folder}")

    def _close_extraction_progress_dialog(self) -> None:
        if self._extract_progress_dialog is None:
            return

        try:
            if self._extract_progress_dialog.winfo_exists():
                self._extract_progress_dialog.grab_release()
                self._extract_progress_dialog.destroy()
        except Exception:
            pass
        finally:
            self._extract_progress_dialog = None
            self._extract_progress_label = None
            self._extract_progress_bar = None
            self._extract_progress_file_label = None
            self._extract_progress_log = None
            self._extract_progress_cancel_button = None

    def extract_songs(self) -> None:
        if self.worker.is_running:
            messagebox.showwarning("Operazione in corso", "Attendi il completamento della creazione del mix.")
            return

        if self.extract_worker.is_running:
            messagebox.showwarning("Operazione in corso", "È già in corso un'estrazione song.")
            return

        has_temporal_data = self._has_valid_last_mix_temporal_data()
        if has_temporal_data:
            messagebox.showinfo(
                "Estrai Song",
                "Sono disponibili i dati temporali dell'ultimo mix. "
                "Verranno estratte le clip audio effettivamente utilizzate."
            )
        else:
            messagebox.showinfo(
                "Estrai Song",
                "I dati temporali dell'ultimo mix non sono disponibili. "
                "Verrà esportato esclusivamente l'elenco ordinato delle Song."
            )

        tracks_data = self._get_ordered_new_mix_tracks() if has_temporal_data else []

        target_folder = self._resolve_song_extraction_folder()
        try:
            target_folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Errore", f"Impossibile creare la cartella di output:\n{error}")
            return

        if not has_temporal_data:
            rows = self._build_song_rows_from_current_order()
            try:
                list_result = self._write_song_list_files(target_folder, rows)
            except OSError as error:
                messagebox.showerror("Errore", f"Impossibile esportare l'elenco Song:\n{error}")
                return

            missing_paths = list_result.get("missing_paths", [])
            if not isinstance(missing_paths, list):
                missing_paths = []

            self._append_log(
                "Elenco Song esportato senza dati temporali: "
                f"{len(rows)} elementi in {target_folder}."
            )

            summary_lines = [
                "Esportazione elenco Song completata.",
                "",
                f"Cartella output: {target_folder}",
                f"File TXT: {list_result.get('txt_path', '')}",
                f"File CSV: {list_result.get('csv_path', '')}",
                f"Song in elenco: {len(rows)}",
                f"Percorsi mancanti: {len(missing_paths)}",
            ]
            if missing_paths:
                summary_lines.append("")
                summary_lines.append("Alcuni file sorgente risultano mancanti:")
                summary_lines.extend(f"- {path}" for path in missing_paths[:12])
                if len(missing_paths) > 12:
                    summary_lines.append(f"- ... altri {len(missing_paths) - 12}")

            messagebox.showinfo("Estrai Song", "\n".join(summary_lines))
            return

        if not tracks_data:
            messagebox.showwarning(
                "Estrai Song",
                "I dati temporali risultano disponibili ma non utilizzabili. "
                "Verrà esportato solo l'elenco Song."
            )
            rows = self._build_song_rows_from_current_order()
            try:
                self._write_song_list_files(target_folder, rows)
            except OSError as error:
                messagebox.showerror("Errore", f"Impossibile esportare l'elenco Song:\n{error}")
            return

        self._extract_tracks_snapshot = list(tracks_data)
        self._extract_has_temporal_mode = True

        self._show_extraction_progress_dialog(total=len(tracks_data), target_folder=target_folder)
        self.status_label.configure(text="Estrazione song in corso...")
        self.status_bar_label.configure(text="Estrazione song in corso...")
        self._append_log("Avvio estrazione Song dall'ultimo mix generato.")
        self._update_controls_state()

        try:
            self.extract_worker.start(
                tracks_data=tracks_data,
                output_folder=str(target_folder),
                bitrate=self.bitrate_combo.get() or "320k",
            )
        except Exception as error:
            self._close_extraction_progress_dialog()
            self._update_controls_state()
            messagebox.showerror("Errore", f"Impossibile avviare l'estrazione:\n{error}")

    def cancel_song_extraction(self) -> None:
        if not self.extract_worker.is_running:
            self._close_extraction_progress_dialog()
            return

        if self._extract_progress_cancel_button is not None:
            self._extract_progress_cancel_button.configure(state="disabled")
        self._append_extract_log("Richiesto annullamento estrazione.")
        self.extract_worker.cancel()

    def _extract_worker_progress(self, current: int, total: int, message: str) -> None:
        self.after(0, self._handle_extract_worker_progress, current, total, message)

    def _handle_extract_worker_progress(self, current: int, total: int, message: str) -> None:
        percent = 0 if total <= 0 else max(0, min(100, int((current / total) * 100)))

        if self._extract_progress_label is not None:
            self._extract_progress_label.configure(text=f"Estrazione in corso: {current} / {total}")
        if self._extract_progress_bar is not None:
            self._extract_progress_bar.set(percent / 100.0)
        if self._extract_progress_file_label is not None:
            self._extract_progress_file_label.configure(text=f"File corrente: {message}")

        self.status_label.configure(text=message)
        self._append_extract_log(message)

    def _extract_worker_completed(self, summary: dict[str, Any]) -> None:
        self.after(0, self._handle_extract_worker_completed, summary)

    def _handle_extract_worker_completed(self, summary: dict[str, Any]) -> None:
        output_folder = str(summary.get("output_folder", ""))
        total = int(summary.get("total", 0))
        extracted = int(summary.get("extracted", 0))
        errors = summary.get("errors", [])
        if not isinstance(errors, list):
            errors = []

        self.status_label.configure(text="Estrazione song completata")
        self.status_bar_label.configure(text="Estrazione song completata")
        self._append_log(f"Estrazione Song completata: {extracted}/{total} file estratti.")

        list_result: dict[str, Any] = {}
        try:
            rows = self._build_song_rows_from_temporal_tracks(self._extract_tracks_snapshot)
            list_result = self._write_song_list_files(Path(output_folder), rows)
        except OSError as error:
            errors.append(f"Impossibile salvare Elenco Song: {error}")

        if self._extract_progress_bar is not None:
            self._extract_progress_bar.set(1)
        if self._extract_progress_label is not None:
            self._extract_progress_label.configure(text=f"Completato: {extracted} / {total}")

        for error in errors:
            self._append_extract_log(f"ERRORE: {error}")

        if self._extract_progress_cancel_button is not None:
            self._extract_progress_cancel_button.configure(text="Chiudi", state="normal", command=self._close_extraction_progress_dialog)

        self._update_controls_state()

        summary_lines = [
            "Estrazione Song completata.",
            "",
            f"Cartella output: {output_folder}",
            f"File estratti: {extracted} / {total}",
            f"Elenco TXT: {list_result.get('txt_path', '')}",
            f"Elenco CSV: {list_result.get('csv_path', '')}",
            f"Percorsi mancanti: {len(list_result.get('missing_paths', []))}",
            f"Errori: {len(errors)}",
        ]
        if errors:
            summary_lines.append("")
            summary_lines.append("Sono presenti errori nel dettaglio avanzamento.")

        messagebox.showinfo("Estrai Song", "\n".join(summary_lines))
        self._extract_tracks_snapshot = []
        self._extract_has_temporal_mode = False

    def _extract_worker_error(self, message: str) -> None:
        self.after(0, self._handle_extract_worker_error, message)

    def _handle_extract_worker_error(self, message: str) -> None:
        self.status_label.configure(text="Errore durante l'estrazione song")
        self.status_bar_label.configure(text="Errore estrazione song")
        self._append_log(f"ERRORE ESTRAZIONE SONG: {message}")

        if self._extract_progress_cancel_button is not None:
            self._extract_progress_cancel_button.configure(text="Chiudi", state="normal", command=self._close_extraction_progress_dialog)
        self._append_extract_log(f"ERRORE: {message}")

        self._update_controls_state()
        messagebox.showerror("Errore estrazione song", message)
        self._extract_tracks_snapshot = []
        self._extract_has_temporal_mode = False

    def _extract_worker_cancelled(self, message: str) -> None:
        self.after(0, self._handle_extract_worker_cancelled, message)

    def _handle_extract_worker_cancelled(self, message: str) -> None:
        self.status_label.configure(text="Estrazione song annullata")
        self.status_bar_label.configure(text="Estrazione annullata")
        self._append_log(message)
        self._append_extract_log(message)

        if self._extract_progress_cancel_button is not None:
            self._extract_progress_cancel_button.configure(text="Chiudi", state="normal", command=self._close_extraction_progress_dialog)

        self._update_controls_state()
        self._extract_tracks_snapshot = []
        self._extract_has_temporal_mode = False

    def cancel_mix(self) -> None:
        if not self.worker.is_running:
            return

        confirm = messagebox.askyesno(
            "Annulla creazione",
            "Vuoi interrompere la creazione del mix?"
        )
        if not confirm:
            return

        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="Annullamento in corso...")
        self.status_bar_label.configure(text="Annullamento...")
        self._append_log("Richiesto annullamento.")
        self.worker.cancel()

    def _worker_progress(self, current: int, total: int, message: str) -> None:
        self.after(0, self._handle_worker_progress, current, total, message)

    def _handle_worker_progress(
        self,
        current: int,
        total: int,
        message: str
    ) -> None:
        percentage = 0 if total <= 0 else max(0, min(100, int((current / total) * 100)))
        self.last_progress_percent = percentage

        self.progress.set(percentage / 100)
        self.percent_label.configure(text=f"{percentage}%")
        self.status_label.configure(text=message)

        if "Analisi" in message and total > 0:
            self.counter_label.configure(text=f"{current} / {total}")
        elif "Creazione mix" in message:
            self.counter_label.configure(text=f"{self.track_count} / {self.track_count}")

        self._update_remaining_time()
        self._append_log(message)

    def _start_timer(self) -> None:
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
        self._tick_timer()

    def _tick_timer(self) -> None:
        if self.start_time is None:
            self.timer_job = None
            return

        elapsed = time.monotonic() - self.start_time
        self.elapsed_label.configure(
            text=self._format_duration(elapsed)
        )
        self._update_remaining_time()

        self.timer_job = self.after(
            1000,
            self._tick_timer
        )

    def _update_remaining_time(self) -> None:
        if self.start_time is None or self.last_progress_percent <= 0:
            self.remaining_label.configure(text="--:--:--")
            return

        elapsed = time.monotonic() - self.start_time
        fraction = self.last_progress_percent / 100

        if fraction <= 0:
            self.remaining_label.configure(text="--:--:--")
            return

        estimated_total = elapsed / fraction
        remaining = max(0, estimated_total - elapsed)
        self.remaining_label.configure(text=self._format_duration(remaining))

    def _worker_completed(self, output_path: Path, mix_report: dict[str, Any]) -> None:
        self.after(0, self._handle_worker_completed, output_path, mix_report)

    def _handle_worker_completed(self, output_path: Path, mix_report: dict[str, Any]) -> None:
        elapsed_seconds = 0.0

        if self.start_time is not None:
            elapsed_seconds = time.monotonic() - self.start_time

        self._restore_after_work()
        self.progress.set(1)
        self.percent_label.configure(text="100%")
        self.remaining_label.configure(text="00:00:00")
        self.status_label.configure(text=f"Mix creato: {output_path.name}")
        self.status_bar_label.configure(text="Mix completato")

        try:
            file_size_mb = output_path.stat().st_size / 1024 / 1024
        except OSError:
            file_size_mb = 0.0

        elapsed_text = self._format_duration(elapsed_seconds)

        if isinstance(mix_report, dict):
            report_tracks = mix_report.get("tracks")
            if isinstance(report_tracks, list):
                normalized_tracks: list[dict[str, Any]] = []
                for item in report_tracks:
                    normalized = self._normalize_saved_mix_track(item)
                    if normalized is not None:
                        normalized_tracks.append(normalized)

                if normalized_tracks:
                    self.last_generated_mix_data = {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "cut_mode": CUT_MODE_LABELS.get(self.cut_mode_combo.get(), "inizio"),
                        "tracks": normalized_tracks,
                    }
                    self._refresh_track_mix_times_index()
                    self._refresh_track_list_box()
                    self._refresh_reuse_previous_option(select_if_available=True)
                    self._mark_project_dirty()

            summary = mix_report.get("reuse_summary")
            if isinstance(summary, dict):
                reused = int(summary.get("reused", 0))
                recalculated = int(summary.get("recalculated", 0))
                new = int(summary.get("new", 0))
                self._append_log(
                    "Clip precedenti: "
                    f"riutilizzate={reused}, "
                    f"ricalcolate={recalculated}, "
                    f"nuove={new}."
                )

        self._append_log(
            f"Completato: {output_path} | "
            f"Dimensione: {file_size_mb:.1f} MB | "
            f"Tempo: {elapsed_text}"
        )

        messagebox.showinfo(
            "Mix completato",
            "File creato correttamente.\n\n"
            f"Nome: {output_path.name}\n"
            f"Dimensione: {file_size_mb:.1f} MB\n"
            f"Tempo impiegato: {elapsed_text}\n\n"
            f"Percorso:\n{output_path}"
        )

    def _worker_error(self, message: str) -> None:
        self.after(0, self._handle_worker_error, message)

    def _handle_worker_error(self, message: str) -> None:
        self._restore_after_work()
        self.status_label.configure(text="Errore durante la creazione.")
        self.status_bar_label.configure(text="Errore")
        self._append_log(f"ERRORE: {message}")
        messagebox.showerror("Errore", message)

    def _worker_cancelled(self, message: str) -> None:
        self.after(0, self._handle_worker_cancelled, message)

    def _handle_worker_cancelled(self, message: str) -> None:
        self._restore_after_work()
        self.status_label.configure(text="Creazione annullata.")
        self.status_bar_label.configure(text="Operazione annullata")
        self._append_log(message)
        messagebox.showinfo("Operazione annullata", message)

    def _restore_after_work(self) -> None:
        if self.timer_job is not None:
            self.after_cancel(self.timer_job)
            self.timer_job = None

        self.start_time = None
        self.create_button.configure(text="CREA MIX")
        self.cancel_button.configure(state="disabled")
        self._update_controls_state()

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{timestamp}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def change_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.settings["appearance_mode"] = mode
        self.settings_manager.save(self.settings)

    def save_settings(self) -> None:
        self.settings.update(
            {
                "input_folder": self.input_entry.get().strip(),
                "output_folder": self.output_entry.get().strip(),
                "output_name": self.output_name_entry.get().strip() or "MixFinale",
                "clip_seconds": int(round(self.clip_slider.get())),
                "crossfade_seconds": int(round(self.crossfade_slider.get())),
                "fade_in_seconds": int(round(self.fade_in_slider.get())),
                "fade_out_seconds": int(round(self.fade_out_slider.get())),
                "bitrate": self.bitrate_combo.get(),
                "cut_mode": CUT_MODE_LABELS.get(
                    self.cut_mode_combo.get(),
                    "inizio"
                ),
                "random_order": bool(self.random_checkbox.get()),
                "normalize_audio": bool(self.normalize_checkbox.get()),
                "continue_short_tracks": bool(self.short_checkbox.get()),
                "exclude_unrecoverable_from_mix": bool(self.exclude_unrecoverable_var.get()),
                "diagnostics_verify_winlive": bool(self.diagnostics_verify_winlive_var.get()),
                "diagnostics_winlive_autocorrect": bool(self.diagnostics_winlive_autocorrect_var.get()),
                "diagnostics_placement_mode": str(self.diagnostics_placement_mode_var.get() or "copy").strip().lower(),
                "appearance_mode": self.appearance_combo.get()
            }
        )
        self.settings_manager.save(self.settings)

    def on_close(self) -> None:
        if self.worker.is_running:
            confirm = messagebox.askyesno(
                "Operazione in corso",
                "La creazione del mix è ancora in corso.\n"
                "Vuoi annullarla e chiudere il programma?"
            )
            if not confirm:
                return
            self.worker.cancel()

        if self.extract_worker.is_running:
            confirm = messagebox.askyesno(
                "Operazione in corso",
                "L'estrazione song è ancora in corso.\n"
                "Vuoi annullarla e chiudere il programma?"
            )
            if not confirm:
                return
            self.extract_worker.cancel()

        if self.diagnostics_worker.is_running:
            confirm = messagebox.askyesno(
                "Operazione in corso",
                "La diagnostica MP3 è ancora in corso.\n"
                "Vuoi annullarla e chiudere il programma?"
            )
            if not confirm:
                return
            self.diagnostics_worker.cancel()

        if not self._confirm_save_if_dirty():
            return

        self.save_settings()
        self.destroy()
