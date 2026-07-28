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
import io
import subprocess
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
from utils import AdaptiveTimeEstimator, scan_mp3_files
from worker import MixWorker, SongExtractionWorker, MP3DiagnosticsWorker, MP3RecoveryWorker
from mp3_recovery_batch import MP3BatchOutcome
from mp3_recovery import RecoveryMode
from mp3_diagnostics import (
    STATUS_PERFECT,
    STATUS_REPAIRED,
    STATUS_UNRECOVERABLE,
)


APP_VERSION = "4.3.0-winlive-stable"
APP_BUILD = "2026.07.25.001"
CREATOR_TEXT = "Created by Dino S."
EXTRACT_SONG_TOOLTIP = (
    "Esporta Elenco_Mix.csv con i tempi reali dell'ultimo mix. "
    "Disponibile solo quando sono presenti dati temporali validi."
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
        self.recovery_worker = MP3RecoveryWorker(
            on_progress=self._recovery_worker_progress,
            on_completed=self._recovery_worker_completed,
            on_error=self._recovery_worker_error,
            on_cancelled=self._recovery_worker_cancelled,
            on_log=self._recovery_worker_log,
        )
        self._recovery_dialog: ctk.CTkToplevel | None = None
        self._recovery_problematic_entry = None
        self._recovery_original_entry = None
        self._recovery_output_entry = None
        self._recovery_mode_var = tk.StringVar(value=RecoveryMode.NORMAL.value)
        self._recovery_mode_normal_radio = None
        self._recovery_mode_forced_radio = None
        self._recovery_forced_confirmation_dialog = None
        self._recovery_status_label = None
        self._recovery_counters_label = None
        self._recovery_progress_bar = None
        self._recovery_log_box = None
        self._recovery_start_button = None
        self._recovery_stop_button = None
        self._recovery_close_button = None
        self._recovery_open_results_button = None
        self._recovery_command_bar = None
        self._recovery_monitor_frame = None
        self._recovery_examined_label = None
        self._recovery_completed_label = None
        self._recovery_batch_status_label = None
        self._recovery_current_file_label = None
        self._recovery_phase_label = None
        self._recovery_elapsed_label = None
        self._recovery_current_file_elapsed_label = None
        self._recovery_eta_label = None
        self._recovery_percent_label = None
        self._recovery_path_widgets: list[Any] = []
        self._recovery_live_counters: dict[str, int] = {}
        self._recovery_started_at: float | None = None
        self._recovery_current_file_started_at: float | None = None
        self._recovery_timer_job = None
        self._recovery_total_files = 0
        self._recovery_examined_files = 0
        self._recovery_completed_files = 0
        self._recovery_current_file_name = "-"
        self._recovery_current_phase = "Pronto"
        self._recovery_completed_file_durations: list[float] = []
        self._recovery_session_folder: str | None = None
        self._recovery_allow_session_log_updates = False
        self._recovery_expected_output_root = ""
        self._recovery_min_session_timestamp = ""
        self._diagnostics_integrity_by_file: dict[str, dict[str, Any]] = {}
        self._diagnostics_status_by_file: dict[str, str] = {}
        self._diagnostics_path_index: dict[str, dict[str, Any]] = {}
        self._diagnostics_stable_index: dict[str, dict[str, Any]] = {}
        self._display_track_names: list[str] = []
        self.track_filter_var = tk.StringVar(value=FILTER_ALL)
        self.mix_include_subfolders_var = tk.BooleanVar(
            value=bool(self.settings.get("mix_include_subfolders", True))
        )
        self.diagnostics_include_subfolders_var = tk.BooleanVar(value=False)
        self.diagnostics_verify_mp3_integrity_var = tk.BooleanVar(value=True)
        self.diagnostics_verify_winlive_var = tk.BooleanVar(value=False)
        self.diagnostics_placement_mode_var = tk.StringVar(
            value=str(self.settings.get("diagnostics_placement_mode", "copy"))
        )
        self.exclude_unrecoverable_var = tk.BooleanVar(value=False)
        self.diagnostics_worker_total = 0
        self.diagnostics_worker_start_time: float | None = None
        self.diagnostics_timer_job = None
        self.diagnostics_last_progress = 0
        self.diagnostics_eta_estimator = AdaptiveTimeEstimator(initial_seconds_per_unit=8.0)
        self.diagnostics_window: ctk.CTkToplevel | None = None
        self._diagnostics_session_snapshot: dict[str, Any] | None = None
        self._recovery_session_snapshot: dict[str, Any] | None = None
        self._diagnostics_toggle_guard = False

        self.current_project_path: str | None = None
        self.project_dirty = False
        self.project_source_folder = self.input_folder or ""
        self.project_name = ""
        self._suspend_project_dirty_tracking = False

        self.start_time: float | None = None
        self.timer_job = None
        self.last_progress_percent = 0
        self.mix_eta_estimator = AdaptiveTimeEstimator(initial_seconds_per_unit=8.0)
        self.mix_eta_phase = ""

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

        self.recover_mp3_button = ctk.CTkButton(
            project_bar,
            text="Recupera MP3",
            width=150,
            command=self.open_mp3_recovery_window,
        )
        self.recover_mp3_button.grid(row=0, column=5, padx=(8, 0), sticky="w")
        self._add_tooltip(
            self.recover_mp3_button,
            "Recupera un MP3 problematico usando come base una copia originale integra dello stesso brano."
        )

        self.project_status_label = ctk.CTkLabel(
            project_bar,
            text="Progetto: Nessuno",
            anchor="e"
        )
        self.project_status_label.grid(row=0, column=6, padx=(12, 0), sticky="ew")

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
            refresh_tooltip="Rilegge gli MP3 nella cartella selezionata, rispettando l'opzione sottocartelle.",
            entry_tooltip="Cartella contenente i file MP3 da utilizzare."
        )
        row += 1

        self.mix_subfolders_checkbox = ctk.CTkCheckBox(
            self.left_panel,
            text="Ricerca nelle sottocartelle (CREA MIX)",
            variable=self.mix_include_subfolders_var,
            command=self._on_mix_subfolders_toggled,
        )
        self.mix_subfolders_checkbox.grid(
            row=row, column=0, columnspan=3,
            padx=10, pady=(0, 8), sticky="w"
        )
        self._add_tooltip(
            self.mix_subfolders_checkbox,
            "Se attivo include gli MP3 nelle sottocartelle, escludendo automaticamente le cartelle di output diagnostica.",
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

        self.diagnostics_integrity_checkbox = ctk.CTkCheckBox(
            diag_card,
            text="Verifica integrità MP3",
            variable=self.diagnostics_verify_mp3_integrity_var,
            command=self._on_diagnostics_integrity_toggle,
        )
        self.diagnostics_integrity_checkbox.grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=2)

        self.diagnostics_winlive_group_label = ctk.CTkLabel(
            diag_card,
            text="Verifica WinLive",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.diagnostics_winlive_group_label.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 2))

        self.diagnostics_winlive_checkbox = ctk.CTkCheckBox(
            diag_card,
            text="Verifica TAG WinLive",
            variable=self.diagnostics_verify_winlive_var,
            command=self._on_diagnostics_winlive_toggle,
        )
        self.diagnostics_winlive_checkbox.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        self._add_tooltip(
            self.diagnostics_winlive_checkbox,
            "Controlla la presenza e la correttezza\n"
            "dei TAG WinLive (testo e accordi)\n"
            "e include i risultati nei report.",
        )

        self._sync_diagnostics_winlive_controls_state()

        ctk.CTkLabel(diag_card, text="Cartella di output").grid(row=7, column=0, sticky="w", padx=10, pady=5)
        self.diagnostics_output_entry = ctk.CTkEntry(diag_card)
        self.diagnostics_output_entry.grid(row=7, column=1, sticky="ew", padx=6, pady=5)
        ctk.CTkButton(diag_card, text="Sfoglia", width=84, command=self.select_diagnostics_output).grid(
            row=7, column=2, sticky="e", padx=10, pady=5
        )

        ctk.CTkLabel(diag_card, text="Al termine dell'analisi:").grid(
            row=8,
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
        self.diagnostics_placement_copy_radio.grid(row=9, column=0, columnspan=3, sticky="w", padx=10, pady=2)

        self.diagnostics_placement_move_radio = ctk.CTkRadioButton(
            diag_card,
            text="Sposta i file nelle cartelle di categoria",
            variable=self.diagnostics_placement_mode_var,
            value="move",
            command=self._on_diagnostics_placement_mode_changed,
        )
        self.diagnostics_placement_move_radio.grid(row=10, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))

        buttons_frame = ctk.CTkFrame(diag_card, fg_color="transparent")
        buttons_frame.grid(row=11, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 4))
        buttons_frame.grid_columnconfigure((0, 1), weight=1)

        self.diagnostics_repair_button = ctk.CTkButton(
            buttons_frame,
            text="Analizza e ripara",
            command=self.start_diagnostics_repair,
        )
        self.diagnostics_repair_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._add_tooltip(
            self.diagnostics_repair_button,
            "Analizza i file selezionati, applica le correzioni disponibili quando necessarie e genera un'unica cartella di esito in base ai controlli attivati.",
        )

        self.diagnostics_stop_button = ctk.CTkButton(
            buttons_frame,
            text="Interrompi",
            state="disabled",
            command=self.stop_diagnostics,
        )
        self.diagnostics_stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.diagnostics_progress = ctk.CTkProgressBar(diag_card)
        self.diagnostics_progress.grid(row=12, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 4))
        self.diagnostics_progress.set(0)

        self.diagnostics_status_label = ctk.CTkLabel(diag_card, text="Pronto", anchor="w")
        self.diagnostics_status_label.grid(row=13, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 2))

        counters_frame = ctk.CTkFrame(diag_card, fg_color="transparent")
        counters_frame.grid(row=14, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        counters_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.diagnostics_count_label = ctk.CTkLabel(counters_frame, text="File analizzati: 0 / 0", anchor="w")
        self.diagnostics_count_label.grid(row=0, column=0, sticky="w")
        self.diagnostics_elapsed_label = ctk.CTkLabel(counters_frame, text="Tempo: 00:00:00", anchor="w")
        self.diagnostics_elapsed_label.grid(row=0, column=1, sticky="w")
        self.diagnostics_eta_label = ctk.CTkLabel(counters_frame, text="Tempo stimato restante: --", anchor="w")
        self.diagnostics_eta_label.grid(row=0, column=2, sticky="w")

        self.diagnostics_log_box = ctk.CTkTextbox(diag_card, height=130)
        self.diagnostics_log_box.grid(row=15, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 10))
        self.diagnostics_log_box.configure(state="disabled")

    def _on_diagnostics_placement_mode_changed(self) -> None:
        self.save_settings()

    def _enforce_at_least_one_diagnostic_check(self, changed: str) -> bool:
        if self._diagnostics_toggle_guard:
            return True

        verify_integrity = bool(self.diagnostics_verify_mp3_integrity_var.get())
        verify_winlive = bool(self.diagnostics_verify_winlive_var.get())
        if verify_integrity or verify_winlive:
            return True

        self._diagnostics_toggle_guard = True
        try:
            if changed == "integrity":
                self.diagnostics_verify_mp3_integrity_var.set(True)
            else:
                self.diagnostics_verify_winlive_var.set(True)
        finally:
            self._diagnostics_toggle_guard = False

        messagebox.showinfo(
            "Diagnostica MP3",
            "Almeno un controllo diagnostico deve rimanere attivo.",
            parent=self._diagnostics_dialog_parent(),
        )
        return False

    def _sync_diagnostics_winlive_controls_state(self) -> None:
        main_enabled = bool(self.diagnostics_verify_winlive_var.get())
        self._update_controls_state()

    def _on_diagnostics_integrity_toggle(self) -> None:
        if not self._enforce_at_least_one_diagnostic_check("integrity"):
            self._update_controls_state()
            self.save_settings()
            return
        self._update_controls_state()
        self.save_settings()

    def _on_diagnostics_winlive_toggle(self) -> None:
        if not self._enforce_at_least_one_diagnostic_check("winlive"):
            self._sync_diagnostics_winlive_controls_state()
            self.save_settings()
            return
        self._sync_diagnostics_winlive_controls_state()
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
            timing_frame, 0, 2, "Tempo stimato restante", "--:--:--"
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

            self.mix_include_subfolders_var.set(bool(self.settings.get("mix_include_subfolders", True)))
            if self.mix_include_subfolders_var.get():
                self.mix_subfolders_checkbox.select()
            else:
                self.mix_subfolders_checkbox.deselect()

            verify_mp3 = True
            verify_winlive = False

            self.diagnostics_verify_mp3_integrity_var.set(verify_mp3)
            self.diagnostics_verify_winlive_var.set(verify_winlive)
            self._sync_diagnostics_winlive_controls_state()

            placement_mode = str(self.settings.get("diagnostics_placement_mode", "copy")).strip().lower()
            if placement_mode not in ("copy", "move"):
                placement_mode = "copy"
            self.diagnostics_placement_mode_var.set(placement_mode)

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
            has_temporal_data = self._has_valid_last_mix_temporal_data()
            self.extract_song_button.configure(state="normal" if has_temporal_data else "disabled")
            if self._extract_song_tooltip is not None:
                if has_temporal_data:
                    self._extract_song_tooltip.text = EXTRACT_SONG_TOOLTIP
                else:
                    self._extract_song_tooltip.text = "Non sono disponibili i dati temporali dell'ultimo mix."

        diagnostics_enabled = self._diagnostics_actions_enabled()
        self._safe_widget_configure(
            getattr(self, "diagnostics_repair_button", None),
            state="normal" if diagnostics_enabled and not is_diag_running else "disabled",
        )
        self._safe_widget_configure(
            getattr(self, "diagnostics_stop_button", None),
            state="normal" if is_diag_running else "disabled",
        )
        self._safe_widget_configure(
            getattr(self, "diagnostics_placement_copy_radio", None),
            state="disabled" if is_diag_running else "normal",
        )
        self._safe_widget_configure(
            getattr(self, "diagnostics_placement_move_radio", None),
            state="disabled" if is_diag_running else "normal",
        )
        self._safe_widget_configure(
            getattr(self, "diagnostics_winlive_checkbox", None),
            state="disabled" if is_diag_running else "normal",
        )

    @staticmethod
    def _safe_widget_configure(widget: Any, **kwargs: Any) -> None:
        if widget is None:
            return
        try:
            if hasattr(widget, "winfo_exists") and not bool(widget.winfo_exists()):
                return
            widget.configure(**kwargs)
        except Exception:
            return

    def _diagnostics_actions_enabled(self) -> bool:
        verify_mp3 = bool(self.diagnostics_verify_mp3_integrity_var.get())
        verify_winlive = bool(self.diagnostics_verify_winlive_var.get())
        return verify_mp3 or verify_winlive

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
            "mix_include_subfolders": bool(self.mix_include_subfolders_var.get()),
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
    def _safe_bool_setting(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "yes", "on"):
                return True
            if normalized in ("0", "false", "no", "off", ""):
                return False
            return default
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

            self.mix_include_subfolders_var.set(self._safe_bool_setting(settings.get("mix_include_subfolders"), True))
            if self.mix_include_subfolders_var.get():
                self.mix_subfolders_checkbox.select()
            else:
                self.mix_subfolders_checkbox.deselect()

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

            self.mix_include_subfolders_var.set(bool(self.settings.get("mix_include_subfolders", True)))
            if self.mix_include_subfolders_var.get():
                self.mix_subfolders_checkbox.select()
            else:
                self.mix_subfolders_checkbox.deselect()

            self.diagnostics_verify_mp3_integrity_var.set(True)
            self.diagnostics_verify_winlive_var.set(False)
            self._sync_diagnostics_winlive_controls_state()

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

    @staticmethod
    def _normalize_relative_mp3_path(value: Any) -> str | None:
        if value is None:
            return None

        raw = str(value).strip().replace("\\", "/")
        if not raw:
            return None

        candidate = Path(raw)
        if candidate.is_absolute():
            return None

        normalized_parts: list[str] = []
        for part in raw.split("/"):
            item = part.strip()
            if item in ("", "."):
                continue
            if item == "..":
                return None
            normalized_parts.append(item)

        if not normalized_parts:
            return None

        normalized = Path(*normalized_parts).as_posix()
        if Path(normalized).suffix.lower() != ".mp3":
            return None
        return normalized

    @staticmethod
    def _path_compare_key(relative_path: str) -> str:
        return relative_path.casefold() if os.name == "nt" else relative_path

    def _collect_saved_project_track_paths(self, loaded_project: dict[str, Any]) -> list[str]:
        tracks = loaded_project.get("tracks")
        if not isinstance(tracks, list):
            return []

        ordered_paths: list[str] = []
        seen: set[str] = set()
        for item in tracks:
            if not isinstance(item, dict):
                continue
            relative = self._normalize_relative_mp3_path(item.get("relative_path"))
            if relative is None:
                relative = self._normalize_relative_mp3_path(item.get("file_name"))
            if relative is None:
                continue
            key = self._path_compare_key(relative)
            if key in seen:
                continue
            seen.add(key)
            ordered_paths.append(relative)

        return ordered_paths

    def _collect_saved_project_track_clip_info(self, loaded_project: dict[str, Any]) -> dict[str, ClipInfo]:
        tracks = loaded_project.get("tracks")
        if not isinstance(tracks, list):
            return {}

        clip_by_key: dict[str, ClipInfo] = {}
        for item in tracks:
            if not isinstance(item, dict):
                continue
            relative = self._normalize_relative_mp3_path(item.get("relative_path"))
            if relative is None:
                relative = self._normalize_relative_mp3_path(item.get("file_name"))
            if relative is None:
                continue
            key = self._path_compare_key(relative)
            if key in clip_by_key:
                continue
            clip_by_key[key] = ClipInfo.from_dict(item.get("clip_info"))
        return clip_by_key

    def _compare_project_tracks_with_folder(
        self,
        *,
        loaded_project: dict[str, Any],
        source_folder: str,
        include_subfolders: bool,
    ) -> tuple[list[str], list[str], list[str]]:
        saved_paths = self._collect_saved_project_track_paths(loaded_project)
        scanned_paths = self.scan_mp3_files(source_folder, include_subfolders=include_subfolders)

        saved_map = {
            self._path_compare_key(path): path
            for path in saved_paths
        }
        scanned_map = {
            self._path_compare_key(path): path
            for path in scanned_paths
        }

        added_keys = [key for key in scanned_map.keys() if key not in saved_map]
        missing_keys = [key for key in saved_map.keys() if key not in scanned_map]

        added = [scanned_map[key] for key in added_keys]
        missing = [saved_map[key] for key in missing_keys]
        return added, missing, scanned_paths

    @staticmethod
    def _format_project_variation_lines(title: str, items: list[str], max_items: int = 10) -> list[str]:
        lines = [title]
        if not items:
            lines.append("- Nessuno")
            return lines

        head = items[:max_items]
        lines.extend(f"- {name}" for name in head)
        extra = len(items) - len(head)
        if extra > 0:
            lines.append(f"... e altri {extra} file")
        return lines

    def _ask_project_source_variation_action(
        self,
        *,
        added_files: list[str],
        missing_files: list[str],
    ) -> str:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Progetto modificato rispetto alla cartella sorgente")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        container = ctk.CTkFrame(dialog)
        container.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            container,
            text="La cartella associata al progetto contiene variazioni rispetto all'elenco salvato.",
            justify="left",
            anchor="w",
            wraplength=680,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", padx=2, pady=(0, 10))

        ctk.CTkLabel(
            container,
            text=(
                f"File aggiunti: {len(added_files)}\n"
                f"File mancanti: {len(missing_files)}\n\n"
                "L'elenco dei brani del progetto potrebbe quindi risultare diverso da quello originale."
            ),
            justify="left",
            anchor="w",
            wraplength=680,
        ).pack(fill="x", padx=2, pady=(0, 10))

        details = []
        details.extend(self._format_project_variation_lines("File aggiunti:", added_files))
        details.append("")
        details.extend(self._format_project_variation_lines("File mancanti:", missing_files))

        details_box = ctk.CTkTextbox(container, height=220, width=720)
        details_box.pack(fill="both", expand=True)
        details_box.insert("1.0", "\n".join(details))
        details_box.configure(state="disabled")

        result = {"value": "cancel"}

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(fill="x", pady=(12, 0))
        buttons.grid_columnconfigure((0, 1, 2), weight=1)

        def _set_and_close(value: str) -> None:
            result["value"] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        ctk.CTkButton(
            buttons,
            text="Mantieni elenco del progetto",
            command=lambda: _set_and_close("keep"),
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        ctk.CTkButton(
            buttons,
            text="Aggiorna elenco dalla cartella",
            command=lambda: _set_and_close("refresh"),
        ).grid(row=0, column=1, padx=6, sticky="ew")

        ctk.CTkButton(
            buttons,
            text="Annulla apertura",
            fg_color="#8f3a3a",
            hover_color="#7c3232",
            command=lambda: _set_and_close("cancel"),
        ).grid(row=0, column=2, padx=(6, 0), sticky="ew")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _set_and_close("cancel"))
        self.wait_window(dialog)
        return str(result.get("value", "cancel"))

    def _open_project_from_path(self, project_path: str) -> None:
        project_choice = "keep"
        compared_added: list[str] = []
        compared_missing: list[str] = []
        scanned_paths_precheck: list[str] = []
        include_subfolders = True
        loaded: dict[str, Any] | None = None
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

            resolved_source_folder = str(Path(selected_folder or source_folder).expanduser())
            loaded_settings = loaded.get("settings") if isinstance(loaded.get("settings"), dict) else {}
            include_subfolders = self._safe_bool_setting(
                loaded_settings.get("mix_include_subfolders"),
                True,
            )

            compared_added, compared_missing, scanned_paths_precheck = self._compare_project_tracks_with_folder(
                loaded_project=loaded,
                source_folder=resolved_source_folder,
                include_subfolders=include_subfolders,
            )

            if compared_added or compared_missing:
                project_choice = self._ask_project_source_variation_action(
                    added_files=compared_added,
                    missing_files=compared_missing,
                )
                if project_choice == "cancel":
                    return

            result = resolve_project_files(
                loaded,
                selected_folder=selected_folder,
                include_subfolders=include_subfolders,
                auto_append_new=False,
            )

        except ProjectValidationError as error:
            messagebox.showerror("Progetto non valido", str(error))
            return
        except ProjectResolutionError as error:
            messagebox.showerror("Errore apertura progetto", str(error))
            return
        except ProjectManagerError as error:
            messagebox.showerror("Errore apertura progetto", str(error))
            return

        restored_order: list[str] = []
        restored_clip_info: dict[str, ClipInfo] = {}
        for item in result.tracks:
            file_name = str(item["file_name"])
            restored_order.append(file_name)
            clip_info = item.get("clip_info")
            if isinstance(clip_info, ClipInfo):
                restored_clip_info[file_name] = clip_info
            else:
                restored_clip_info[file_name] = ClipInfo.from_dict(clip_info)

        scanned_paths_runtime = self.scan_mp3_files(
            result.source_folder,
            include_subfolders=include_subfolders,
        )

        if project_choice == "refresh":
            scan_for_refresh = scanned_paths_runtime if scanned_paths_runtime else scanned_paths_precheck
            resolved_by_key: dict[str, ClipInfo] = {
                self._path_compare_key(name): restored_clip_info.get(name, ClipInfo())
                for name in restored_order
            }
            new_order = list(scan_for_refresh)
            new_clip_info = {
                name: resolved_by_key.get(self._path_compare_key(name), ClipInfo())
                for name in new_order
            }
        else:
            new_order = list(restored_order)
            new_clip_info = dict(restored_clip_info)

            # Fallback robusto per progetti legacy/ambigui: mantiene l'ordine salvato,
            # includendo solo i file realmente disponibili oggi.
            if not new_order and loaded is not None and scanned_paths_runtime:
                saved_order = self._collect_saved_project_track_paths(loaded)
                saved_clip_by_key = self._collect_saved_project_track_clip_info(loaded)
                scanned_key_to_path = {
                    self._path_compare_key(path): path
                    for path in scanned_paths_runtime
                }
                fallback_order: list[str] = []
                fallback_clip_info: dict[str, ClipInfo] = {}
                for saved_path in saved_order:
                    key = self._path_compare_key(saved_path)
                    current_path = scanned_key_to_path.get(key)
                    if current_path is None:
                        continue
                    fallback_order.append(current_path)
                    fallback_clip_info[current_path] = saved_clip_by_key.get(key, ClipInfo())
                if fallback_order:
                    new_order = fallback_order
                    new_clip_info = fallback_clip_info

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
            f"{len(compared_missing or result.missing_files)} mancanti, "
            f"{len(compared_added)} nuovi, "
            f"{len(result.modified_files)} modificati."
        )

        if project_choice == "keep" and compared_missing:
            warning_lines = [
                "Alcuni file previsti dal progetto non sono disponibili nella cartella sorgente:",
                "",
            ]
            warning_lines.extend(self._format_project_variation_lines("File mancanti:", compared_missing))
            messagebox.showwarning("File mancanti nel progetto", "\n".join(warning_lines))

        if result.missing_files or result.modified_files or result.warnings:
            summary_lines = [
                "Progetto caricato.",
                "",
                f"Brani ripristinati: {len(new_order)}",
                f"Brani mancanti: {len(result.missing_files)}",
                f"Nuovi brani trovati: {len(compared_added)}",
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

    def _on_mix_subfolders_toggled(self) -> None:
        self.update_preview()
        self._mark_project_dirty()

        if self._suspend_project_dirty_tracking:
            return

        self.save_settings()

        folder = self.input_entry.get().strip()
        if folder and Path(folder).is_dir():
            self.load_mp3_list(mark_dirty=False)

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

    def _select_recovery_problematic_file(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella contenente i file problematici",
            parent=self._recovery_dialog,
        )
        if selected and self._recovery_problematic_entry is not None:
            self._replace_entry(self._recovery_problematic_entry, selected)

    def _select_recovery_original_file(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella contenente gli originali integri",
            parent=self._recovery_dialog,
        )
        if selected and self._recovery_original_entry is not None:
            self._replace_entry(self._recovery_original_entry, selected)

    def _select_recovery_output_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella di destinazione",
            parent=self._recovery_dialog,
        )
        if selected and self._recovery_output_entry is not None:
            self._replace_entry(self._recovery_output_entry, selected)

    def _open_recovery_results_folder(self) -> None:
        target = (self._recovery_session_folder or "").strip()
        if not target:
            return
        path = Path(target)
        if not path.exists():
            messagebox.showwarning("Recupero MP3", f"Cartella non trovata:\n{path}", parent=self._recovery_dialog)
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:
            messagebox.showerror("Recupero MP3", f"Impossibile aprire la cartella esiti:\n{error}", parent=self._recovery_dialog)

    @staticmethod
    def _compute_recovery_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
        safe_screen_width = max(900, int(screen_width))
        safe_screen_height = max(700, int(screen_height))
        horizontal_margin = 64
        vertical_margin = 96
        desired_width = 1320
        desired_height = 900
        window_width = min(desired_width, max(860, safe_screen_width - horizontal_margin))
        window_height = min(desired_height, max(620, safe_screen_height - vertical_margin))
        return window_width, window_height, horizontal_margin, vertical_margin

    def open_mp3_recovery_window(self) -> None:
        if self.recovery_worker.is_running:
            messagebox.showwarning(
                "Recupero MP3",
                "Un recupero MP3 è già in corso.",
                parent=self,
            )
            return

        if self._recovery_dialog is not None and self._recovery_dialog.winfo_exists():
            self._recovery_dialog.deiconify()
            self._recovery_dialog.lift()
            self._recovery_dialog.focus_force()
            return

        window = ctk.CTkToplevel(self)
        window.title("Recupero massivo MP3 da Originali")
        screen_width = max(900, int(window.winfo_screenwidth()))
        screen_height = max(700, int(window.winfo_screenheight()))
        window_width, window_height, _horizontal_margin, _vertical_margin = self._compute_recovery_window_geometry(
            screen_width,
            screen_height,
        )
        x_position = max(0, (screen_width - window_width) // 2)
        y_position = max(0, (screen_height - window_height) // 2)
        window.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        window.minsize(860, 620)
        window.resizable(True, True)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_recovery_window)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=0)
        self._recovery_dialog = window

        frame = ctk.CTkFrame(window)
        frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, weight=0)
        for fixed_row in (0, 1, 2, 3, 4, 5):
            frame.grid_rowconfigure(fixed_row, weight=0)
        frame.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(
            frame,
            text="Cartella file problematici",
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self._recovery_problematic_entry = ctk.CTkEntry(frame)
        self._recovery_problematic_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(10, 4))
        problematic_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_recovery_problematic_file)
        problematic_browse.grid(row=0, column=2, padx=(0, 10), pady=(10, 4))

        ctk.CTkLabel(
            frame,
            text="Cartella MP3 originali integri",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self._recovery_original_entry = ctk.CTkEntry(frame)
        self._recovery_original_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=4)
        original_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_recovery_original_file)
        original_browse.grid(row=1, column=2, padx=(0, 10), pady=4)

        ctk.CTkLabel(
            frame,
            text="Cartella di destinazione",
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self._recovery_output_entry = ctk.CTkEntry(frame)
        self._recovery_output_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)
        default_output_folder = self.output_folder or self.input_folder or str(Path.home())
        self._recovery_output_entry.insert(0, default_output_folder)
        output_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_recovery_output_folder)
        output_browse.grid(row=2, column=2, padx=(0, 10), pady=4)

        mode_frame = ctk.CTkFrame(frame, fg_color="transparent")
        mode_frame.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 2))
        mode_frame.grid_columnconfigure(0, weight=1)
        mode_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(mode_frame, text="Modalita recupero", anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._recovery_mode_normal_radio = ctk.CTkRadioButton(
            mode_frame,
            text="Recupero normale",
            variable=self._recovery_mode_var,
            value=RecoveryMode.NORMAL.value,
        )
        self._recovery_mode_normal_radio.grid(row=1, column=0, sticky="w", padx=(0, 12))
        self._recovery_mode_forced_radio = ctk.CTkRadioButton(
            mode_frame,
            text="Recupero forzato / esperto",
            variable=self._recovery_mode_var,
            value=RecoveryMode.FORCED.value,
        )
        self._recovery_mode_forced_radio.grid(row=1, column=1, sticky="w")
        self._add_tooltip(
            self._recovery_mode_normal_radio,
            "Confronta il contenuto audio del file da recuperare con l’originale e procede solo quando la compatibilità richiesta è confermata.",
        )
        self._add_tooltip(
            self._recovery_mode_forced_radio,
            "Ignora il confronto di compatibilità audio e utilizza comunque l’originale trovato per il recupero. I TAG WinLive vengono trasferiti e il file finale viene sottoposto a verifica di integrità. Usare solo quando si è certi che l’originale associato sia corretto.",
        )

        self._recovery_status_label = ctk.CTkLabel(frame, text="Pronto", anchor="w")
        self._recovery_status_label.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 3))

        self._recovery_counters_label = ctk.CTkLabel(frame, text="", anchor="w", justify="left")
        self._recovery_counters_label.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        self._reset_recovery_live_counters()

        split_container = tk.PanedWindow(
            frame,
            orient=tk.VERTICAL,
            sashwidth=8,
            bd=0,
            relief="flat",
            showhandle=False,
        )
        split_container.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 8))

        self._recovery_monitor_frame = ctk.CTkFrame(split_container)
        self._recovery_monitor_frame.grid_columnconfigure(0, weight=1)
        self._recovery_examined_label = ctk.CTkLabel(self._recovery_monitor_frame, text="File esaminati: 0 / 0", anchor="w")
        self._recovery_examined_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 1))
        self._recovery_completed_label = ctk.CTkLabel(self._recovery_monitor_frame, text="File completati: 0 / 0", anchor="w")
        self._recovery_completed_label.grid(row=1, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_batch_status_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Stato batch: Pronto", anchor="w")
        self._recovery_batch_status_label.grid(row=2, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_current_file_label = ctk.CTkLabel(self._recovery_monitor_frame, text="File corrente: -", anchor="w")
        self._recovery_current_file_label.grid(row=3, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_phase_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Fase corrente: Pronto", anchor="w")
        self._recovery_phase_label.grid(row=4, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_elapsed_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Tempo trascorso complessivo: 00:00:00", anchor="w")
        self._recovery_elapsed_label.grid(row=5, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_current_file_elapsed_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Tempo elaborazione file corrente: 00:00:00", anchor="w")
        self._recovery_current_file_elapsed_label.grid(row=6, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_eta_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Tempo restante stimato: Calcolo in corso...", anchor="w")
        self._recovery_eta_label.grid(row=7, column=0, sticky="ew", padx=10, pady=1)
        self._recovery_percent_label = ctk.CTkLabel(self._recovery_monitor_frame, text="Percentuale batch: 0%", anchor="w")
        self._recovery_percent_label.grid(row=8, column=0, sticky="ew", padx=10, pady=(1, 3))

        self._recovery_progress_bar = ctk.CTkProgressBar(self._recovery_monitor_frame)
        self._recovery_progress_bar.grid(row=9, column=0, sticky="ew", padx=10, pady=(2, 6))
        self._recovery_progress_bar.set(0)

        log_container = ctk.CTkFrame(split_container)
        log_container.grid_columnconfigure(0, weight=1)
        log_container.grid_rowconfigure(1, weight=1)

        log_title = ctk.CTkLabel(log_container, text="Log operazioni", anchor="w")
        log_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))

        self._recovery_log_box = ctk.CTkTextbox(log_container, height=240)
        self._recovery_log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self._recovery_log_box.configure(state="disabled")

        split_container.add(self._recovery_monitor_frame, minsize=210)
        split_container.add(log_container, minsize=220)
        desired_log_height = max(220, min(280, int(window_height * 0.33)))
        desired_monitor_height = max(220, int(window_height - desired_log_height - 260))
        try:
            split_container.sash_place(0, 0, desired_monitor_height)
        except Exception:
            pass
        self._reset_recovery_monitor_state()

        button_row = ctk.CTkFrame(window, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        button_row.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self._recovery_command_bar = button_row

        self._recovery_start_button = ctk.CTkButton(
            button_row,
            text="Avvia recupero massivo",
            height=42,
            command=self._start_mp3_recovery,
        )
        self._recovery_start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._recovery_stop_button = ctk.CTkButton(
            button_row,
            text="Interrompi",
            height=42,
            command=self._request_stop_mp3_recovery,
            state="disabled",
        )
        self._recovery_stop_button.grid(row=0, column=1, sticky="ew", padx=(6, 6))

        self._recovery_close_button = ctk.CTkButton(
            button_row,
            text="Chiudi",
            height=42,
            command=self._close_recovery_window,
        )
        self._recovery_close_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))

        self._recovery_open_results_button = ctk.CTkButton(
            button_row,
            text="Apri cartella esiti",
            height=42,
            state="disabled",
            command=self._open_recovery_results_folder,
        )
        self._recovery_open_results_button.grid(row=0, column=2, sticky="ew", padx=(6, 6))

        self._recovery_path_widgets = [
            self._recovery_problematic_entry,
            self._recovery_original_entry,
            self._recovery_output_entry,
            problematic_browse,
            original_browse,
            output_browse,
            self._recovery_mode_normal_radio,
            self._recovery_mode_forced_radio,
        ]

        self._add_tooltip(
            self._recovery_problematic_entry,
            "Seleziona la cartella contenente gli MP3 da recuperare. Verranno analizzati soltanto i file presenti direttamente nella cartella.",
        )
        self._add_tooltip(
            problematic_browse,
            "Seleziona la cartella contenente gli MP3 da recuperare. Verranno analizzati soltanto i file presenti direttamente nella cartella.",
        )
        self._add_tooltip(
            self._recovery_original_entry,
            "Seleziona la cartella contenente gli MP3 originali integri con lo stesso nome dei file problematici.",
        )
        self._add_tooltip(
            original_browse,
            "Seleziona la cartella contenente gli MP3 originali integri con lo stesso nome dei file problematici.",
        )
        self._add_tooltip(
            self._recovery_output_entry,
            "Seleziona la cartella in cui creare direttamente la sessione Diagnosi Recupero. Puo coincidere con la cartella dei file problematici.",
        )
        self._add_tooltip(
            output_browse,
            "Seleziona la cartella in cui creare direttamente la sessione Diagnosi Recupero. Puo coincidere con la cartella dei file problematici.",
        )
        self._add_tooltip(
            self._recovery_start_button,
            "Avvia il recupero degli MP3 presenti direttamente nelle cartelle selezionate.",
        )
        self._add_tooltip(
            self._recovery_stop_button,
            "Interrompe il recupero dopo il file attualmente in elaborazione, senza modificare i file originali.",
        )
        self._add_tooltip(
            self._recovery_open_results_button,
            "Apre la cartella della sessione corrente con esiti, report e diagnostica scanner.",
        )

        self._append_log("Apertura finestra recupero MP3.")

        self._recovery_mode_var.set(RecoveryMode.NORMAL.value)

    def _confirm_forced_recovery(self) -> bool:
        if self._recovery_dialog is None:
            return False

        dialog = ctk.CTkToplevel(self._recovery_dialog)
        self._recovery_forced_confirmation_dialog = dialog
        dialog.title("Conferma recupero forzato")
        dialog.resizable(False, False)
        dialog.transient(self._recovery_dialog)
        dialog.grab_set()

        width = 680
        height = 300
        parent = self._recovery_dialog
        x_position = max(0, parent.winfo_rootx() + (max(1, parent.winfo_width()) - width) // 2)
        y_position = max(0, parent.winfo_rooty() + (max(1, parent.winfo_height()) - height) // 2)
        dialog.geometry(f"{width}x{height}+{x_position}+{y_position}")

        confirmed = {"value": False}

        def _finish(value: bool) -> None:
            confirmed["value"] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            if dialog.winfo_exists():
                dialog.destroy()

        def _on_close() -> None:
            _finish(False)

        dialog.protocol("WM_DELETE_WINDOW", _on_close)

        content = ctk.CTkFrame(dialog)
        content.pack(fill="both", expand=True, padx=16, pady=16)
        content.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            content,
            text=(
                "La modalita Recupero forzato ignora il confronto di compatibilita audio tra il file da recuperare e l’originale.\n\n"
                "Il programma utilizzera comunque l’originale associato, trasferira gli eventuali TAG WinLive ed eseguira la verifica di integrita finale.\n\n"
                "Utilizzare questa modalita solo se si e certi che gli originali selezionati siano corretti.\n\n"
                "Procedere?"
            ),
            justify="left",
            anchor="w",
            wraplength=620,
        ).grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 18))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew")
        button_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(button_row, text="Procedi", command=lambda: _finish(True)).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(button_row, text="Annulla", command=lambda: _finish(False)).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        parent.wait_window(dialog)
        self._recovery_forced_confirmation_dialog = None
        return confirmed["value"]

    def _close_recovery_window(self) -> None:
        if self.recovery_worker.is_running:
            should_hide = messagebox.askyesno(
                "Recupero MP3",
                "Un recupero massivo e in esecuzione. Vuoi nascondere la finestra?",
                parent=self._recovery_dialog,
            )
            if not should_hide:
                return
            if self._recovery_dialog is not None:
                self._recovery_dialog.withdraw()
            return

        if self._recovery_forced_confirmation_dialog is not None:
            try:
                self._recovery_forced_confirmation_dialog.destroy()
            except Exception:
                pass
            self._recovery_forced_confirmation_dialog = None

        if self._recovery_dialog is not None:
            try:
                self._recovery_dialog.destroy()
            except Exception:
                pass
        self._recovery_dialog = None
        self._recovery_problematic_entry = None
        self._recovery_original_entry = None
        self._recovery_output_entry = None
        self._recovery_mode_normal_radio = None
        self._recovery_mode_forced_radio = None
        self._recovery_status_label = None
        self._recovery_counters_label = None
        self._recovery_progress_bar = None
        self._recovery_log_box = None
        self._recovery_command_bar = None
        self._recovery_monitor_frame = None
        self._recovery_examined_label = None
        self._recovery_completed_label = None
        self._recovery_batch_status_label = None
        self._recovery_current_file_label = None
        self._recovery_phase_label = None
        self._recovery_elapsed_label = None
        self._recovery_current_file_elapsed_label = None
        self._recovery_eta_label = None
        self._recovery_percent_label = None
        self._recovery_start_button = None
        self._recovery_stop_button = None
        self._recovery_close_button = None
        self._recovery_open_results_button = None
        self._recovery_path_widgets = []
        self._recovery_session_folder = None
        self._recovery_session_snapshot = None
        self._recovery_allow_session_log_updates = False
        self._recovery_expected_output_root = ""
        self._recovery_min_session_timestamp = ""
        self._stop_recovery_timer()

    def _reset_recovery_monitor_state(self) -> None:
        self._recovery_started_at = None
        self._recovery_current_file_started_at = None
        self._recovery_total_files = 0
        self._recovery_examined_files = 0
        self._recovery_completed_files = 0
        self._recovery_current_file_name = "-"
        self._recovery_current_phase = "Pronto"
        self._recovery_completed_file_durations = []
        self._render_recovery_monitor()

    def _start_recovery_timer(self) -> None:
        if self._recovery_timer_job is not None:
            self.after_cancel(self._recovery_timer_job)
        self._tick_recovery_timer()

    def _stop_recovery_timer(self) -> None:
        if self._recovery_timer_job is not None:
            self.after_cancel(self._recovery_timer_job)
            self._recovery_timer_job = None

    def _tick_recovery_timer(self) -> None:
        if self._recovery_started_at is None:
            self._recovery_timer_job = None
            return
        self._render_recovery_monitor()
        self._recovery_timer_job = self.after(1000, self._tick_recovery_timer)

    def _render_recovery_monitor(self) -> None:
        examined_text = f"File esaminati: {self._recovery_examined_files} / {self._recovery_total_files}"
        completed_text = f"File completati: {self._recovery_completed_files} / {self._recovery_total_files}"
        current_file_text = f"File corrente: {self._recovery_current_file_name}"
        phase_text = f"Fase corrente: {self._recovery_current_phase}"
        batch_status = "In corso"
        if self._recovery_current_phase in {"Interrotto", "Errore"}:
            batch_status = self._recovery_current_phase
        elif self._recovery_current_phase == "Completato":
            batch_status = "Completato"
        elif self._recovery_started_at is None:
            batch_status = "Pronto"

        elapsed_total = 0.0 if self._recovery_started_at is None else max(0.0, time.monotonic() - self._recovery_started_at)
        elapsed_file = 0.0 if self._recovery_current_file_started_at is None else max(0.0, time.monotonic() - self._recovery_current_file_started_at)

        if self._recovery_completed_files <= 0:
            eta_text = "Calcolo in corso..."
        else:
            average = sum(self._recovery_completed_file_durations) / float(self._recovery_completed_files)
            remaining = max(0, self._recovery_total_files - self._recovery_completed_files)
            eta_text = self._format_duration(average * remaining)

        percent = 0
        if self._recovery_total_files > 0:
            percent = int((self._recovery_completed_files / float(self._recovery_total_files)) * 100)

        if self._recovery_examined_label is not None:
            self._recovery_examined_label.configure(text=examined_text)
        if self._recovery_completed_label is not None:
            self._recovery_completed_label.configure(text=completed_text)
        if self._recovery_batch_status_label is not None:
            self._recovery_batch_status_label.configure(text=f"Stato batch: {batch_status}")
        if self._recovery_current_file_label is not None:
            self._recovery_current_file_label.configure(text=current_file_text)
        if self._recovery_phase_label is not None:
            self._recovery_phase_label.configure(text=phase_text)
        if self._recovery_elapsed_label is not None:
            self._recovery_elapsed_label.configure(text=f"Tempo trascorso complessivo: {self._format_duration(elapsed_total)}")
        if self._recovery_current_file_elapsed_label is not None:
            self._recovery_current_file_elapsed_label.configure(text=f"Tempo elaborazione file corrente: {self._format_duration(elapsed_file)}")
        if self._recovery_eta_label is not None:
            self._recovery_eta_label.configure(text=f"Tempo restante stimato: {eta_text}")
        if self._recovery_percent_label is not None:
            self._recovery_percent_label.configure(text=f"Percentuale batch: {percent}%")

    def _set_recovery_phase(self, phase: str) -> None:
        self._recovery_current_phase = phase.strip() or "Pronto"
        self._render_recovery_monitor()

    def _set_recovery_current_file(self, current: int, total: int, file_name: str) -> None:
        self._recovery_total_files = max(self._recovery_total_files, max(0, int(total)))
        self._recovery_examined_files = max(self._recovery_examined_files, max(0, int(current)))
        self._recovery_current_file_name = file_name.strip() or "-"
        self._recovery_current_file_started_at = time.monotonic()
        self._render_recovery_monitor()

    def _mark_recovery_file_completed(self, current: int, total: int) -> None:
        now = time.monotonic()
        self._recovery_total_files = max(self._recovery_total_files, max(0, int(total)))
        self._recovery_completed_files = max(self._recovery_completed_files, max(0, min(int(current), self._recovery_total_files if self._recovery_total_files > 0 else int(current))))
        if self._recovery_current_file_started_at is not None and self._recovery_completed_files > len(self._recovery_completed_file_durations):
            self._recovery_completed_file_durations.append(max(0.0, now - self._recovery_current_file_started_at))
        self._render_recovery_monitor()

    def _process_recovery_tech_message(self, message: str) -> None:
        if message.startswith("[TECH] Conteggio problematici="):
            try:
                self._recovery_total_files = max(0, int(message.split("=", 1)[1].strip()))
            except ValueError:
                return
            self._render_recovery_monitor()
            return

        if message.startswith("[TECH] Inizio file "):
            payload = message[len("[TECH] Inizio file "):]
            progress_text, _, file_name = payload.partition(" | ")
            current_text, _, total_text = progress_text.partition("/")
            try:
                current = int(current_text)
                total = int(total_text)
            except ValueError:
                return
            self._set_recovery_current_file(current, total, file_name)
            return

        if message.startswith("[TECH] Fase -> "):
            self._set_recovery_phase(message.split("->", 1)[1].strip())
            return

        if message.startswith("[TECH] Sessione esiti creata | path="):
            if not self._recovery_allow_session_log_updates:
                return
            candidate = message.split("path=", 1)[1].strip()
            if not candidate:
                return
            try:
                candidate_path = Path(candidate).resolve()
            except Exception:
                return

            if self._recovery_expected_output_root:
                expected_root = Path(self._recovery_expected_output_root).resolve()
                expected_prefix = str(expected_root).casefold()
                if not str(candidate_path).casefold().startswith(expected_prefix):
                    return

            if self._recovery_min_session_timestamp:
                name = candidate_path.name
                prefix = "Diagnosi Recupero "
                if name.startswith(prefix):
                    stamp = name[len(prefix):]
                    if stamp < self._recovery_min_session_timestamp:
                        return

            self._recovery_session_folder = str(candidate_path)
            if self._recovery_open_results_button is not None:
                self._recovery_open_results_button.configure(state="normal")

    def _reset_recovery_live_counters(self) -> None:
        self._recovery_live_counters = {
            MP3BatchOutcome.RECOVERED_TAGS.value: 0,
            MP3BatchOutcome.RECOVERED_UNCHANGED.value: 0,
            MP3BatchOutcome.RECOVERED_FORCED.value: 0,
            MP3BatchOutcome.ORIGINAL_NOT_FOUND.value: 0,
            MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value: 0,
            MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value: 0,
            "errori": 0,
        }
        self._render_recovery_counters()

    def _render_recovery_counters(self) -> None:
        if self._recovery_counters_label is None:
            return
        counters = self._recovery_live_counters
        text = (
            f"Recuperati con TAG trasferiti: {counters[MP3BatchOutcome.RECOVERED_TAGS.value]} | "
            f"Recuperati come copia invariata: {counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value]} | "
            f"Recuperati forzatamente: {counters[MP3BatchOutcome.RECOVERED_FORCED.value]} | "
            f"Originale non trovato: {counters[MP3BatchOutcome.ORIGINAL_NOT_FOUND.value]}\n"
            f"Originale incompatibile: {counters[MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value]} | "
            f"Piu originali compatibili: {counters[MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value]} | "
            f"Errori: {counters['errori']}"
        )
        self._recovery_counters_label.configure(text=text)

    def _update_recovery_live_counters_from_log(self, message: str) -> None:
        if message.startswith("[RECUPERATO TAG]"):
            self._recovery_live_counters[MP3BatchOutcome.RECOVERED_TAGS.value] += 1
        elif message.startswith("[COPIA INVARIATA]"):
            self._recovery_live_counters[MP3BatchOutcome.RECOVERED_UNCHANGED.value] += 1
        elif message.startswith("[RECUPERATO FORZATO]"):
            self._recovery_live_counters[MP3BatchOutcome.RECOVERED_FORCED.value] += 1
        elif message.startswith("[NON TROVATO]"):
            self._recovery_live_counters[MP3BatchOutcome.ORIGINAL_NOT_FOUND.value] += 1
        elif message.startswith("[INCOMPATIBILE]"):
            self._recovery_live_counters[MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value] += 1
        elif message.startswith("[AMBIGUO]"):
            self._recovery_live_counters[MP3BatchOutcome.MULTIPLE_COMPATIBLE_ORIGINALS.value] += 1
        elif message.startswith("[ERRORE]"):
            self._recovery_live_counters["errori"] += 1
        self._render_recovery_counters()

    def _set_recovery_ui_running_state(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self._recovery_path_widgets:
            try:
                widget.configure(state=state)
            except Exception:
                pass
        if self._recovery_start_button is not None:
            self._recovery_start_button.configure(state="disabled" if running else "normal")
        if self._recovery_stop_button is not None:
            self._recovery_stop_button.configure(state="normal" if running else "disabled")
        if self._recovery_open_results_button is not None and not self._recovery_session_folder:
            self._recovery_open_results_button.configure(state="disabled")

    def _append_recovery_log(self, message: str) -> None:
        if self._recovery_log_box is not None:
            self._recovery_log_box.configure(state="normal")
            self._recovery_log_box.insert("end", f"{message}\n")
            self._recovery_log_box.see("end")
            self._recovery_log_box.configure(state="disabled")
        self._process_recovery_tech_message(message)
        self._update_recovery_live_counters_from_log(message)
        self._append_log(message)

    def _start_mp3_recovery(self) -> None:
        if self.recovery_worker.is_running:
            messagebox.showwarning("Recupero MP3", "Un recupero MP3 è già in corso.", parent=self._recovery_dialog)
            return

        # Reset the previous session state on every new start click, before any validation.
        self._recovery_session_folder = None
        self._recovery_allow_session_log_updates = False
        self._recovery_expected_output_root = ""
        self._recovery_min_session_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if self._recovery_open_results_button is not None:
            self._recovery_open_results_button.configure(state="disabled")

        problematic_dir = self._recovery_problematic_entry.get().strip() if self._recovery_problematic_entry is not None else ""
        originals_dir = self._recovery_original_entry.get().strip() if self._recovery_original_entry is not None else ""
        destination_dir = self._recovery_output_entry.get().strip() if self._recovery_output_entry is not None else ""

        if not problematic_dir or not Path(problematic_dir).is_dir():
            messagebox.showerror("Recupero MP3", "Seleziona una cartella file problematici valida.", parent=self._recovery_dialog)
            return

        if not originals_dir or not Path(originals_dir).is_dir():
            messagebox.showerror("Recupero MP3", "Seleziona una cartella MP3 originali integri valida.", parent=self._recovery_dialog)
            return

        if not destination_dir:
            messagebox.showerror("Recupero MP3", "Seleziona una cartella di destinazione valida.", parent=self._recovery_dialog)
            return

        try:
            Path(destination_dir).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Recupero MP3", f"Impossibile creare la cartella di destinazione:\n{error}", parent=self._recovery_dialog)
            return

        if Path(problematic_dir).resolve() == Path(originals_dir).resolve():
            messagebox.showerror(
                "Recupero MP3",
                "La cartella problematici deve essere diversa dalla cartella originali.",
                parent=self._recovery_dialog,
            )
            return

        destination_path = Path(destination_dir).resolve()
        if destination_path == Path(originals_dir).resolve():
            messagebox.showerror(
                "Recupero MP3",
                "La cartella di destinazione non puo coincidere con la cartella originali.",
                parent=self._recovery_dialog,
            )
            return

        selected_recovery_mode = RecoveryMode.coerce(self._recovery_mode_var.get())
        if selected_recovery_mode == RecoveryMode.FORCED and not self._confirm_forced_recovery():
            return

        problematic_top = [
            item for item in Path(problematic_dir).iterdir()
            if item.is_file() and item.suffix.lower() == ".mp3"
        ]
        originals_top = [
            item for item in Path(originals_dir).iterdir()
            if item.is_file() and item.suffix.lower() == ".mp3"
        ]
        if not problematic_top:
            messagebox.showerror(
                "Recupero MP3",
                "Nessun file MP3 trovato nella cartella dei problematici.",
                parent=self._recovery_dialog,
            )
            return
        if not originals_top:
            messagebox.showerror(
                "Recupero MP3",
                "Nessun file MP3 trovato nella cartella degli originali.",
                parent=self._recovery_dialog,
            )
            return

        if self._recovery_status_label is not None:
            self._recovery_status_label.configure(text="Recupero massivo in corso...")
        if self._recovery_progress_bar is not None:
            self._recovery_progress_bar.set(0)
        self._recovery_expected_output_root = str(destination_path)
        self._recovery_allow_session_log_updates = True
        self._reset_recovery_monitor_state()
        self._recovery_started_at = time.monotonic()
        self._reset_recovery_live_counters()
        self._set_recovery_ui_running_state(True)
        self._set_recovery_phase("Ricerca originale")
        self._start_recovery_timer()

        self._append_recovery_log("Avvio recupero massivo MP3.")
        forced_mode = selected_recovery_mode == RecoveryMode.FORCED
        self._recovery_session_snapshot = {
            "processing_type": "Recupero MP3",
            "recovery_mode": selected_recovery_mode.value,
            "recovery_mode_label": "Forzato" if forced_mode else "Normale",
            "forced_recovery": forced_mode,
            "audio_comparison_enabled": not forced_mode,
            "matching_by_filename": True,
            "problematic_dir": str(Path(problematic_dir).expanduser().resolve()),
            "originals_dir": str(Path(originals_dir).expanduser().resolve()),
            "destination_dir": str(Path(destination_dir).expanduser().resolve()),
        }

        try:
            self.recovery_worker.start(
                problematic_dir=problematic_dir,
                originals_dir=originals_dir,
                destination_dir=destination_dir,
                recovery_mode=selected_recovery_mode,
                session_snapshot=dict(self._recovery_session_snapshot),
            )
        except Exception as error:
            self._recovery_allow_session_log_updates = False
            messagebox.showerror("Recupero MP3", str(error), parent=self._recovery_dialog)
            if self._recovery_status_label is not None:
                self._recovery_status_label.configure(text="Errore recupero MP3")
            self._set_recovery_ui_running_state(False)

    def _request_stop_mp3_recovery(self) -> None:
        if not self.recovery_worker.is_running:
            return
        should_stop = messagebox.askyesno(
            "Recupero MP3",
            "Vuoi interrompere il recupero dopo il file attualmente in elaborazione?",
            parent=self._recovery_dialog,
        )
        if not should_stop:
            return
        self.recovery_worker.cancel()
        self._append_recovery_log("Richiesta di interruzione inviata.")

    def _recovery_worker_progress(self, current: int, total: int, message: str) -> None:
        self.after(0, self._handle_recovery_worker_progress, current, total, message)

    def _handle_recovery_worker_progress(self, current: int, total: int, message: str) -> None:
        self._mark_recovery_file_completed(current, total)
        if self._recovery_status_label is not None:
            self._recovery_status_label.configure(text=message)
        if self._recovery_progress_bar is not None:
            self._recovery_progress_bar.set(0 if total <= 0 else min(1.0, max(0.0, current / total)))


    def _recovery_worker_log(self, message: str) -> None:
        self.after(0, self._append_recovery_log, message)

    def _recovery_worker_completed(self, result) -> None:
        self.after(0, self._handle_recovery_worker_completed, result)

    def _handle_recovery_worker_completed(self, result) -> None:
        self._stop_recovery_timer()
        self._recovery_session_snapshot = None
        self._recovery_allow_session_log_updates = False
        examined = int(getattr(result, "examined_problematic", result.processed_problematic))
        completed = int(getattr(result, "completed_problematic", result.processed_problematic if not result.interrupted else 0))
        self._recovery_examined_files = max(self._recovery_examined_files, examined)
        self._recovery_completed_files = max(self._recovery_completed_files, completed)
        self._recovery_total_files = max(self._recovery_total_files, int(result.total_problematic))
        self._recovery_session_folder = str(getattr(result, "session_folder", "") or result.output_root)
        if self._recovery_open_results_button is not None and self._recovery_session_folder:
            self._recovery_open_results_button.configure(state="normal")
        self._set_recovery_phase("Completato" if not result.interrupted else "Interrotto")
        self._set_recovery_ui_running_state(False)
        if self._recovery_status_label is not None:
            self._recovery_status_label.configure(text="Recupero completato" if not result.interrupted else "Operazione interrotta")
        if self._recovery_progress_bar is not None:
            progress = 0 if result.total_problematic <= 0 else min(1.0, completed / result.total_problematic)
            self._recovery_progress_bar.set(progress)

        self._append_recovery_log(
            f"Sintesi: tot={result.total_problematic}, esaminati={examined}, completati={completed}, "
            f"tempo={result.elapsed_seconds:.2f}s"
        )
        self._append_recovery_log(f"Report CSV: {result.report_paths.get('csv', '')}")
        self._append_recovery_log(f"Cartella output: {result.output_root}")
        self._append_recovery_log(f"Cartella sessione esiti: {self._recovery_session_folder}")

        total_recovered = (
            result.counters.get(MP3BatchOutcome.RECOVERED_TAGS.value, 0)
            + result.counters.get(MP3BatchOutcome.RECOVERED_UNCHANGED.value, 0)
            + result.counters.get(MP3BatchOutcome.RECOVERED_FORCED.value, 0)
        )
        total_errors = (
            result.counters.get(MP3BatchOutcome.READ_ERROR.value, 0)
            + result.counters.get(MP3BatchOutcome.WRITE_ERROR.value, 0)
            + result.counters.get(MP3BatchOutcome.FINAL_VERIFICATION_FAILED.value, 0)
            + result.counters.get(MP3BatchOutcome.ERROR.value, 0)
        )
        total_not_recovered = result.total_problematic - total_recovered

        if result.interrupted:
            info_text = (
                "Operazione interrotta.\n"
                f"File esaminati: {examined}\n"
                f"File completati: {completed}\n"
                f"Stato parziale salvato in:\n{self._recovery_session_folder}"
            )
        elif total_recovered > 0:
            info_text = (
                "Operazione completata.\n"
                f"File recuperati: {total_recovered}\n"
                f"File non recuperati: {total_not_recovered}\n"
                f"Esiti salvati in:\n{self._recovery_session_folder}"
            )
        else:
            info_text = (
                "Operazione completata senza file recuperati.\n"
                f"Originali incompatibili: {result.counters.get(MP3BatchOutcome.ORIGINAL_INCOMPATIBLE.value, 0)}\n"
                f"Esiti e motivazioni salvati in:\n{self._recovery_session_folder}"
            )

        messagebox.showinfo("Recupero MP3", info_text, parent=self._recovery_dialog)

    def _recovery_worker_error(self, message: str) -> None:
        self.after(0, self._handle_recovery_worker_error, message)

    def _handle_recovery_worker_error(self, message: str) -> None:
        self._stop_recovery_timer()
        self._recovery_session_snapshot = None
        self._recovery_allow_session_log_updates = False
        self._set_recovery_phase("Errore")
        self._set_recovery_ui_running_state(False)
        if self._recovery_status_label is not None:
            self._recovery_status_label.configure(text="Errore recupero MP3")
        self._append_recovery_log(f"ERRORE: {message}")
        if self._recovery_session_folder:
            self._append_recovery_log(f"Stato parziale salvato in: {self._recovery_session_folder}")
            message = f"{message}\n\nStato parziale salvato in:\n{self._recovery_session_folder}"
        messagebox.showerror("Recupero MP3", message, parent=self._recovery_dialog)

    def _recovery_worker_cancelled(self, message: str) -> None:
        self.after(0, self._handle_recovery_worker_cancelled, message)

    def _handle_recovery_worker_cancelled(self, message: str) -> None:
        self._stop_recovery_timer()
        self._recovery_session_snapshot = None
        self._recovery_allow_session_log_updates = False
        self._set_recovery_phase("Interrotto")
        self._set_recovery_ui_running_state(False)
        if self._recovery_status_label is not None:
            self._recovery_status_label.configure(text="Recupero interrotto")
        self._append_recovery_log(message)
        if self._recovery_session_folder:
            self._append_recovery_log(f"Stato parziale salvato in: {self._recovery_session_folder}")

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
        self._stop_diagnostics_timer()
        self.diagnostics_window = None
        self.diagnostics_input_entry = None
        self.diagnostics_output_entry = None
        self.diagnostics_subfolders_checkbox = None
        self.diagnostics_integrity_checkbox = None
        self.diagnostics_winlive_group_label = None
        self.diagnostics_winlive_checkbox = None
        self.diagnostics_placement_copy_radio = None
        self.diagnostics_placement_move_radio = None
        self.diagnostics_repair_button = None
        self.diagnostics_stop_button = None
        self.diagnostics_progress = None
        self.diagnostics_status_label = None
        self.diagnostics_count_label = None
        self.diagnostics_elapsed_label = None
        self.diagnostics_eta_label = None
        self.diagnostics_log_box = None

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

    def start_diagnostics_repair(self) -> None:
        self._start_diagnostics_worker(repair_mode=True)

    def _start_diagnostics_worker(
        self,
        repair_mode: bool,
        *,
        input_folder: str | None = None,
        output_folder: str | None = None,
        include_subfolders: bool | None = None,
        selected_input_files: list[Path] | None = None,
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
        verify_mp3_integrity = bool(self.diagnostics_verify_mp3_integrity_var.get())
        verify_winlive = bool(self.diagnostics_verify_winlive_var.get())
        placement_mode = str(self.diagnostics_placement_mode_var.get() or "copy").strip().lower()
        if placement_mode not in ("copy", "move"):
            placement_mode = "copy"

        if not verify_mp3_integrity and not verify_winlive:
            messagebox.showerror(
                "Diagnostica MP3",
                "Almeno un controllo diagnostico deve essere attivo.",
                parent=self._diagnostics_dialog_parent(),
            )
            self._raise_diagnostics_window()
            return

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

        self._set_diagnostics_entry_values(input_folder=selected_input, output_folder=selected_output)
        self.settings["diagnostics_placement_mode"] = placement_mode
        self.save_settings()
        self._diagnostics_session_snapshot = {
            "processing_type": "Diagnostica MP3",
            "include_subfolders": bool(selected_subfolders),
            "verify_mp3_integrity": bool(verify_mp3_integrity),
            "verify_winlive": bool(verify_winlive),
            "placement_mode": placement_mode,
            "placement_mode_label": "Copia" if placement_mode == "copy" else "Sposta",
            "input_folder": str(Path(selected_input).expanduser().resolve()),
            "output_folder": str(Path(selected_output).expanduser().resolve()),
        }

        self.diagnostics_worker_total = 0
        self.diagnostics_last_progress = 0
        self.diagnostics_worker_start_time = time.monotonic()
        self.diagnostics_eta_estimator.reset(total_units=0, initial_seconds_per_unit=8.0)
        self.diagnostics_progress.set(0)
        self.diagnostics_status_label.configure(text=start_message)
        self.diagnostics_count_label.configure(text="File analizzati: 0 / 0")
        self.diagnostics_elapsed_label.configure(text="Tempo: 00:00:00")
        self.diagnostics_eta_label.configure(text="Tempo stimato restante: --")
        self._append_diagnostics_log(log_message)
        self._append_diagnostics_log(
            f"Integrità MP3: {'ATTIVA' if verify_mp3_integrity else 'DISATTIVA'}"
        )
        self._append_diagnostics_log(
            f"WinLive: {'ATTIVO' if verify_winlive else 'DISATTIVO'}"
        )
        if verify_winlive:
            self._append_diagnostics_log("Verifica WinLive attivata.")
        self._start_diagnostics_timer()

        try:
            self.diagnostics_worker.start(
                input_folder=selected_input,
                include_subfolders=selected_subfolders,
                output_folder=selected_output,
                repair_mode=repair_mode,
                placement_mode=placement_mode,
                selected_input_files=selected_input_files,
                verify_mp3_integrity=verify_mp3_integrity,
                verify_winlive=verify_winlive,
                session_snapshot=dict(self._diagnostics_session_snapshot),
            )
        except Exception as error:
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
            if current <= 0:
                self.diagnostics_eta_estimator.reset(total_units=total, initial_seconds_per_unit=8.0)
            else:
                self.diagnostics_eta_estimator.observe(current, total_units=total)

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
        diagnostic_results = payload.get("diagnostic_results", []) if isinstance(payload, dict) else []
        winlive_summary = self._build_winlive_runtime_summary(diagnostic_results)
        self._append_diagnostics_log("Diagnostica completata.")
        self._append_diagnostics_log(f"Report CSV: {report_paths.get('csv', '')}")
        self._append_diagnostics_log(f"Report XLSX: {report_paths.get('xlsx', '')}")
        self._append_diagnostics_log(f"Report HTML: {report_paths.get('html', '')}")
        self._append_winlive_completion_logs(diagnostic_results, winlive_summary)

        self._load_latest_diagnostics_index(preferred_output_folder=self._diagnostics_output_value())
        self._refresh_track_list_box()
        self.update_preview()

        title = "Diagnostica MP3"
        verify_integrity = bool(summary.get("verify_mp3_integrity", True))
        if verify_integrity:
            base_message = (
                "Diagnostica completata.\n\n"
                f"File già rilevati OK: {summary.get('category_ok_files', 0)}\n"
                f"File riparati: {summary.get('category_repaired_files', 0)}\n"
                f"File non recuperabili: {summary.get('category_unrecoverable_files', 0)}\n\n"
                f"Anomalie tecniche ignorate in zone silenziose: {summary.get('ignored_silent_anomalies', 0)}\n"
                f"Totale analizzati: {summary.get('analyzed_files', 0)}"
            )
        else:
            base_message = (
                "Diagnostica completata.\n\n"
                "Controllo integrità MP3 eseguito: No\n"
                f"Totale analizzati: {summary.get('analyzed_files', 0)}"
            )

        if winlive_summary["verified"] > 0:
            base_message += (
                "\n\n"
                f"WinLive verificati: {winlive_summary['verified']}\n"
                f"WinLive normalizzati: {winlive_summary['normalized']}\n"
                f"Errori WinLive: {winlive_summary['errors']}"
            )

        self._update_controls_state()
        messagebox.showinfo(title, base_message, parent=self._diagnostics_dialog_parent())
        self._diagnostics_session_snapshot = None
        self.diagnostics_eta_label.configure(text="Tempo stimato restante: completato")
        self._raise_diagnostics_window()

    @staticmethod
    def _build_winlive_runtime_summary(diagnostic_results: Any) -> dict[str, int]:
        verified = 0
        normalized = 0
        errors = 0
        for item in diagnostic_results if isinstance(diagnostic_results, list) else []:
            winlive = getattr(item, "winlive", None)
            if winlive is None or not bool(getattr(winlive, "verifica_winlive_eseguita", False)):
                continue
            verified += 1

            outcome = getattr(winlive, "stato_winlive_finale", None)
            outcome_value = getattr(outcome, "value", "") if outcome is not None else ""
            if bool(getattr(winlive, "normalizzazione_validata", False)) or outcome_value == "NORMALIZZATO":
                normalized += 1

            error_text = str(getattr(winlive, "errore_winlive", "") or "").strip()
            error_code = str(getattr(winlive, "errore_winlive_code", "") or "").strip()
            if error_text or error_code:
                errors += 1

        return {
            "verified": verified,
            "normalized": normalized,
            "errors": errors,
        }

    def _append_winlive_completion_logs(self, diagnostic_results: Any, winlive_summary: dict[str, int]) -> None:
        if not isinstance(diagnostic_results, list):
            return
        if winlive_summary.get("verified", 0) <= 0:
            return

        self._append_diagnostics_log("Verifica WinLive completata.")
        for item in diagnostic_results:
            winlive = getattr(item, "winlive", None)
            if winlive is None or not bool(getattr(winlive, "verifica_winlive_eseguita", False)):
                continue

            file_name = str(getattr(item, "file_name", "") or "")
            outcome = getattr(winlive, "stato_winlive_finale", None)
            outcome_text = str(getattr(outcome, "value", "") or "").strip()
            if outcome_text:
                prefix = f"{file_name}: " if file_name else ""
                self._append_diagnostics_log(f"{prefix}{outcome_text}")

        self._append_diagnostics_log(
            f"WinLive verificati: {int(winlive_summary.get('verified', 0))}"
        )
        self._append_diagnostics_log(
            f"WinLive normalizzati: {int(winlive_summary.get('normalized', 0))}"
        )
        self._append_diagnostics_log(
            f"Errori WinLive: {int(winlive_summary.get('errors', 0))}"
        )

    def _diagnostics_worker_error(self, message: str) -> None:
        self.after(0, self._handle_diagnostics_worker_error, message)

    def _handle_diagnostics_worker_error(self, message: str) -> None:
        self._stop_diagnostics_timer()
        self.diagnostics_status_label.configure(text="Errore diagnostica MP3")
        self.diagnostics_eta_label.configure(text="Tempo stimato restante: non disponibile")
        self._append_diagnostics_log(f"ERRORE: {message}")
        self._diagnostics_session_snapshot = None
        self._update_controls_state()
        messagebox.showerror("Diagnostica MP3", message, parent=self._diagnostics_dialog_parent())
        self._raise_diagnostics_window()

    def _diagnostics_worker_cancelled(self, message: str) -> None:
        self.after(0, self._handle_diagnostics_worker_cancelled, message)

    def _handle_diagnostics_worker_cancelled(self, message: str) -> None:
        self._stop_diagnostics_timer()
        self.diagnostics_status_label.configure(text="Diagnostica MP3 interrotta")
        self.diagnostics_eta_label.configure(text="Tempo stimato restante: annullato")
        self._append_diagnostics_log(message)
        self._diagnostics_session_snapshot = None
        self._update_controls_state()

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
        self.diagnostics_timer_job = self.after(750, self._tick_diagnostics_timer)

    def _update_diagnostics_eta(self) -> None:
        if self.diagnostics_worker_start_time is None:
            self.diagnostics_eta_label.configure(text="Tempo stimato restante: --")
            return

        total = self.diagnostics_worker_total
        progress = self.diagnostics_last_progress
        if total <= 0:
            self.diagnostics_eta_label.configure(text="Tempo stimato restante: --")
            return

        remaining_text = self.diagnostics_eta_estimator.format_remaining()
        if remaining_text == "calcolo in corso...":
            if progress <= 0:
                remaining = max(0.0, float(total) * 8.0)
            else:
                elapsed = max(0.0, time.monotonic() - self.diagnostics_worker_start_time)
                avg_per_file = elapsed / float(progress)
                remaining_files = max(0, total - progress)
                remaining = max(0.0, avg_per_file * float(remaining_files))

            if remaining < 60:
                remaining_text = f"{int(round(remaining))} secondi"
            elif remaining >= 3600:
                remaining_text = self._format_duration(remaining)
            else:
                minutes = int(remaining // 60)
                seconds = int(round(remaining % 60))
                if seconds == 60:
                    minutes += 1
                    seconds = 0
                remaining_text = f"{minutes:02d}:{seconds:02d}"

        self.diagnostics_eta_label.configure(text=f"Tempo stimato restante: {remaining_text}")

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
            normalized_name = self._normalize_relative_mp3_path(file_name)
            if normalized_name:
                by_name[normalized_name] = item
                by_name[self._path_compare_key(normalized_name)] = item
            base_name = Path(file_name).name
            if base_name:
                by_name[base_name] = item
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

        def _add_candidates(root_text: str) -> None:
            root = Path(root_text).expanduser()
            candidates.append(root / "REPORT" / "IntegrityIndex.json")
            candidates.append(root / "Report" / "IntegrityIndex.json")
            for session_dir in sorted(root.glob("Diagnostica_MP3_*")):
                if not session_dir.is_dir():
                    continue
                candidates.append(session_dir / "REPORT" / "IntegrityIndex.json")
                candidates.append(session_dir / "Report" / "IntegrityIndex.json")

        output_folder = (preferred_output_folder or "").strip()
        if output_folder:
            _add_candidates(output_folder)

        if hasattr(self, "diagnostics_output_entry"):
            entry_folder = self.diagnostics_output_entry.get().strip()
            if entry_folder:
                _add_candidates(entry_folder)

        if hasattr(self, "output_entry"):
            generic_output = self.output_entry.get().strip()
            if generic_output:
                _add_candidates(generic_output)

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

        normalized_name = self._normalize_relative_mp3_path(file_name)
        if normalized_name and normalized_name in self._diagnostics_integrity_by_file:
            return self._diagnostics_integrity_by_file[normalized_name]

        name_key = self._path_compare_key(normalized_name or file_name)
        if name_key in self._diagnostics_integrity_by_file:
            return self._diagnostics_integrity_by_file[name_key]

        base_name = Path(file_name).name
        if base_name in self._diagnostics_integrity_by_file:
            return self._diagnostics_integrity_by_file[base_name]

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
    def scan_mp3_files(folder_path: str | Path, *, include_subfolders: bool = False) -> list[str]:
        folder = Path(folder_path).expanduser()
        paths = scan_mp3_files(
            folder,
            include_subfolders=include_subfolders,
            exclude_diagnostics_sessions=True,
        )
        names: list[str] = []
        for path in paths:
            try:
                names.append(path.relative_to(folder).as_posix())
            except ValueError:
                names.append(path.name)
        return names

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

        include_subfolders = bool(self.mix_include_subfolders_var.get())
        try:
            disk_files = self.scan_mp3_files(folder, include_subfolders=include_subfolders)
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
            f"totale {self.track_count}; "
            f"sottocartelle={'ON' if include_subfolders else 'OFF'}."
        )

        if new_files or removed:
            self._mark_project_dirty()
        elif source_changed:
            self._mark_project_dirty()

        self._update_tracks_count()
        self._update_controls_state()

    def load_mp3_list(self, mark_dirty: bool = False) -> None:
        include_subfolders = bool(self.mix_include_subfolders_var.get())
        try:
            files = self.scan_mp3_files(self.input_folder, include_subfolders=include_subfolders)
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
        self._append_log(
            f"Trovati {len(files)} file MP3 "
            f"(sottocartelle={'ON' if include_subfolders else 'OFF'})."
        )
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

    @staticmethod
    def _format_short_file_list(items: list[str], limit: int = 10) -> str:
        if not items:
            return "- Nessuno"
        head = items[:limit]
        lines = [f"- {name}" for name in head]
        extra = len(items) - len(head)
        if extra > 0:
            lines.append(f"... e altri {extra} file")
        return "\n".join(lines)

    def _ask_unrecoverable_mix_action(self, files: list[str]) -> str:
        dialog = ctk.CTkToplevel(self)
        dialog.title("File potenzialmente non recuperabili")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        container = ctk.CTkFrame(dialog)
        container.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            container,
            text="Nell'elenco sono presenti file classificati come non recuperabili.",
            anchor="w",
            justify="left",
            wraplength=700,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            container,
            text=(
                "La loro inclusione potrebbe provocare errori, interruzioni o anomalie "
                "audio nel mix finale."
            ),
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(fill="x", pady=(0, 10))

        details_box = ctk.CTkTextbox(container, height=200, width=720)
        details_box.pack(fill="both", expand=True)
        details_box.insert("1.0", self._format_short_file_list(files, limit=10))
        details_box.configure(state="disabled")

        result = {"value": "cancel"}

        actions = ctk.CTkFrame(container, fg_color="transparent")
        actions.pack(fill="x", pady=(12, 0))
        actions.grid_columnconfigure((0, 1, 2), weight=1)

        def _set_and_close(value: str) -> None:
            result["value"] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        ctk.CTkButton(
            actions,
            text="Escludi i file e continua",
            command=lambda: _set_and_close("exclude"),
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        ctk.CTkButton(
            actions,
            text="Includi comunque",
            command=lambda: _set_and_close("include"),
        ).grid(row=0, column=1, padx=6, sticky="ew")

        ctk.CTkButton(
            actions,
            text="Annulla",
            fg_color="#8f3a3a",
            hover_color="#7c3232",
            command=lambda: _set_and_close("cancel"),
        ).grid(row=0, column=2, padx=(6, 0), sticky="ew")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _set_and_close("cancel"))
        self.wait_window(dialog)
        return str(result.get("value", "cancel"))

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
        self.mix_eta_phase = ""
        self.mix_eta_estimator.reset(total_units=0, initial_seconds_per_unit=8.0)

        self.create_button.configure(
            state="disabled",
            text="CREAZIONE IN CORSO..."
        )
        self.cancel_button.configure(state="normal")
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.counter_label.configure(text=f"0 / {self.track_count}")
        self.elapsed_label.configure(text="00:00:00")
        self.remaining_label.configure(text="Calcolo tempo stimato in corso...")
        self.status_label.configure(text="Avvio elaborazione...")
        self.status_bar_label.configure(text="Creazione mix in corso...")
        self._append_log("Avvio creazione mix.")

        reuse_enabled = bool(self.reuse_previous_clips_var.get()) and bool(self._reusable_previous_clips)
        if reuse_enabled:
            self._append_log("Utilizza clip generate in precedenza: attivo.")
        else:
            self._append_log("Utilizza clip generate in precedenza: disattivo.")

        unrecoverable_files = [
            file_name
            for file_name in self.ordered_track_names
            if self._is_track_unrecoverable(file_name)
        ]

        session_excluded_unrecoverable: set[str] = set()
        session_isolated_unrecoverable: set[str] = set()
        if unrecoverable_files:
            nr_action = self._ask_unrecoverable_mix_action(unrecoverable_files)
            if nr_action == "cancel":
                self._restore_after_work()
                self.status_bar_label.configure(text="Avvio mix annullato")
                self._append_log("Creazione mix annullata: presenza file non recuperabili.")
                return
            if nr_action == "exclude":
                session_excluded_unrecoverable = set(unrecoverable_files)
                self._append_log(
                    "Esclusione temporanea file non recuperabili per questa generazione: "
                    f"{len(session_excluded_unrecoverable)} file."
                )
            elif nr_action == "include":
                session_isolated_unrecoverable = set(unrecoverable_files)
                self._append_log(
                    "Inclusione autorizzata dall'utente per file non recuperabili: "
                    f"{len(unrecoverable_files)} file."
                )

        effective_track_names = self._effective_mix_track_names()
        if session_excluded_unrecoverable:
            effective_track_names = [
                name for name in effective_track_names
                if name not in session_excluded_unrecoverable
            ]

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
                isolated_input_names=sorted(session_isolated_unrecoverable) if session_isolated_unrecoverable else None,
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
            mix_start_ms = self._safe_int(item.get("mix_start_ms"))
            mix_end_ms = self._safe_int(item.get("mix_end_ms"))
            if mix_start_ms is None or mix_end_ms is None:
                continue

            start_ms = max(0, int(mix_start_ms))
            end_ms = max(start_ms, int(mix_end_ms))
            duration_ms = max(0, end_ms - start_ms)

            rows.append(
                {
                    "number": f"{index:03d}",
                    "title": Path(file_name).stem,
                    "from": self._format_hhmmss_from_ms(start_ms),
                    "to": self._format_hhmmss_from_ms(end_ms),
                    "duration": self._format_hhmmss_from_ms(duration_ms),
                }
            )
        return rows

    @staticmethod
    def _serialize_song_timeline(rows: list[dict[str, str]]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(["N", "Nome Song", "Da", "A", "Durata"])
        for row in rows:
            writer.writerow([
                row.get("number", ""),
                row.get("title", ""),
                row.get("from", "00:00:00"),
                row.get("to", "00:00:00"),
                row.get("duration", "00:00:00"),
            ])
        return buffer.getvalue()

    def _write_song_timeline_files(self, base_folder: Path, rows: list[dict[str, str]]) -> dict[str, str]:
        base_folder.mkdir(parents=True, exist_ok=True)
        csv_path = base_folder / "Elenco_Mix.csv"
        txt_path = base_folder / "Elenco_Mix.txt"

        content = self._serialize_song_timeline(rows)

        csv_path.write_text(content, encoding="utf-8-sig", newline="")
        txt_path.write_text(content, encoding="utf-8-sig", newline="")

        return {
            "csv_path": str(csv_path),
            "txt_path": str(txt_path),
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

        if not self._has_valid_last_mix_temporal_data():
            messagebox.showinfo(
                "Estrai Song",
                "Non sono disponibili i dati temporali dell'ultimo mix."
            )
            return

        tracks_data = self._get_ordered_new_mix_tracks()
        if not tracks_data:
            messagebox.showinfo("Estrai Song", "Non sono disponibili i dati temporali dell'ultimo mix.")
            return

        output_folder = self.output_entry.get().strip() if hasattr(self, "output_entry") else ""
        if not output_folder:
            output_folder = self.input_entry.get().strip() if hasattr(self, "input_entry") else ""
        if not output_folder:
            messagebox.showerror("Errore", "Cartella output non disponibile per esportare Elenco_Mix.csv")
            return

        rows = self._build_song_rows_from_temporal_tracks(tracks_data)
        if not rows:
            messagebox.showinfo("Estrai Song", "Non sono disponibili i dati temporali dell'ultimo mix.")
            return

        target_folder = Path(output_folder)
        try:
            exported_paths = self._write_song_timeline_files(target_folder, rows)
        except OSError as error:
            messagebox.showerror("Errore", f"Impossibile esportare Elenco_Mix:\n{error}")
            return

        self._append_log(
            "Esportazione Elenco_Mix completata: "
            f"{len(rows)} righe in {exported_paths['csv_path']}."
        )
        self.status_bar_label.configure(text="Elenco_Mix esportato")
        messagebox.showinfo(
            "Estrai Song",
            "Esportazione completata.\n\n"
            f"CSV: {exported_paths['csv_path']}\n"
            f"TXT: {exported_paths['txt_path']}\n"
            f"Righe esportate: {len(rows)}"
        )

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

        phase = "export" if message.startswith("Creazione mix") else "preparation"
        if phase != self.mix_eta_phase:
            self.mix_eta_phase = phase
            initial_seconds = 1.0 if phase == "export" else 8.0
            self.mix_eta_estimator.reset(total_units=max(0, int(total)), initial_seconds_per_unit=initial_seconds)

        if total > 0 and current > 0:
            sample_allowed = not message.startswith("Riutilizzo clip precedente")
            self.mix_eta_estimator.observe(current, total_units=total, sample_allowed=sample_allowed)

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
            750,
            self._tick_timer
        )

    def _update_remaining_time(self) -> None:
        if self.start_time is None:
            self.remaining_label.configure(text="--:--:--")
            return

        if self.last_progress_percent <= 0:
            self.remaining_label.configure(text="Calcolo tempo stimato in corso...")
            return

        remaining_text = self.mix_eta_estimator.format_remaining()
        if remaining_text == "calcolo in corso...":
            self.remaining_label.configure(text="Calcolo tempo stimato in corso...")
            return

        self.remaining_label.configure(text=remaining_text)

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
                "mix_include_subfolders": bool(self.mix_include_subfolders_var.get()),
                "diagnostics_verify_mp3_integrity": bool(self.diagnostics_verify_mp3_integrity_var.get()),
                "diagnostics_verify_winlive": bool(self.diagnostics_verify_winlive_var.get()),
                "diagnostics_placement_mode": str(self.diagnostics_placement_mode_var.get() or "copy").strip().lower(),
                "appearance_mode": self.appearance_combo.get()
            }
        )
        self.settings_manager.save(self.settings)

    def on_close(self, _shutdown_after_cancel: bool = False) -> None:
        if self.worker.is_running:
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "La creazione del mix è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            self.worker.cancel()
            self.after(150, lambda: self.on_close(True))
            return

        if self.extract_worker.is_running:
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "L'estrazione song è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            self.extract_worker.cancel()
            self.after(150, lambda: self.on_close(True))
            return

        if self.diagnostics_worker.is_running:
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "La diagnostica MP3 è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            self.diagnostics_worker.cancel()
            self.after(150, lambda: self.on_close(True))
            return

        if self.recovery_worker.is_running:
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "Il recupero MP3 è ancora in corso.\n"
                    "Vuoi interromperlo e chiudere il programma?"
                )
                if not confirm:
                    return
            self.recovery_worker.cancel()
            self.after(150, lambda: self.on_close(True))
            return

        if not self._confirm_save_if_dirty():
            return

        self.save_settings()
        self.destroy()
