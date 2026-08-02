# -*- coding: utf-8 -*-
"""MixCreator PRO
gui.py - Versione 2.8
Patch 1.3.05

Novità:
- tempo trascorso
- barra di stato
- numero build visibile
"""

from __future__ import annotations

import time
import threading
import csv
import json
import os
import io
import subprocess
from datetime import datetime
from pathlib import Path
from tkinter import END, SINGLE, filedialog, messagebox, ttk
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
    save_project as save_project_file,
)
from settings import SettingsManager
from tooltip import Tooltip
from utils import AdaptiveTimeEstimator, scan_mp3_files
from worker import (
    MixWorker,
    SongExtractionWorker,
    MP3DiagnosticsWorker,
    MP3RecoveryWorker,
    MP3RepertoryDiagnosticsWorker,
    MP3RepertoryNewTracksWorker,
    MP3RepertoryOrganizerWorker,
)
from mp3_repertory_new_tracks import (
    NewTrackItem,
    NewTracksAssignmentModel,
    RepertoryFolderItem,
    STATUS_DA_GESTIRE,
    STATUS_GESTITO,
    ensure_folder_available,
    format_size_megabytes,
    list_new_tracks_non_recursive,
    scan_repertory_folders_non_recursive_stats,
)
from mp3_repertory_new_tracks_update import (
    DECISION_SKIP_AND_BYPASS_SESSION as REP003_DECISION_SKIP_AND_BYPASS_SESSION,
    DECISION_SKIP_CURRENT as REP003_DECISION_SKIP_CURRENT,
    DECISION_UPDATE_AND_BYPASS_SESSION as REP003_DECISION_UPDATE_AND_BYPASS_SESSION,
    DECISION_UPDATE_CURRENT as REP003_DECISION_UPDATE_CURRENT,
    Rep003UpdateResult,
)
from mp3_recovery_batch import MP3BatchOutcome
from mp3_recovery import RecoveryMode
from mp3_repertory_organizer import (
    COUNTER_BRANI_AGGIORNATI,
    COUNTER_BRANI_DA_INSERIRE,
    COUNTER_BRANI_DA_INSERIRE_ERRORI,
    COUNTER_COPIE_AGGIORNATE_REPERTORIO,
    COUNTER_FILE_MANTENUTI,
    COUNTER_FILE_NON_TROVATI_COPIATI,
    COUNTER_FILE_NON_TROVATI_ERRORI_COPIA,
    COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO,
    COUNTER_SMARTPHONE_TABLET_COPIATI,
    COUNTER_SMARTPHONE_TABLET_ERRORI,
    RepertoryStatus,
    SMARTPHONE_TABLET_ROOT,
    assert_smartphone_tablet_dir_accessible,
    reset_smartphone_tablet_dir,
)
from mp3_diagnostics import (
    STATUS_PERFECT,
    STATUS_REPAIRED,
    STATUS_UNRECOVERABLE,
)
from mp3_repertory_diagnostics import enumerate_split_repertory_nodes
from mp3_repertory_diagnostics import ROOT_FILES_TOKEN


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
    "Brano intero": "intero",
}

CUT_MODE_VALUES = {value: key for key, value in CUT_MODE_LABELS.items()}

REPERTORY_MODE_UPDATE = "update"
REPERTORY_MODE_DIAGNOSTICS = "diagnostics"
REPERTORY_MODE_INSERT_TRACKS = "insert_tracks"
REPERTORY_MODE_LABELS = {
    REPERTORY_MODE_UPDATE: "Aggiornamento Repertorio",
    REPERTORY_MODE_DIAGNOSTICS: "Diagnosi Repertorio",
    REPERTORY_MODE_INSERT_TRACKS: "Inserimento nuovi brani",
}
REPERTORY_MODE_BY_LABEL = {
    label.casefold(): mode
    for mode, label in REPERTORY_MODE_LABELS.items()
}

REP003_SORT_NAME = "name"
REP003_SORT_STATUS = "status"
REP003_SORT_FOLDERS = "folders"
REP003_SORT_FOLDER_NAME = "folder"
REP003_SORT_FOLDER_RELATIVE = "relative"
REP003_SORT_FOLDER_COUNT = "count"
REP003_SORT_FOLDER_SIZE = "size"

REP003_INVALID_FOLDER_CHARS = set('<>:"/\\|?*')
REP003_RESERVED_FOLDER_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
REP003_BLOCKED_TECHNICAL_FOLDER_NAMES = {
    "diagnosi",
    "report",
    "log",
    "file non trovati in repertorio",
    "file non trovati nel repertorio",
    "repertorio_generale_da_mixcreator",
}

REP003_SESSION_NOT_LOADED = "NOT_LOADED"
REP003_SESSION_LOADED_UNASSIGNED = "LOADED_UNASSIGNED"
REP003_SESSION_ASSIGNMENTS_PENDING = "ASSIGNMENTS_PENDING"
REP003_SESSION_PROCESSING = "PROCESSING"
REP003_SESSION_COMPLETED = "COMPLETED"
REP003_SESSION_COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
REP003_SESSION_READY_FOR_NEW_SESSION = "READY_FOR_NEW_SESSION"


class ManagedCTkToplevel(ctk.CTkToplevel):
    def __init__(self, *args, **kwargs):
        self._managed_after_jobs: set[str] = set()
        self._managed_destroying = False
        super().__init__(*args, **kwargs)

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        if self._managed_destroying:
            return None

        job_id = None

        def _runner() -> None:
            nonlocal job_id
            if job_id is not None:
                self._managed_after_jobs.discard(job_id)
                job_id = None
            if self._managed_destroying:
                return
            func(*args)

        try:
            job_id = super().after(ms, _runner)
        except (tk.TclError, RuntimeError):
            return None
        self._managed_after_jobs.add(job_id)
        return job_id

    def after_idle(self, func, *args):
        if self._managed_destroying:
            return None

        job_id = None

        def _runner() -> None:
            nonlocal job_id
            if job_id is not None:
                self._managed_after_jobs.discard(job_id)
                job_id = None
            if self._managed_destroying:
                return
            func(*args)

        try:
            job_id = super().after_idle(_runner)
        except (tk.TclError, RuntimeError):
            return None
        self._managed_after_jobs.add(job_id)
        return job_id

    def after_cancel(self, identifier):
        if identifier is not None:
            self._managed_after_jobs.discard(identifier)
        return super().after_cancel(identifier)

    def _cancel_managed_after_jobs(self) -> None:
        pending_jobs = list(self._managed_after_jobs)
        self._managed_after_jobs.clear()
        for job_id in pending_jobs:
            try:
                self.tk.call("after", "cancel", job_id)
            except (tk.TclError, RuntimeError):
                pass

    def _cancel_matching_after_scripts(self, *fragments: str) -> None:
        try:
            pending_jobs = self.tk.splitlist(self.tk.call("after", "info"))
        except (tk.TclError, RuntimeError):
            return

        for job_id in pending_jobs:
            try:
                callback_info = self.tk.splitlist(self.tk.call("after", "info", job_id))
            except (tk.TclError, RuntimeError):
                continue
            callback_text = " ".join(str(item) for item in callback_info)
            if any(fragment in callback_text for fragment in fragments):
                try:
                    self.tk.call("after", "cancel", job_id)
                except (tk.TclError, RuntimeError):
                    pass

    def destroy(self):
        if self._managed_destroying:
            return
        self._managed_destroying = True
        self._cancel_matching_after_scripts(
            "<lambda>",
            "_windows_set_titlebar_icon",
            "_revert_withdraw_after_windows_set_titlebar_color",
            "focus_set",
        )
        self._cancel_managed_after_jobs()
        super().destroy()


class ManagedCTkTextbox(ctk.CTkTextbox):
    def __init__(self, *args, **kwargs):
        self._managed_after_jobs: set[str] = set()
        self._managed_destroying = False
        super().__init__(*args, **kwargs)

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        if self._managed_destroying:
            return None

        job_id = None

        def _runner() -> None:
            nonlocal job_id
            if job_id is not None:
                self._managed_after_jobs.discard(job_id)
                job_id = None
            if self._managed_destroying:
                return
            func(*args)

        try:
            job_id = super().after(ms, _runner)
        except (tk.TclError, RuntimeError):
            return None
        self._managed_after_jobs.add(job_id)
        return job_id

    def after_idle(self, func, *args):
        if self._managed_destroying:
            return None

        job_id = None

        def _runner() -> None:
            nonlocal job_id
            if job_id is not None:
                self._managed_after_jobs.discard(job_id)
                job_id = None
            if self._managed_destroying:
                return
            func(*args)

        try:
            job_id = super().after_idle(_runner)
        except (tk.TclError, RuntimeError):
            return None
        self._managed_after_jobs.add(job_id)
        return job_id

    def after_cancel(self, identifier):
        if identifier is not None:
            self._managed_after_jobs.discard(identifier)
        return super().after_cancel(identifier)

    def _cancel_managed_after_jobs(self) -> None:
        pending_jobs = list(self._managed_after_jobs)
        self._managed_after_jobs.clear()
        for job_id in pending_jobs:
            try:
                self.tk.call("after", "cancel", job_id)
            except (tk.TclError, RuntimeError):
                pass

    def _cancel_matching_after_scripts(self, *fragments: str) -> None:
        try:
            pending_jobs = self.tk.splitlist(self.tk.call("after", "info"))
        except (tk.TclError, RuntimeError):
            return

        for job_id in pending_jobs:
            try:
                callback_info = self.tk.splitlist(self.tk.call("after", "info", job_id))
            except (tk.TclError, RuntimeError):
                continue
            callback_text = " ".join(str(item) for item in callback_info)
            if any(fragment in callback_text for fragment in fragments):
                try:
                    self.tk.call("after", "cancel", job_id)
                except (tk.TclError, RuntimeError):
                    pass

    def destroy(self):
        if self._managed_destroying:
            return
        self._managed_destroying = True
        self._cancel_matching_after_scripts("<lambda>", "_check_if_scrollbars_needed")
        self._cancel_managed_after_jobs()
        super().destroy()


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
        self._tracked_after_jobs: set[str] = set()
        self._shutdown_after_job = None
        self._is_destroying = False
        self._destroy_completed = False
        self.timer_job = None
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
        self.repertory_worker = MP3RepertoryOrganizerWorker(
            on_progress=self._repertory_worker_progress,
            on_completed=self._repertory_worker_completed,
            on_error=self._repertory_worker_error,
            on_cancelled=self._repertory_worker_cancelled,
            on_log=self._repertory_worker_log,
            on_decision_required=self._repertory_worker_decision_required,
        )
        self.repertory_diagnostics_worker = MP3RepertoryDiagnosticsWorker(
            on_progress=self._repertory_diagnostics_worker_progress,
            on_completed=self._repertory_diagnostics_worker_completed,
            on_error=self._repertory_diagnostics_worker_error,
            on_cancelled=self._repertory_diagnostics_worker_cancelled,
            on_log=self._repertory_diagnostics_worker_log,
        )
        self._repertory_dialog: ctk.CTkToplevel | None = None
        self._repertory_general_label = None
        self._repertory_updates_entry = None
        self._repertory_library_entry = None
        self._repertory_general_entry = None
        self._repertory_results_entry = None
        self._repertory_smartphone_label = None
        self._repertory_smartphone_entry = None
        self._repertory_smartphone_browse_button = None
        self._repertory_backup_var = tk.BooleanVar(value=bool(self.settings.get("repertory_backup_enabled", True)))
        self._repertory_status_label = None
        self._repertory_log_box = None
        self._repertory_progress_bar = None
        self._repertory_start_button = None
        self._repertory_stop_button = None
        self._repertory_close_button = None
        self._repertory_open_results_button = None
        self._repertory_open_smartphone_button = None
        self._repertory_reset_smartphone_button = None
        self._repertory_general_browse_button = None
        self._repertory_path_widgets: list[Any] = []
        self._repertory_session_folder: str | None = None
        self._repertory_result_folder_update: str | None = None
        self._repertory_result_folder_diagnostics: str | None = None
        self._repertory_result_folder_insert_tracks: str | None = None
        self._repertory_selected_smartphone_folder: str | None = None
        self._repertory_last_completed_smartphone_folder: str | None = None
        self._repertory_allow_session_log_updates = False
        self._repertory_expected_output_root = ""
        self._repertory_min_session_timestamp = ""
        self._repertory_total_files = 0
        self._repertory_processed_files = 0
        self._repertory_matches_found = 0
        self._repertory_files_updated = 0
        self._repertory_files_not_found = 0
        self._repertory_errors = 0
        self._repertory_started_at: float | None = None
        self._repertory_timer_job = None
        self._repertory_file_counter_label = None
        self._repertory_matches_label = None
        self._repertory_updated_label = None
        self._repertory_not_found_label = None
        self._repertory_errors_label = None
        self._repertory_elapsed_label = None
        self._repertory_eta_label = None
        self._repertory_decision_dialog = None
        self._repertory_decision_tooltips: list[Tooltip] = []
        self._repertory_updates_entry_tooltip: Tooltip | None = None
        self._repertory_library_entry_tooltip: Tooltip | None = None
        self._repertory_general_entry_tooltip: Tooltip | None = None
        self._repertory_results_entry_tooltip: Tooltip | None = None
        self._repertory_smartphone_entry_tooltip: Tooltip | None = None
        self._repertory_start_button_tooltip: Tooltip | None = None
        self._repertory_stop_button_tooltip: Tooltip | None = None
        self._repertory_open_results_button_tooltip: Tooltip | None = None
        self._repertory_open_smartphone_button_tooltip: Tooltip | None = None
        self._repertory_reset_smartphone_button_tooltip: Tooltip | None = None
        self._repertory_backup_check_tooltip: Tooltip | None = None
        self._repertory_diagnostics_refresh_tooltip: Tooltip | None = None
        self._repertory_diagnostics_select_all_tooltip: Tooltip | None = None
        self._repertory_diagnostics_deselect_all_tooltip: Tooltip | None = None
        self._rep003_new_tracks_entry_tooltip: Tooltip | None = None
        self._rep003_split_entry_tooltip: Tooltip | None = None
        self._rep003_general_entry_tooltip: Tooltip | None = None
        self._rep003_smartphone_entry_tooltip: Tooltip | None = None
        self._rep003_load_button_tooltip: Tooltip | None = None
        self._rep003_show_managed_tooltip: Tooltip | None = None
        self._rep003_create_folder_tooltip: Tooltip | None = None
        self._rep003_refresh_folders_tooltip: Tooltip | None = None
        self._rep003_assign_button_tooltip: Tooltip | None = None
        self._rep003_remove_button_tooltip: Tooltip | None = None
        self._repertory_pending_decision_request_id: str | None = None
        self._repertory_mtime_bypass_active = False
        self._repertory_mtime_session_choice = "ASK"
        self._repertory_smartphone_root = str(
            self.settings.get("repertory_smartphone_folder", "")
            or SMARTPHONE_TABLET_ROOT
        )
        self._repertory_reset_in_progress = False
        self._repertory_close_requested = False
        self._repertory_mode_var = tk.StringVar(value=REPERTORY_MODE_UPDATE)
        self._repertory_mode_label_var = tk.StringVar(value=REPERTORY_MODE_LABELS[REPERTORY_MODE_UPDATE])
        self._repertory_mode_selector = None
        self._repertory_mode_radios: list[Any] = []
        self._repertory_mode_frame = None
        self._repertory_diagnostics_tree_scrollable = None
        self._repertory_diagnostics_refresh_button = None
        self._repertory_diagnostics_select_all_button = None
        self._repertory_diagnostics_deselect_all_button = None
        self._repertory_diagnostics_tree_items: dict[str, dict[str, Any]] = {}
        self._repertory_diagnostics_tree_order: list[str] = []
        self._repertory_diagnostics_tree_widgets: list[Any] = []
        self._rep003_model = NewTracksAssignmentModel()
        self.rep003_worker = MP3RepertoryNewTracksWorker(
            on_progress=self._rep003_worker_progress,
            on_completed=self._rep003_worker_completed,
            on_error=self._rep003_worker_error,
            on_cancelled=self._rep003_worker_cancelled,
            on_log=self._rep003_worker_log,
            on_decision_required=self._rep003_worker_decision_required,
        )
        self._rep003_window: ctk.CTkToplevel | None = None
        self._rep003_panel_frame = None
        self._rep003_tracks_tree = None
        self._rep003_folders_tree = None
        self._rep003_new_tracks_entry = None
        self._rep003_split_entry = None
        self._rep003_general_entry = None
        self._rep003_smartphone_entry = None
        self._rep003_status_label = None
        self._rep003_load_button = None
        self._rep003_create_folder_button = None
        self._rep003_refresh_folders_button = None
        self._rep003_assign_button = None
        self._rep003_remove_button = None
        self._rep003_show_managed_switch = None
        self._rep003_browse_buttons: list[Any] = []
        self._rep003_path_widgets: list[Any] = []
        self._rep003_tracks_h_scroll = None
        self._rep003_folders_h_scroll = None
        self._rep003_ttk_style_configured = False
        self._rep003_show_managed_var = tk.BooleanVar(value=True)
        self._rep003_sort_key = REP003_SORT_NAME
        self._rep003_sort_reverse = False
        self._rep003_track_row_by_iid: dict[str, str] = {}
        self._rep003_folder_iid_by_relative: dict[str, str] = {}
        self._rep003_folder_sort_key = REP003_SORT_FOLDER_RELATIVE
        self._rep003_folder_sort_reverse = False
        self._rep003_pending_decision_request_id: str | None = None
        self._rep003_decision_dialog = None
        self._rep003_create_folder_dialog = None
        self._rep003_create_folder_entry = None
        self._rep003_create_folder_focus_after_id = None
        self._rep003_create_folder_preview_var = tk.StringVar(value="")
        self._rep003_session_policy = "ASK"
        self._rep003_session_state = REP003_SESSION_NOT_LOADED
        self._rep003_last_processed_sources: set[str] = set()
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
        self.start_time: float | None = None
        self.last_progress_percent = 0
        self.mix_eta_estimator = AdaptiveTimeEstimator(initial_seconds_per_unit=8.0)
        self.mix_eta_phase = ""

        self.worker = MixWorker(
            on_progress=self._worker_progress,
            on_completed=self._worker_completed,
            on_error=self._worker_error,
            on_cancelled=self._worker_cancelled,
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

        self._configure_main_window_geometry()

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        project_bar = ctk.CTkFrame(self)
        project_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        project_bar.grid_columnconfigure(7, weight=1)

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

        self.organize_repertory_button = ctk.CTkButton(
            project_bar,
            text="Organizza repertorio",
            width=170,
            command=self.open_repertory_organizer_window,
        )
        self.organize_repertory_button.grid(row=0, column=6, padx=(8, 0), sticky="w")
        self._add_tooltip(
            self.organize_repertory_button,
            "Aggiorna in blocco i file del repertorio usando una cartella aggiornamenti con confronto per nome normalizzato.",
        )

        self.project_status_label = ctk.CTkLabel(
            project_bar,
            text="Progetto: Nessuno",
            anchor="e"
        )
        self.project_status_label.grid(row=0, column=7, padx=(12, 0), sticky="ew")

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
            self._repertory_backup_var.set(bool(self.settings.get("repertory_backup_enabled", True)))

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
        is_mix_running = bool(getattr(getattr(self, "worker", None), "is_running", False))
        is_extract_running = bool(getattr(getattr(self, "extract_worker", None), "is_running", False))
        is_diag_running = bool(getattr(getattr(self, "diagnostics_worker", None), "is_running", False))
        is_repertory_running = bool(getattr(getattr(self, "repertory_worker", None), "is_running", False))
        is_repertory_diagnostics_running = bool(getattr(getattr(self, "repertory_diagnostics_worker", None), "is_running", False))
        is_rep003_running = bool(getattr(getattr(self, "rep003_worker", None), "is_running", False))
        is_busy = is_mix_running or is_extract_running or is_diag_running or is_repertory_running or is_repertory_diagnostics_running or is_rep003_running

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

        if hasattr(self, "organize_repertory_button"):
            self.organize_repertory_button.configure(
                state="disabled" if is_repertory_running or is_repertory_diagnostics_running else "normal"
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
            self._repertory_backup_var.set(bool(self.settings.get("repertory_backup_enabled", True)))

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

    def _select_repertory_updates_folder(self) -> None:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        title = (
            "Seleziona la cartella del Repertorio suddiviso da controllare"
            if mode == REPERTORY_MODE_DIAGNOSTICS
            else "Seleziona la cartella contenente i file da aggiornare"
        )
        selected = filedialog.askdirectory(
            title=title,
            parent=self._repertory_dialog,
        )
        if selected and self._repertory_updates_entry is not None:
            self._replace_entry(self._repertory_updates_entry, selected)
            self._validate_repertory_diagnostics_paths(show_message=True)
            self._refresh_repertory_diagnostics_folder_tree()
            self._update_repertory_primary_action_state()

    def _select_repertory_library_folder(self) -> None:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        title = (
            "Seleziona la cartella del Repertorio generale da confrontare"
            if mode == REPERTORY_MODE_DIAGNOSTICS
            else "Seleziona la cartella del Repertorio suddiviso"
        )
        selected = filedialog.askdirectory(
            title=title,
            parent=self._repertory_dialog,
        )
        if selected and self._repertory_library_entry is not None:
            self._replace_entry(self._repertory_library_entry, selected)
            self._validate_repertory_diagnostics_paths(show_message=True)
            self._refresh_repertory_diagnostics_folder_tree()
            self._update_repertory_primary_action_state()

    def _select_repertory_general_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella del Repertorio generale",
            parent=self._repertory_dialog,
        )
        if selected and self._repertory_general_entry is not None:
            self._replace_entry(self._repertory_general_entry, selected)
            self._update_repertory_primary_action_state()

    def _select_repertory_results_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella dei risultati",
            parent=self._repertory_dialog,
        )
        if selected and self._repertory_results_entry is not None:
            self._replace_entry(self._repertory_results_entry, selected)
            self._update_repertory_primary_action_state()

    def _select_repertory_smartphone_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Seleziona la cartella per dispositivo Android",
            parent=self._repertory_dialog,
        )
        if selected and self._repertory_smartphone_entry is not None:
            resolved = str(Path(selected).expanduser().resolve())
            self._replace_entry(self._repertory_smartphone_entry, resolved)
            self._repertory_smartphone_root = resolved
            self._repertory_selected_smartphone_folder = resolved
            self._repertory_last_completed_smartphone_folder = None
            self._update_repertory_android_buttons_state()
            self._update_repertory_primary_action_state()

    def _open_repertory_results_folder(self) -> None:
        target = self._active_repertory_results_folder()
        if not target:
            return
        path = Path(target)
        if not path.exists():
            self._set_repertory_results_folder_for_mode(self._normalize_repertory_mode(self._repertory_mode_var.get()), "")
            self._update_repertory_open_results_button_state()
            messagebox.showwarning("Organizza repertorio", f"Cartella non trovata:\n{path}", parent=self._repertory_dialog)
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:
            messagebox.showerror("Organizza repertorio", f"Impossibile aprire la cartella risultati:\n{error}", parent=self._repertory_dialog)

    def _set_repertory_results_folder_for_mode(self, mode: str, folder_path: str | None) -> None:
        normalized_mode = self._normalize_repertory_mode(mode)
        cleaned = str(folder_path or "").strip() or None
        if normalized_mode == REPERTORY_MODE_UPDATE:
            self._repertory_result_folder_update = cleaned
            return
        if normalized_mode == REPERTORY_MODE_DIAGNOSTICS:
            self._repertory_result_folder_diagnostics = cleaned
            return
        self._repertory_result_folder_insert_tracks = cleaned

    def _active_repertory_results_folder(self) -> str:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode == REPERTORY_MODE_UPDATE:
            return str(self._repertory_result_folder_update or "").strip()
        if mode == REPERTORY_MODE_DIAGNOSTICS:
            return str(self._repertory_result_folder_diagnostics or "").strip()
        return str(self._repertory_result_folder_insert_tracks or "").strip()

    @staticmethod
    def _is_existing_directory(folder_path: str) -> bool:
        candidate = str(folder_path or "").strip()
        if not candidate:
            return False
        try:
            path = Path(candidate).expanduser()
        except Exception:
            return False
        return path.exists() and path.is_dir()

    def _update_repertory_open_results_button_state(self) -> None:
        if self._repertory_open_results_button is None:
            return

        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        diagnostics_mode = mode == REPERTORY_MODE_DIAGNOSTICS
        insert_tracks_mode = mode == REPERTORY_MODE_INSERT_TRACKS
        self._repertory_open_results_button.configure(
            text="Apri cartella Diagnosi" if diagnostics_mode else "Apri cartella risultati"
        )

        if self._repertory_dialog is None:
            self._repertory_open_results_button.configure(state="disabled")
            return
        try:
            if not bool(self._repertory_dialog.winfo_exists()):
                self._repertory_open_results_button.configure(state="disabled")
                return
        except Exception:
            self._repertory_open_results_button.configure(state="disabled")
            return

        active_results_folder = self._active_repertory_results_folder()
        if self._is_any_repertory_worker_running() or not active_results_folder:
            self._repertory_open_results_button.configure(state="disabled")
            return

        if insert_tracks_mode:
            if self._rep003_session_state not in {
                REP003_SESSION_COMPLETED,
                REP003_SESSION_COMPLETED_WITH_ERRORS,
                REP003_SESSION_READY_FOR_NEW_SESSION,
            }:
                self._repertory_open_results_button.configure(state="disabled")
                return
            if not self._is_existing_directory(active_results_folder):
                self._set_repertory_results_folder_for_mode(REPERTORY_MODE_INSERT_TRACKS, "")
                self._repertory_open_results_button.configure(state="disabled")
                return

        self._repertory_open_results_button.configure(
            state="normal" if active_results_folder else "disabled"
        )

    def _get_repertory_smartphone_root(self) -> Path:
        if self._repertory_smartphone_entry is not None:
            candidate = self._repertory_smartphone_entry.get().strip()
            if candidate:
                return Path(candidate).expanduser().resolve()
        return Path(self._repertory_smartphone_root).expanduser().resolve()

    def _active_repertory_smartphone_folder(self) -> str:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode not in {REPERTORY_MODE_UPDATE, REPERTORY_MODE_INSERT_TRACKS}:
            return ""
        selected = str(self._repertory_selected_smartphone_folder or "").strip()
        completed = str(self._repertory_last_completed_smartphone_folder or "").strip()
        if not selected or not completed:
            return ""
        if self._canonical_path_for_compare(selected) != self._canonical_path_for_compare(completed):
            return ""
        entry_widget = self._repertory_smartphone_entry
        if mode == REPERTORY_MODE_INSERT_TRACKS and self._rep003_smartphone_entry is not None:
            entry_widget = self._rep003_smartphone_entry
        if entry_widget is None:
            return ""
        current_entry = str(entry_widget.get() or "").strip()
        if not current_entry:
            return ""
        if self._canonical_path_for_compare(current_entry) != self._canonical_path_for_compare(selected):
            return ""
        return completed

    def _is_valid_repertory_smartphone_destination(self, folder_path: str) -> bool:
        if not folder_path:
            return False
        try:
            target = Path(folder_path).expanduser().resolve()
        except Exception:
            return False
        try:
            assert_smartphone_tablet_dir_accessible(target, require_exists=True)
        except RuntimeError:
            return False
        return True

    def _update_repertory_android_buttons_state(self) -> None:
        if self._repertory_open_smartphone_button is None and self._repertory_reset_smartphone_button is None:
            return

        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        running = self._is_any_repertory_worker_running()
        active_folder = self._active_repertory_smartphone_folder()
        can_use = (
            mode in {REPERTORY_MODE_UPDATE, REPERTORY_MODE_INSERT_TRACKS}
            and not running
            and not self._repertory_reset_in_progress
            and self._is_valid_repertory_smartphone_destination(active_folder)
        )

        if self._repertory_open_smartphone_button is not None:
            self._repertory_open_smartphone_button.configure(state="normal" if can_use else "disabled")

        can_reset = False
        if can_use:
            try:
                target = Path(active_folder).expanduser().resolve()
                can_reset, _ = self._validate_repertory_smartphone_reset_target(target)
            except Exception:
                can_reset = False
        if self._repertory_reset_smartphone_button is not None:
            self._repertory_reset_smartphone_button.configure(state="normal" if can_reset else "disabled")

    @staticmethod
    def _is_filesystem_root(path: Path) -> bool:
        return path.parent == path

    def _validate_repertory_smartphone_reset_target(self, target: Path) -> tuple[bool, str]:
        if not str(target).strip():
            return False, "Cartella Smartphone/Tablet non valida: percorso vuoto."
        if not target.exists() or not target.is_dir():
            return False, "Cartella Smartphone/Tablet non valida: il percorso selezionato non esiste o non e una cartella."
        if self._is_filesystem_root(target):
            return False, "Operazione annullata: il reset della root del disco non e consentito."

        disallowed_roots: list[Path] = []
        for entry in (
            self._repertory_updates_entry,
            self._repertory_library_entry,
            self._repertory_general_entry,
            self._rep003_new_tracks_entry,
            self._rep003_split_entry,
            self._rep003_general_entry,
        ):
            if entry is None:
                continue
            raw = str(entry.get() or "").strip()
            if not raw:
                continue
            try:
                disallowed_roots.append(Path(raw).expanduser().resolve())
            except Exception:
                continue

        for disallowed in disallowed_roots:
            if target == disallowed:
                return (
                    False,
                    "Operazione annullata: la cartella Smartphone/Tablet coincide con un percorso sorgente o repertorio.",
                )
        session_folder = str(self._repertory_session_folder or "").strip()
        if session_folder:
            try:
                if target == Path(session_folder).expanduser().resolve():
                    return (
                        False,
                        "Operazione annullata: la cartella Smartphone/Tablet coincide con la cartella di sessione.",
                    )
            except Exception:
                pass
        return True, ""

    def _prompt_repertory_smartphone_folder_creation(self, parent, folder_path: Path, reason: str) -> bool:
        dialog = ManagedCTkToplevel(parent)
        dialog.title("Cartella Smartphone/Tablet non disponibile")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.geometry("760x360")
        dialog.minsize(680, 320)

        user_choice = {"verify": False}

        def _close(verify: bool) -> None:
            user_choice["verify"] = verify
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close(False))
        dialog.grid_columnconfigure(0, weight=1)

        body = ctk.CTkFrame(dialog)
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        body.grid_columnconfigure(0, weight=1)

        text = (
            "Per procedere con Organizzazione Repertorio e necessario che la cartella seguente esista, "
            "sia accessibile e scrivibile:\n\n"
            f"{folder_path}\n\n"
            "Correggi il percorso o rendi disponibile la cartella, poi usa Verifica.\n\n"
            f"Dettaglio controllo: {reason}"
        )
        ctk.CTkLabel(body, text=text, justify="left", anchor="w", wraplength=700).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(10, 12),
        )

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        button_row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            button_row,
            text="Ho creato la cartella - Verifica",
            command=lambda: _close(True),
            height=40,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            button_row,
            text="Annulla",
            command=lambda: _close(False),
            height=40,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        parent.wait_window(dialog)
        return bool(user_choice["verify"])

    def _ensure_repertory_smartphone_folder_ready(self) -> bool:
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        target = self._get_repertory_smartphone_root()
        while True:
            try:
                assert_smartphone_tablet_dir_accessible(target, require_exists=True)
                return True
            except RuntimeError as error:
                should_verify = self._prompt_repertory_smartphone_folder_creation(parent, target, str(error))
                if not should_verify:
                    return False

    def _open_repertory_smartphone_folder(self) -> None:
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        active_folder = self._active_repertory_smartphone_folder()
        if not active_folder:
            self._update_repertory_android_buttons_state()
            return
        target = Path(active_folder).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            self._repertory_last_completed_smartphone_folder = None
            self._update_repertory_android_buttons_state()
            messagebox.showwarning(
                "Organizza repertorio",
                f"Cartella Smartphone/Tablet non trovata:\n{target}",
                parent=parent,
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(target))
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as error:
            messagebox.showerror(
                "Organizza repertorio",
                f"Impossibile aprire la cartella Smartphone/Tablet:\n{error}",
                parent=parent,
            )

    def _confirm_reset_repertory_smartphone_folder(self) -> bool:
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        active_folder = self._active_repertory_smartphone_folder()
        if not active_folder:
            self._update_repertory_android_buttons_state()
            return False
        target = Path(active_folder).expanduser().resolve()
        dialog = ManagedCTkToplevel(parent)
        dialog.title("Reset cartella Smartphone/Tablet")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.geometry("820x420")
        dialog.minsize(760, 380)

        accepted = {"value": False}

        def _close(value: bool) -> None:
            accepted["value"] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close(False))
        dialog.grid_columnconfigure(0, weight=1)

        content = ctk.CTkFrame(dialog)
        content.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        content.grid_columnconfigure(0, weight=1)

        message = (
            "Tutti i file e tutte le sottocartelle presenti nella cartella:\n\n"
            f"{target}\n\n"
            "verranno eliminati.\n\n"
            "La cartella principale verra mantenuta e sara pronta per una nuova sincronizzazione.\n\n"
            "L'operazione e irreversibile.\n\n"
            "Vuoi continuare?"
        )
        ctk.CTkLabel(content, text=message, justify="left", anchor="w", wraplength=760).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(10, 12),
        )

        row = ctk.CTkFrame(content, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            row,
            text="Si, svuota la cartella",
            command=lambda: _close(True),
            height=40,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            row,
            text="Annulla",
            command=lambda: _close(False),
            height=40,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        parent.wait_window(dialog)
        return bool(accepted["value"])

    def _reset_repertory_smartphone_folder(self) -> None:
        if self._repertory_reset_in_progress:
            return

        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        active_folder = self._active_repertory_smartphone_folder()
        if not active_folder:
            self._update_repertory_android_buttons_state()
            return
        target = Path(active_folder).expanduser().resolve()
        allowed, reason = self._validate_repertory_smartphone_reset_target(target)
        if not allowed:
            messagebox.showerror("Reset cartella Smartphone/Tablet", reason, parent=parent)
            self._update_repertory_android_buttons_state()
            return

        if not self._confirm_reset_repertory_smartphone_folder():
            return

        self._repertory_reset_in_progress = True
        self._update_repertory_android_buttons_state()

        def _run_reset() -> None:
            try:
                deleted_files, deleted_dirs = reset_smartphone_tablet_dir(
                    target,
                    expected_root=target,
                )
            except Exception as error:
                self.after(0, self._on_repertory_smartphone_reset_failed, str(error))
                return
            self.after(0, self._on_repertory_smartphone_reset_completed, deleted_files, deleted_dirs)

        threading.Thread(target=_run_reset, daemon=True).start()

    def _on_repertory_smartphone_reset_failed(self, error_message: str) -> None:
        self._repertory_reset_in_progress = False
        self._update_repertory_android_buttons_state()
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        messagebox.showerror(
            "Reset cartella Smartphone/Tablet",
            f"Impossibile completare il reset:\n{error_message}",
            parent=parent,
        )

    def _on_repertory_smartphone_reset_completed(self, deleted_files: int, deleted_dirs: int) -> None:
        self._repertory_reset_in_progress = False
        self._update_repertory_android_buttons_state()
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        if deleted_files == 0 and deleted_dirs == 0:
            messagebox.showinfo(
                "Reset cartella Smartphone/Tablet",
                "La cartella Smartphone/Tablet e gia vuota.",
                parent=parent,
            )
            return

        messagebox.showinfo(
            "Reset cartella Smartphone/Tablet",
            "Cartella Smartphone/Tablet svuotata con successo.\n\n"
            f"File eliminati: {deleted_files}\n"
            f"Sottocartelle eliminate: {deleted_dirs}\n\n"
            "La cartella e pronta per ricevere i prossimi aggiornamenti.",
            parent=parent,
        )

    @staticmethod
    def _compute_repertory_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
        safe_screen_width = max(920, int(screen_width))
        safe_screen_height = max(720, int(screen_height))
        horizontal_margin = 64
        vertical_margin = 96
        desired_width = 1300
        desired_height = 880
        window_width = min(desired_width, max(900, safe_screen_width - horizontal_margin))
        window_height = min(desired_height, max(640, safe_screen_height - vertical_margin))
        return window_width, window_height, horizontal_margin, vertical_margin

    def _compute_rep003_window_geometry(self, screen_width: int, screen_height: int) -> tuple[int, int]:
        safe_screen_width = max(980, int(screen_width))
        safe_screen_height = max(760, int(screen_height))
        width = min(1540, max(1020, safe_screen_width - 64))
        height = min(920, max(720, safe_screen_height - 88))
        return width, height

    def _select_rep003_folder(self, entry_widget, title: str) -> None:
        if entry_widget is None:
            return
        selected = filedialog.askdirectory(title=title, parent=self._rep003_window)
        if selected:
            self._replace_entry(entry_widget, selected)

    def _close_rep003_window(self) -> None:
        if self.rep003_worker.is_running and not self._is_destroying:
            confirm = messagebox.askyesno(
                "Inserimento nuovi brani",
                "Elaborazione in corso. Vuoi interrompere l'aggiornamento?",
                parent=self._rep003_window,
            )
            if not confirm:
                return
            self.rep003_worker.cancel()
            return

        self._close_rep003_decision_dialog()
        self._close_rep003_create_folder_dialog()
        self._reset_rep003_operational_session(
            preserve_results=False,
            preserve_android_destination=False,
            clear_paths=True,
        )
        if self._rep003_window is not None:
            self._cleanup_tooltips(owner=self._rep003_window)
            try:
                self._rep003_window.destroy()
            except Exception:
                pass
        self._rep003_window = None
        self._rep003_tracks_tree = None
        self._rep003_folders_tree = None
        self._rep003_new_tracks_entry = None
        self._rep003_split_entry = None
        self._rep003_general_entry = None
        self._rep003_smartphone_entry = None
        self._rep003_status_label = None
        self._rep003_load_button = None
        self._rep003_create_folder_button = None
        self._rep003_refresh_folders_button = None
        self._rep003_assign_button = None
        self._rep003_remove_button = None
        self._rep003_show_managed_switch = None
        self._rep003_browse_buttons = []
        self._rep003_track_row_by_iid = {}
        self._rep003_folder_iid_by_relative = {}
        self._rep003_tracks_h_scroll = None
        self._rep003_folders_h_scroll = None
        self._rep003_folder_sort_key = REP003_SORT_FOLDER_RELATIVE
        self._rep003_folder_sort_reverse = False
        self._rep003_pending_decision_request_id = None
        self._rep003_create_folder_dialog = None
        self._rep003_create_folder_entry = None
        self._rep003_create_folder_focus_after_id = None
        self._rep003_create_folder_preview_var.set("")
        self._rep003_session_policy = "ASK"
        self._rep003_session_state = REP003_SESSION_NOT_LOADED
        self._rep003_last_processed_sources = set()

    def _build_rep003_panel(self, parent: Any) -> None:
        self._rep003_panel_frame = parent
        self._rep003_window = self._repertory_dialog
        self._configure_rep003_treeview_style()

        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        self._rep003_browse_buttons = []
        self._rep003_path_widgets = []

        paths_frame = ctk.CTkFrame(parent)
        paths_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        paths_frame.grid_columnconfigure(1, weight=1)

        def _add_path_row(row: int, label: str, title: str):
            ctk.CTkLabel(paths_frame, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            entry = ctk.CTkEntry(paths_frame)
            entry.grid(row=row, column=1, sticky="ew", padx=10, pady=4)
            browse_btn = ctk.CTkButton(
                paths_frame,
                text="Sfoglia",
                width=90,
                command=lambda e=entry, t=title: self._select_rep003_folder(e, t),
            )
            browse_btn.grid(row=row, column=2, sticky="e", padx=(0, 10), pady=4)
            self._rep003_browse_buttons.append(browse_btn)
            self._rep003_path_widgets.extend([entry, browse_btn])
            return entry

        self._rep003_new_tracks_entry = _add_path_row(0, "Cartella Nuovi Brani", "Seleziona Cartella Nuovi Brani")
        self._rep003_split_entry = _add_path_row(1, "Cartella Repertorio Suddiviso", "Seleziona Cartella Repertorio Suddiviso")
        self._rep003_general_entry = _add_path_row(2, "Cartella Repertorio Generale", "Seleziona Cartella Repertorio Generale")
        self._rep003_smartphone_entry = _add_path_row(3, "Cartella Smartphone/Tablet", "Seleziona Cartella Smartphone/Tablet")

        content = ctk.CTkFrame(parent)
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        content.grid_columnconfigure(0, weight=7)
        content.grid_columnconfigure(1, weight=4, minsize=340)
        content.grid_columnconfigure(2, weight=7)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="Lista Nuovi Brani", anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self._rep003_tracks_tree = ttk.Treeview(
            left,
            columns=("name", "status", "folders"),
            show="headings",
            selectmode="extended",
            style="Rep003.Treeview",
        )
        self._rep003_tracks_tree.heading("name", text="Nome File", command=lambda: self._rep003_sort_tracks_by(REP003_SORT_NAME))
        self._rep003_tracks_tree.heading("status", text="Stato", command=lambda: self._rep003_sort_tracks_by(REP003_SORT_STATUS))
        self._rep003_tracks_tree.heading("folders", text="Cartelle Abbinate", command=lambda: self._rep003_sort_tracks_by(REP003_SORT_FOLDERS))
        self._rep003_tracks_tree.column("name", width=460, minwidth=320, anchor="w", stretch=True)
        self._rep003_tracks_tree.column("status", width=180, minwidth=150, anchor="center", stretch=False)
        self._rep003_tracks_tree.column("folders", width=560, minwidth=300, anchor="w", stretch=True)

        tracks_v_scroll = ttk.Scrollbar(left, orient="vertical", command=self._rep003_tracks_tree.yview)
        tracks_h_scroll = ttk.Scrollbar(left, orient="horizontal", command=self._rep003_tracks_tree.xview)
        self._rep003_tracks_tree.configure(yscrollcommand=tracks_v_scroll.set, xscrollcommand=tracks_h_scroll.set)
        self._rep003_tracks_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 0))
        tracks_v_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 0))
        tracks_h_scroll.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._rep003_tracks_tree.bind("<<TreeviewSelect>>", self._rep003_on_tracks_selection_changed)
        self._rep003_tracks_tree.bind("<Configure>", lambda _event: self._rep003_update_horizontal_scrollbars_visibility())

        center = ctk.CTkFrame(content)
        center.grid(row=0, column=1, sticky="nsew", padx=8)
        center.grid_columnconfigure(0, weight=1, minsize=300)

        rep003_center_button_font = ctk.CTkFont("Segoe UI Semibold", 14)
        rep003_center_switch_font = ctk.CTkFont("Segoe UI", 13)
        rep003_button_height = 44

        self._rep003_load_button = ctk.CTkButton(
            center,
            text="Carica cartelle e brani",
            width=300,
            height=rep003_button_height,
            font=rep003_center_button_font,
            command=self._rep003_load_sources,
        )
        self._rep003_load_button.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 8))
        self._rep003_path_widgets.append(self._rep003_load_button)

        self._rep003_show_managed_switch = ctk.CTkSwitch(
            center,
            text="Mostra brani gestiti",
            width=300,
            height=38,
            font=rep003_center_switch_font,
            variable=self._rep003_show_managed_var,
            command=self._rep003_on_show_managed_toggle,
        )
        self._rep003_show_managed_switch.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        self._rep003_create_folder_button = ctk.CTkButton(
            center,
            text="Crea nuova cartella",
            width=300,
            height=rep003_button_height,
            font=rep003_center_button_font,
            command=self._rep003_create_folder,
        )
        self._rep003_create_folder_button.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))

        self._rep003_refresh_folders_button = ctk.CTkButton(
            center,
            text="Aggiorna elenco cartelle",
            width=300,
            height=rep003_button_height,
            font=rep003_center_button_font,
            command=self._rep003_refresh_folders_only,
        )
        self._rep003_refresh_folders_button.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))

        self._rep003_assign_button = ctk.CTkButton(
            center,
            text="Conferma abbinamento",
            width=300,
            height=rep003_button_height,
            font=rep003_center_button_font,
            command=self._rep003_assign_selected,
        )
        self._rep003_assign_button.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._rep003_remove_button = ctk.CTkButton(
            center,
            text="Elimina abbinamento",
            width=300,
            height=rep003_button_height,
            font=rep003_center_button_font,
            command=self._rep003_remove_assignment,
        )
        self._rep003_remove_button.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))

        right = ctk.CTkFrame(content)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(right, text="Lista Cartelle Repertorio", anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self._rep003_folders_tree = ttk.Treeview(
            right,
            columns=("folder", "relative", "count", "size"),
            show="headings",
            selectmode="extended",
            style="Rep003.Treeview",
        )
        self._rep003_folders_tree.heading("folder", text="Nome Cartella", command=lambda: self._rep003_sort_folders_by(REP003_SORT_FOLDER_NAME))
        self._rep003_folders_tree.heading("relative", text="Percorso Relativo", command=lambda: self._rep003_sort_folders_by(REP003_SORT_FOLDER_RELATIVE))
        self._rep003_folders_tree.heading("count", text="Numero MP3 diretti", command=lambda: self._rep003_sort_folders_by(REP003_SORT_FOLDER_COUNT))
        self._rep003_folders_tree.heading("size", text="Dimensione MP3 diretti", command=lambda: self._rep003_sort_folders_by(REP003_SORT_FOLDER_SIZE))
        self._rep003_folders_tree.column("folder", width=280, minwidth=220, anchor="w", stretch=True)
        self._rep003_folders_tree.column("relative", width=430, minwidth=300, anchor="w", stretch=True)
        self._rep003_folders_tree.column("count", width=170, minwidth=160, anchor="center", stretch=False)
        self._rep003_folders_tree.column("size", width=220, minwidth=190, anchor="e", stretch=False)

        folders_v_scroll = ttk.Scrollbar(right, orient="vertical", command=self._rep003_folders_tree.yview)
        folders_h_scroll = ttk.Scrollbar(right, orient="horizontal", command=self._rep003_folders_tree.xview)
        self._rep003_folders_tree.configure(yscrollcommand=folders_v_scroll.set, xscrollcommand=folders_h_scroll.set)
        self._rep003_folders_tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 0))
        folders_v_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 8), pady=(0, 0))
        folders_h_scroll.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._rep003_folders_tree.bind("<<TreeviewSelect>>", self._rep003_on_folders_selection_changed)
        self._rep003_folders_tree.bind("<Configure>", lambda _event: self._rep003_update_horizontal_scrollbars_visibility())

        self._rep003_tracks_h_scroll = tracks_h_scroll
        self._rep003_folders_h_scroll = folders_h_scroll

        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)

        self._rep003_status_label = ctk.CTkLabel(footer, text="Brani caricati: 0 | Gestiti: 0 | Da gestire: 0", anchor="w")
        self._rep003_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._rep003_path_widgets.extend([
            self._rep003_create_folder_button,
            self._rep003_refresh_folders_button,
            self._rep003_assign_button,
            self._rep003_remove_button,
            self._rep003_show_managed_switch,
            self._rep003_tracks_tree,
            self._rep003_folders_tree,
        ])

        self._replace_entry(self._rep003_new_tracks_entry, str(self.settings.get("rep003_new_tracks_folder", "") or ""))
        self._replace_entry(self._rep003_split_entry, str(self.settings.get("rep003_split_folder", "") or self.settings.get("repertory_library_folder", "") or ""))
        self._replace_entry(
            self._rep003_general_entry,
            str(self.settings.get("rep003_general_folder", "") or self.settings.get("repertory_general_folder", "") or self.settings.get("repertory_library_folder", "") or ""),
        )
        self._replace_entry(self._rep003_smartphone_entry, str(self.settings.get("rep003_smartphone_folder", "") or self._repertory_smartphone_root or ""))

        self._rep003_show_managed_var.set(True)
        self._rep003_sort_key = REP003_SORT_NAME
        self._rep003_sort_reverse = False
        self._rep003_folder_sort_key = REP003_SORT_FOLDER_RELATIVE
        self._rep003_folder_sort_reverse = False
        self._rep003_refresh_folders_tree(clear_selection=True)
        self._rep003_refresh_tracks_tree(clear_selection=True)
        self._rep003_update_status()
        self._rep003_update_horizontal_scrollbars_visibility()

        self._rep003_new_tracks_entry_tooltip = self._add_tooltip(
            self._rep003_new_tracks_entry,
            "Seleziona la cartella contenente i nuovi MP3 da inserire. Le sottocartelle non vengono lette.",
        )
        self._rep003_split_entry_tooltip = self._add_tooltip(
            self._rep003_split_entry,
            "Seleziona la root del repertorio in cui assegnare i nuovi brani.",
        )
        self._rep003_general_entry_tooltip = self._add_tooltip(
            self._rep003_general_entry,
            "Seleziona la cartella piatta che conterra una copia di ogni nuovo brano.",
        )
        self._rep003_smartphone_entry_tooltip = self._add_tooltip(
            self._rep003_smartphone_entry,
            "Seleziona la cartella di appoggio destinata alla futura sincronizzazione Android.",
        )
        self._rep003_load_button_tooltip = self._add_tooltip(
            self._rep003_load_button,
            "Carica i nuovi MP3 e l'elenco delle cartelle disponibili.",
        )
        self._rep003_show_managed_tooltip = self._add_tooltip(
            self._rep003_show_managed_switch,
            "Attivo: mostra anche i brani gia abbinati. Disattivo: mostra soltanto quelli da gestire.",
        )
        self._rep003_create_folder_tooltip = self._add_tooltip(
            self._rep003_create_folder_button,
            "Crea una nuova cartella nella destinazione selezionata del Repertorio suddiviso.",
        )
        self._rep003_refresh_folders_tooltip = self._add_tooltip(
            self._rep003_refresh_folders_button,
            "Rilegge le cartelle senza cancellare brani e abbinamenti gia memorizzati.",
        )
        self._rep003_assign_button_tooltip = self._add_tooltip(
            self._rep003_assign_button,
            "Memorizza le cartelle selezionate per i brani scelti.",
        )
        self._rep003_remove_button_tooltip = self._add_tooltip(
            self._rep003_remove_button,
            "Cancella gli abbinamenti memorizzati per i brani selezionati.",
        )

    def _configure_rep003_treeview_style(self) -> None:
        if self._rep003_ttk_style_configured:
            return
        style = ttk.Style(self)
        style.configure(
            "Rep003.Treeview",
            font=("Segoe UI", 14),
            rowheight=34,
            padding=2,
        )
        style.configure(
            "Rep003.Treeview.Heading",
            font=("Segoe UI Semibold", 14),
            padding=(8, 6),
        )
        style.map(
            "Rep003.Treeview",
            background=[("selected", "#275d9a")],
            foreground=[("selected", "#ffffff")],
        )
        self._rep003_ttk_style_configured = True

    def _rep003_update_horizontal_scrollbars_visibility(self) -> None:
        pairs = (
            (self._rep003_tracks_tree, self._rep003_tracks_h_scroll),
            (self._rep003_folders_tree, self._rep003_folders_h_scroll),
        )
        for tree, scroll in pairs:
            if tree is None or scroll is None:
                continue
            try:
                start, end = tree.xview()
            except Exception:
                continue
            if float(start) <= 0.0 and float(end) >= 1.0:
                scroll.grid_remove()
            else:
                scroll.grid()

    def _close_rep003_decision_dialog(self) -> None:
        if self._rep003_decision_dialog is not None:
            try:
                self._rep003_decision_dialog.destroy()
            except Exception:
                pass
        self._rep003_decision_dialog = None

    def _close_rep003_create_folder_dialog(self) -> None:
        dialog = self._rep003_create_folder_dialog
        focus_after_id = self._rep003_create_folder_focus_after_id
        self._rep003_create_folder_dialog = None
        self._rep003_create_folder_entry = None
        self._rep003_create_folder_focus_after_id = None
        self._rep003_create_folder_preview_var.set("")

        if dialog is not None:
            try:
                if focus_after_id is not None:
                    dialog.after_cancel(focus_after_id)
            except Exception:
                pass
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        if self._rep003_window is not None:
            try:
                self._rep003_window.focus_set()
            except Exception:
                pass

    def _rep003_set_ui_running_state(self, running: bool) -> None:
        widgets: list[Any] = [
            self._rep003_new_tracks_entry,
            self._rep003_split_entry,
            self._rep003_general_entry,
            self._rep003_smartphone_entry,
            self._rep003_load_button,
            self._rep003_create_folder_button,
            self._rep003_refresh_folders_button,
            self._rep003_assign_button,
            self._rep003_remove_button,
            self._rep003_show_managed_switch,
            self._rep003_tracks_tree,
            self._rep003_folders_tree,
        ]
        widgets.extend(self._rep003_browse_buttons)
        state = "disabled" if running else "normal"
        for widget in widgets:
            if widget is None:
                continue
            try:
                widget.configure(state=state)
            except Exception:
                continue
        if not running:
            self._rep003_update_assignment_buttons_state()

    def _rep003_status_display(self, item) -> str:
        if item.status != STATUS_GESTITO:
            return STATUS_DA_GESTIRE
        unavailable_count = self._rep003_count_unavailable_destinations(item.destinations)
        if unavailable_count > 0:
            return f"{STATUS_GESTITO} ({len(item.destinations)}, non disponibili: {unavailable_count})"
        return f"{STATUS_GESTITO} ({len(item.destinations)})"

    @staticmethod
    def _rep003_normalize_relative_path(value: str | None) -> str:
        return str(value or "").strip().replace("\\", "/").strip("/")

    @staticmethod
    def _rep003_destination_key(value: str | None) -> str:
        normalized = str(value or "").strip().replace("\\", "/").strip("/")
        if normalized == ".":
            return ""
        return normalized

    def _rep003_folder_relatives_set(self) -> set[str]:
        return {
            self._rep003_normalize_relative_path(folder.relative_path)
            for folder in self._rep003_model.folders
        }

    def _rep003_count_unavailable_destinations(self, destinations: tuple[str, ...]) -> int:
        if not destinations:
            return 0
        available = self._rep003_folder_relatives_set()
        missing = 0
        for destination in destinations:
            if self._rep003_destination_key(destination) not in available:
                missing += 1
        return missing

    def _rep003_destination_labels_with_availability(self, source_path: str) -> str:
        item = self._rep003_model.get_track(source_path)
        if item is None or not item.destinations:
            return ""

        available = self._rep003_folder_relatives_set()
        labels: list[str] = []
        for destination in item.destinations:
            normalized = self._rep003_destination_key(destination)
            label = self._rep003_model._display_label_for_destination(normalized)
            if normalized not in available:
                label = f"{label} [NON DISPONIBILE]"
            labels.append(label)
        return ", ".join(labels)

    def _rep003_folders_display_value(self, source_path: str) -> str:
        rendered = self._rep003_destination_labels_with_availability(source_path).strip()
        return rendered if rendered else "-"

    @staticmethod
    def _rep003_sortable_folders_value(rendered_value: str) -> str:
        normalized = str(rendered_value or "").strip()
        if not normalized or normalized == "-":
            return ""
        return normalized.casefold()

    def _rep003_restore_folder_selection(self, selected_relative_paths: list[str]) -> None:
        if self._rep003_folders_tree is None:
            return
        if not selected_relative_paths:
            return
        selected_iids: list[str] = []
        for relative in selected_relative_paths:
            normalized = self._rep003_normalize_relative_path(relative)
            iid = "__ROOT__" if normalized in {"", "."} else normalized
            if iid in self._rep003_folder_iid_by_relative:
                selected_iids.append(iid)
        if selected_iids:
            self._rep003_folders_tree.selection_set(selected_iids)

    @staticmethod
    def _rep003_folder_sort_token(folder, key: str):
        relative = str(folder.relative_path or "")
        if key == REP003_SORT_FOLDER_NAME:
            return str(folder.folder_name or "").casefold()
        if key == REP003_SORT_FOLDER_COUNT:
            return int(folder.direct_mp3_count)
        if key == REP003_SORT_FOLDER_SIZE:
            return int(folder.direct_mp3_size_bytes)
        return relative.casefold()

    def _rep003_selected_track_paths(self) -> list[str]:
        if self._rep003_tracks_tree is None:
            return []
        selected_paths: list[str] = []
        for iid in self._rep003_tracks_tree.selection():
            source_path = self._rep003_track_row_by_iid.get(str(iid))
            if source_path:
                selected_paths.append(source_path)
        return selected_paths

    def _rep003_selected_folder_relative_paths(self) -> list[str]:
        if self._rep003_folders_tree is None:
            return []
        selected: list[str] = []
        for iid in self._rep003_folders_tree.selection():
            relative = self._rep003_folder_iid_by_relative.get(str(iid), None)
            if relative is None:
                continue
            selected.append(relative if relative else ".")
        return selected

    def _rep003_update_status(self) -> None:
        if self._rep003_status_label is None:
            return
        if not self._widget_exists(self._rep003_status_label):
            return
        total = len(self._rep003_model.tracks)
        managed = sum(1 for item in self._rep003_model.tracks if item.status == STATUS_GESTITO)
        try:
            self._rep003_status_label.configure(text=f"Brani caricati: {total} | Gestiti: {managed} | Da gestire: {max(0, total - managed)}")
        except (tk.TclError, RuntimeError):
            return

    def _rep003_update_assignment_buttons_state(self) -> None:
        has_tracks = bool(self._rep003_model.tracks)
        selected_tracks = self._rep003_selected_track_paths()
        selected_folders = self._rep003_selected_folder_relative_paths()
        running = self.rep003_worker.is_running

        can_assign = bool(has_tracks and selected_tracks and selected_folders and not running)
        can_remove = False
        if has_tracks and selected_tracks and not running:
            for source_path in selected_tracks:
                item = self._rep003_model.get_track(source_path)
                if item is not None and item.status == STATUS_GESTITO and bool(item.destinations):
                    can_remove = True
                    break

        if self._rep003_assign_button is not None:
            try:
                self._rep003_assign_button.configure(state="normal" if can_assign else "disabled")
            except Exception:
                pass
        if self._rep003_remove_button is not None:
            try:
                self._rep003_remove_button.configure(state="normal" if can_remove else "disabled")
            except Exception:
                pass

    def _rep003_update_finalize_button_state(self) -> None:
        self._update_repertory_primary_action_state()

    @staticmethod
    def _rep003_format_binary_size(size_bytes: int) -> str:
        value = float(max(0, int(size_bytes)))
        units = ["B", "KB", "MB", "GB"]
        index = 0
        while value >= 1024.0 and index < len(units) - 1:
            value /= 1024.0
            index += 1
        if index == 0:
            return f"{int(value)} {units[index]}"
        return f"{value:.2f} {units[index]}"

    def _rep003_refresh_folders_tree(self, *, clear_selection: bool = True) -> None:
        if self._rep003_folders_tree is None:
            return

        self._rep003_folders_tree.delete(*self._rep003_folders_tree.get_children())
        self._rep003_folder_iid_by_relative = {}
        folders = sorted(
            self._rep003_model.folders,
            key=lambda row: self._rep003_folder_sort_token(row, self._rep003_folder_sort_key),
            reverse=self._rep003_folder_sort_reverse,
        )

        for folder in folders:
            relative = str(folder.relative_path or "")
            iid = "__ROOT__" if not relative else relative
            relative_display = "." if not relative else relative
            self._rep003_folders_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    folder.folder_name,
                    relative_display,
                    str(folder.direct_mp3_count),
                    self._rep003_format_binary_size(folder.direct_mp3_size_bytes),
                ),
            )
            self._rep003_folder_iid_by_relative[iid] = relative

        if clear_selection:
            self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
        self._rep003_update_horizontal_scrollbars_visibility()
        self._rep003_update_assignment_buttons_state()

    def _rep003_refresh_tracks_tree(self, *, clear_selection: bool = True) -> None:
        if self._rep003_tracks_tree is None:
            return

        show_managed = bool(self._rep003_show_managed_var.get())
        rows = self._rep003_model.get_visible_tracks(show_managed=show_managed)
        if self._rep003_sort_key == REP003_SORT_STATUS:
            rows = sorted(
                rows,
                key=lambda row: (
                    0 if row.status != STATUS_GESTITO else 1,
                    len(row.destinations),
                    row.file_name.casefold(),
                ),
                reverse=self._rep003_sort_reverse,
            )
        elif self._rep003_sort_key == REP003_SORT_FOLDERS:
            rows = sorted(
                rows,
                key=lambda row: (
                    self._rep003_sortable_folders_value(self._rep003_folders_display_value(row.source_path)),
                    row.file_name.casefold(),
                ),
                reverse=self._rep003_sort_reverse,
            )
        else:
            rows = self._rep003_model.sort_tracks(rows, self._rep003_sort_key, self._rep003_sort_reverse)

        self._rep003_tracks_tree.delete(*self._rep003_tracks_tree.get_children())
        self._rep003_track_row_by_iid = {}
        for index, row in enumerate(rows):
            iid = f"track-{index}"
            self._rep003_tracks_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.file_name,
                    self._rep003_status_display(row),
                    self._rep003_folders_display_value(row.source_path),
                ),
            )
            self._rep003_track_row_by_iid[iid] = row.source_path

        if clear_selection:
            self._rep003_tracks_tree.selection_remove(self._rep003_tracks_tree.selection())
        self._rep003_update_horizontal_scrollbars_visibility()
        self._rep003_update_assignment_buttons_state()

    def _rep003_sort_tracks_by(self, key: str) -> None:
        selected_tracks = self._rep003_selected_track_paths()
        selected_folders = self._rep003_selected_folder_relative_paths()
        if key == self._rep003_sort_key:
            self._rep003_sort_reverse = not self._rep003_sort_reverse
        else:
            self._rep003_sort_key = key
            self._rep003_sort_reverse = False
        self._rep003_refresh_tracks_tree(clear_selection=True)
        self._rep003_restore_track_selection(selected_tracks)
        self._rep003_restore_folder_selection(selected_folders)

    def _rep003_sort_folders_by(self, key: str) -> None:
        if key == self._rep003_folder_sort_key:
            self._rep003_folder_sort_reverse = not self._rep003_folder_sort_reverse
        else:
            self._rep003_folder_sort_key = key
            self._rep003_folder_sort_reverse = False
        self._rep003_refresh_folders_tree(clear_selection=True)
        if self._rep003_tracks_tree is not None:
            self._rep003_tracks_tree.selection_remove(self._rep003_tracks_tree.selection())

    def _rep003_on_show_managed_toggle(self) -> None:
        self._rep003_refresh_tracks_tree(clear_selection=True)
        if self._rep003_folders_tree is not None:
            self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
        self._rep003_update_assignment_buttons_state()
        self._rep003_update_finalize_button_state()

    def _rep003_on_tracks_selection_changed(self, _event=None) -> None:
        if self._rep003_folders_tree is None:
            return
        selected_tracks = self._rep003_selected_track_paths()
        self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
        if len(selected_tracks) != 1:
            return
        item = self._rep003_model.get_track(selected_tracks[0])
        if item is None or item.status != STATUS_GESTITO:
            return
        for destination in item.destinations:
            relative = "" if destination == "." else destination
            iid = "__ROOT__" if not relative else relative
            if iid in self._rep003_folder_iid_by_relative:
                self._rep003_folders_tree.selection_add(iid)
                try:
                    self._rep003_folders_tree.see(iid)
                except Exception:
                    pass
        self._rep003_update_assignment_buttons_state()

    def _rep003_on_folders_selection_changed(self, _event=None) -> None:
        self._rep003_update_assignment_buttons_state()

    def _rep003_assign_selected(self) -> None:
        selected_tracks = self._rep003_selected_track_paths()
        if not selected_tracks:
            messagebox.showerror("Inserimento nuovi brani", "Selezionare almeno un brano.", parent=self._rep003_window)
            return
        selected_folders = self._rep003_selected_folder_relative_paths()
        if not selected_folders:
            messagebox.showerror("Inserimento nuovi brani", "Selezionare almeno una cartella repertorio.", parent=self._rep003_window)
            return

        try:
            self._rep003_model.assign_tracks(selected_tracks, selected_folders)
        except ValueError as error:
            messagebox.showerror("Inserimento nuovi brani", str(error), parent=self._rep003_window)
            return

        self._rep003_refresh_folders_tree(clear_selection=True)
        self._rep003_refresh_tracks_tree(clear_selection=True)
        if self._rep003_folders_tree is not None:
            self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
        self._rep003_update_status()
        self._rep003_update_session_state_from_model()
        self._rep003_update_assignment_buttons_state()
        self._rep003_update_finalize_button_state()

    def _rep003_remove_assignment(self) -> None:
        selected_tracks = self._rep003_selected_track_paths()
        if not selected_tracks:
            messagebox.showerror("Inserimento nuovi brani", "Selezionare almeno un brano da aggiornare.", parent=self._rep003_window)
            return
        self._rep003_model.remove_assignments(selected_tracks)
        self._rep003_refresh_folders_tree(clear_selection=True)
        self._rep003_refresh_tracks_tree(clear_selection=True)
        if self._rep003_folders_tree is not None:
            self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
        self._rep003_update_status()
        self._rep003_update_session_state_from_model()
        self._rep003_update_assignment_buttons_state()
        self._rep003_update_finalize_button_state()

    def _rep003_load_sources(self) -> None:
        if self._rep003_new_tracks_entry is None or self._rep003_split_entry is None or self._rep003_general_entry is None or self._rep003_smartphone_entry is None:
            return

        new_tracks_folder = self._rep003_new_tracks_entry.get().strip()
        split_folder = self._rep003_split_entry.get().strip()
        general_folder = self._rep003_general_entry.get().strip()
        smartphone_folder = self._rep003_smartphone_entry.get().strip()

        if not new_tracks_folder or not Path(new_tracks_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Nuovi Brani non valida.", parent=self._rep003_window)
            return
        if not split_folder or not Path(split_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Suddiviso non valida.", parent=self._rep003_window)
            return
        if not general_folder or not Path(general_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Generale non valida.", parent=self._rep003_window)
            return

        try:
            ensure_folder_available(smartphone_folder)
        except Exception as error:
            messagebox.showerror("Inserimento nuovi brani", f"Cartella Smartphone/Tablet non disponibile:\n{error}", parent=self._rep003_window)
            return

        split_root = Path(split_folder).expanduser().resolve()
        general_root = Path(general_folder).expanduser().resolve()
        if self._repertory_paths_collide(split_folder, general_folder):
            messagebox.showerror(
                "Inserimento nuovi brani",
                "La Cartella Repertorio Generale non puo coincidere con il Repertorio Suddiviso.",
                parent=self._rep003_window,
            )
            return

        self._rep003_update_session_state_from_model()
        if self._rep003_has_pending_assignments():
            pending_count = self._rep003_count_assigned_tracks()
            proceed = self._show_rep003_confirmation_dialog(
                title="Attenzione: abbinamenti in corso",
                body=(
                    "Sono presenti abbinamenti non ancora elaborati.\n"
                    "Se ricarichi ora, verranno persi.\n\n"
                    f"Abbinamenti correnti: {pending_count}\n\n"
                    "Vuoi continuare e ricaricare comunque?"
                ),
                confirm_label="Ricarica e annulla abbinamenti",
                cancel_label="Annulla",
            )
            if not proceed:
                return
            self._rep003_discard_pending_assignments_state()

        previous_state = self._rep003_session_state
        if previous_state in {
            REP003_SESSION_COMPLETED,
            REP003_SESSION_COMPLETED_WITH_ERRORS,
            REP003_SESSION_READY_FOR_NEW_SESSION,
        }:
            self._rep003_model.reset()
            self._rep003_last_processed_sources = set()

        self._repertory_session_folder = None
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_INSERT_TRACKS, "")
        self._rep003_session_state = REP003_SESSION_NOT_LOADED
        try:
            loaded_tracks = list_new_tracks_non_recursive(new_tracks_folder)
            loaded_folders = self._rep003_scan_repertory_folders(split_folder, split_root, general_root)
        except Exception as error:
            self._append_log(f"[REP003][ERRORE] Caricamento sorgenti fallito: {error}")
            messagebox.showerror(
                "Inserimento nuovi brani",
                f"Errore durante il caricamento di brani/cartelle:\n{error}",
                parent=self._rep003_window,
            )
            return
        self._rep003_model.load_tracks(loaded_tracks)
        self._rep003_model.load_folders(loaded_folders)
        self._rep003_session_policy = "ASK"
        self._rep003_pending_decision_request_id = None
        self._close_rep003_decision_dialog()
        self._repertory_selected_smartphone_folder = str(Path(smartphone_folder).expanduser().resolve())
        self._repertory_last_completed_smartphone_folder = None

        self.settings["rep003_new_tracks_folder"] = new_tracks_folder
        self.settings["rep003_split_folder"] = split_folder
        self.settings["rep003_general_folder"] = general_folder
        self.settings["rep003_smartphone_folder"] = smartphone_folder
        self.save_settings()

        self._rep003_last_processed_sources = set()
        if loaded_tracks:
            self._rep003_session_state = REP003_SESSION_LOADED_UNASSIGNED
        else:
            self._rep003_session_state = REP003_SESSION_NOT_LOADED

        try:
            self._rep003_refresh_folders_tree(clear_selection=True)
            self._rep003_refresh_tracks_tree(clear_selection=True)
            self._rep003_update_status()
            self._rep003_update_finalize_button_state()
        except Exception as error:
            self._append_log(f"[REP003][ERRORE] Popolamento liste fallito: {error}")
            messagebox.showerror(
                "Inserimento nuovi brani",
                f"Errore durante il popolamento delle liste:\n{error}",
                parent=self._rep003_window,
            )
            return
        if not loaded_tracks:
            self._append_log("[REP003] Nessun MP3 trovato direttamente nella Cartella Nuovi Brani.")
            messagebox.showinfo(
                "Inserimento nuovi brani",
                "NESSUN NUOVO BRANO TROVATO nella cartella selezionata.",
                parent=self._rep003_window,
            )
        if not loaded_folders:
            messagebox.showinfo(
                "Inserimento nuovi brani",
                "Nessuna cartella valida trovata nel repertorio suddiviso.",
                parent=self._rep003_window,
            )
        if self.rep003_worker.is_running:
            self._rep003_set_ui_running_state(True)

    def _rep003_collect_excluded_relative_roots(self, split_root: Path, general_root: Path) -> tuple[str, ...]:
        excluded_relative_roots: set[str] = {
            "File Non trovati in Repertorio",
            "Report",
            "Log",
            "Diagnosi",
            "REPERTORIO_GENERALE_DA_MIXCREATOR",
        }
        try:
            general_relative = general_root.relative_to(split_root).as_posix().strip("/")
            if general_relative:
                excluded_relative_roots.add(general_relative)
        except Exception:
            pass

        try:
            for child in split_root.iterdir():
                if not child.is_dir():
                    continue
                folded = child.name.casefold()
                if folded == "diagnosi" or folded.startswith("diagnosi_repertorio_"):
                    excluded_relative_roots.add(child.name)
        except OSError as error:
            raise RuntimeError(f"Lettura cartelle repertorio fallita: {error}") from error

        return tuple(sorted(excluded_relative_roots))

    def _rep003_scan_repertory_folders(self, split_folder: str, split_root: Path, general_root: Path) -> list[RepertoryFolderItem]:
        excluded_relative_roots = self._rep003_collect_excluded_relative_roots(split_root, general_root)
        return scan_repertory_folders_non_recursive_stats(
            split_folder,
            excluded_relative_roots=excluded_relative_roots,
        )

    def _rep003_restore_track_selection(self, source_paths: list[str]) -> None:
        if self._rep003_tracks_tree is None:
            return
        if not source_paths:
            return
        selected_iids = [
            iid
            for iid, source_path in self._rep003_track_row_by_iid.items()
            if source_path in source_paths
        ]
        if not selected_iids:
            return
        self._rep003_tracks_tree.selection_set(selected_iids)

    def _rep003_refresh_folders_only(self, *, select_relative: str | None = None) -> None:
        if self._rep003_split_entry is None or self._rep003_general_entry is None:
            return

        split_folder = self._rep003_split_entry.get().strip()
        general_folder = self._rep003_general_entry.get().strip()

        if not split_folder or not Path(split_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Suddiviso non valida.", parent=self._rep003_window)
            return
        if not general_folder or not Path(general_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Generale non valida.", parent=self._rep003_window)
            return
        if self._repertory_paths_collide(split_folder, general_folder):
            messagebox.showerror(
                "Inserimento nuovi brani",
                "La Cartella Repertorio Generale non puo coincidere con il Repertorio Suddiviso.",
                parent=self._rep003_window,
            )
            return

        split_root = Path(split_folder).expanduser().resolve()
        general_root = Path(general_folder).expanduser().resolve()
        selected_tracks = self._rep003_selected_track_paths()

        current_relative = ""
        if self._rep003_folders_tree is not None:
            current_selection = self._rep003_selected_folder_relative_paths()
            if current_selection:
                current_relative = self._rep003_normalize_relative_path(current_selection[0])

        target_relative = self._rep003_normalize_relative_path(select_relative) if select_relative is not None else current_relative

        try:
            folders = self._rep003_scan_repertory_folders(split_folder, split_root, general_root)
        except Exception as error:
            self._append_log(f"[REP003][ERRORE] Aggiornamento cartelle fallito: {error}")
            messagebox.showerror(
                "Inserimento nuovi brani",
                f"Errore durante l'aggiornamento delle cartelle:\n{error}",
                parent=self._rep003_window,
            )
            return

        self._rep003_model.load_folders(folders)
        self._rep003_refresh_folders_tree(clear_selection=True)
        self._rep003_refresh_tracks_tree(clear_selection=True)
        self._rep003_restore_track_selection(selected_tracks)
        self._rep003_update_status()
        self._rep003_update_session_state_from_model()
        self._rep003_update_finalize_button_state()

        if self._rep003_folders_tree is None:
            return

        available_relatives = {
            self._rep003_normalize_relative_path(folder.relative_path)
            for folder in self._rep003_model.folders
        }
        if target_relative not in available_relatives:
            return

        target_iid = "__ROOT__" if target_relative == "" else target_relative
        if target_iid in self._rep003_folder_iid_by_relative:
            self._rep003_folders_tree.selection_set(target_iid)
            try:
                self._rep003_folders_tree.see(target_iid)
            except Exception:
                pass

    @staticmethod
    def _rep003_validate_new_folder_name(raw_name: str) -> str:
        name = str(raw_name or "").strip()
        if not name:
            raise ValueError("Nome cartella non valido: valore vuoto.")
        if name in {".", ".."}:
            raise ValueError("Nome cartella non valido.")
        if Path(name).is_absolute() or "/" in name or "\\" in name:
            raise ValueError("Inserire solo il nome della cartella, senza percorso.")
        if name.endswith(".") or name.endswith(" "):
            raise ValueError("Il nome cartella non puo terminare con punto o spazio.")
        if any(char in REP003_INVALID_FOLDER_CHARS for char in name):
            raise ValueError("Nome cartella non valido: contiene caratteri non consentiti.")
        reserved_key = name.rstrip(" .").split(".", 1)[0].upper()
        if reserved_key in REP003_RESERVED_FOLDER_NAMES:
            raise ValueError("Nome cartella riservato dal sistema.")
        folded = name.casefold()
        if folded in REP003_BLOCKED_TECHNICAL_FOLDER_NAMES or folded.startswith("diagnosi_repertorio_"):
            raise ValueError("Nome cartella riservato a uso tecnico.")
        return name

    def _rep003_create_folder(self) -> None:
        if self._rep003_split_entry is None or self._rep003_general_entry is None:
            return

        split_folder = self._rep003_split_entry.get().strip()
        general_folder = self._rep003_general_entry.get().strip()
        if not split_folder or not Path(split_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Suddiviso non valida.", parent=self._rep003_window)
            return
        if not general_folder or not Path(general_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Generale non valida.", parent=self._rep003_window)
            return

        selected_relatives = self._rep003_selected_folder_relative_paths()
        parent_relative = self._rep003_normalize_relative_path(selected_relatives[0] if selected_relatives else "")
        parent_label = "ROOT" if not parent_relative else parent_relative.replace("/", "\\")
        split_root = Path(split_folder).expanduser().resolve()

        def _submit_folder_name(raw_folder_name: str) -> bool:
            try:
                valid_name = self._rep003_validate_new_folder_name(raw_folder_name)
            except ValueError as error:
                messagebox.showerror("Inserimento nuovi brani", str(error), parent=self._rep003_window)
                return False

            parent_path = split_root if not parent_relative else (split_root / parent_relative)
            if not parent_path.exists() or not parent_path.is_dir():
                messagebox.showerror(
                    "Inserimento nuovi brani",
                    "Cartella padre non disponibile. Aggiornare l'elenco cartelle.",
                    parent=self._rep003_window,
                )
                return False

            try:
                resolved_parent = parent_path.resolve()
                resolved_parent.relative_to(split_root)
            except Exception:
                messagebox.showerror("Inserimento nuovi brani", "Percorso padre non valido.", parent=self._rep003_window)
                return False

            for child in parent_path.iterdir():
                if not child.is_dir():
                    continue
                if child.name.rstrip(" .").casefold() == valid_name.rstrip(" .").casefold():
                    messagebox.showerror(
                        "Inserimento nuovi brani",
                        "Esiste gia una cartella con lo stesso nome (confronto case-insensitive).",
                        parent=self._rep003_window,
                    )
                    return False

            target_folder = parent_path / valid_name
            try:
                target_folder.mkdir(parents=False, exist_ok=False)
            except Exception as error:
                messagebox.showerror(
                    "Inserimento nuovi brani",
                    f"Creazione cartella non riuscita:\n{error}",
                    parent=self._rep003_window,
                )
                return False

            if not target_folder.exists() or not target_folder.is_dir():
                messagebox.showerror(
                    "Inserimento nuovi brani",
                    "Creazione cartella non verificata su filesystem.",
                    parent=self._rep003_window,
                )
                return False

            relative_target = "" if target_folder == split_root else target_folder.relative_to(split_root).as_posix().strip("/")
            self._append_log(f"[REP003] Cartella creata: {target_folder}")
            self._rep003_refresh_folders_only(select_relative=relative_target)
            return True

        self._open_rep003_create_folder_dialog(parent_label=parent_label, on_submit=_submit_folder_name)

    def _open_rep003_create_folder_dialog(self, *, parent_label: str, on_submit) -> None:
        self._close_rep003_create_folder_dialog()
        parent = self._rep003_window if self._rep003_window is not None else self

        dialog = ManagedCTkToplevel(parent)
        self._rep003_create_folder_dialog = dialog
        dialog.title("Crea nuova cartella")
        dialog.resizable(False, False)
        dialog.transient(parent)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass

        width = 560
        height = 290
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(520, 260)

        try:
            parent.update_idletasks()
            parent_x = int(parent.winfo_x())
            parent_y = int(parent.winfo_y())
            parent_w = int(parent.winfo_width())
            parent_h = int(parent.winfo_height())
            pos_x = max(0, parent_x + (parent_w - width) // 2)
            pos_y = max(0, parent_y + (parent_h - height) // 2)
            dialog.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        except Exception:
            pass

        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)

        frame = ctk.CTkFrame(dialog)
        frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        info_text = f"Nuova cartella da creare in: {parent_label}"
        ctk.CTkLabel(frame, text=info_text, anchor="w", justify="left", wraplength=500).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(10, 8),
        )

        ctk.CTkLabel(frame, text="Nome nuova cartella", anchor="w", font=ctk.CTkFont("Segoe UI Semibold", 13)).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=10,
            pady=(4, 4),
        )

        name_var = tk.StringVar(value="")
        preview_prefix = "ROOT" if parent_label == "ROOT" else parent_label
        self._rep003_create_folder_preview_var.set(f"Percorso risultante: {preview_prefix}\\")

        entry = ctk.CTkEntry(frame, textvariable=name_var, height=38, font=ctk.CTkFont("Segoe UI", 13))
        entry.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._rep003_create_folder_entry = entry

        ctk.CTkLabel(
            frame,
            textvariable=self._rep003_create_folder_preview_var,
            anchor="w",
            justify="left",
            wraplength=500,
            text_color=("#4b5563", "#9ca3af"),
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))

        def _update_preview(*_args) -> None:
            raw_name = str(name_var.get() or "").strip()
            self._rep003_create_folder_preview_var.set(f"Percorso risultante: {preview_prefix}\\{raw_name}")

        name_var.trace_add("write", _update_preview)

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)

        def _on_cancel(_event=None) -> str:
            self._close_rep003_create_folder_dialog()
            return "break"

        def _on_create(_event=None) -> str:
            if on_submit(name_var.get()):
                self._close_rep003_create_folder_dialog()
            return "break"

        ctk.CTkButton(
            buttons,
            text="Crea",
            height=44,
            font=ctk.CTkFont("Segoe UI Semibold", 14),
            command=_on_create,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            buttons,
            text="Annulla",
            height=44,
            font=ctk.CTkFont("Segoe UI Semibold", 14),
            command=_on_cancel,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        dialog.bind("<Escape>", _on_cancel)
        dialog.bind("<Return>", _on_create)
        dialog.bind("<KP_Enter>", _on_create)
        entry.bind("<Escape>", _on_cancel)
        entry.bind("<Return>", _on_create)
        entry.bind("<KP_Enter>", _on_create)
        raw_entry = getattr(entry, "_entry", None)
        if raw_entry is not None:
            try:
                raw_entry.bind("<Escape>", _on_cancel)
                raw_entry.bind("<Return>", _on_create)
                raw_entry.bind("<KP_Enter>", _on_create)
            except Exception:
                pass
        dialog.protocol("WM_DELETE_WINDOW", self._close_rep003_create_folder_dialog)

        def _focus_entry() -> None:
            self._rep003_create_folder_focus_after_id = None
            try:
                if self._rep003_create_folder_entry is not None and self._rep003_create_folder_entry.winfo_exists():
                    self._rep003_create_folder_entry.focus_force()
            except Exception:
                pass

        self._rep003_create_folder_focus_after_id = dialog.after(0, _focus_entry)

    def _rep003_finalize_placeholder(self) -> None:
        if self.rep003_worker.is_running:
            return
        if self._rep003_new_tracks_entry is None or self._rep003_split_entry is None or self._rep003_general_entry is None or self._rep003_smartphone_entry is None:
            return

        new_tracks_folder = self._rep003_new_tracks_entry.get().strip()
        split_folder = self._rep003_split_entry.get().strip()
        general_folder = self._rep003_general_entry.get().strip()
        smartphone_folder = self._rep003_smartphone_entry.get().strip()

        if not new_tracks_folder or not Path(new_tracks_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Nuovi Brani non valida.", parent=self._rep003_window)
            return
        if not split_folder or not Path(split_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Suddiviso non valida.", parent=self._rep003_window)
            return
        if not general_folder or not Path(general_folder).is_dir():
            messagebox.showerror("Inserimento nuovi brani", "Cartella Repertorio Generale non valida.", parent=self._rep003_window)
            return

        try:
            ensure_folder_available(smartphone_folder)
        except Exception as error:
            messagebox.showerror("Inserimento nuovi brani", f"Cartella Smartphone/Tablet non disponibile:\n{error}", parent=self._rep003_window)
            return

        if not self._rep003_model.tracks:
            messagebox.showerror("Inserimento nuovi brani", "Nessun brano da elaborare.", parent=self._rep003_window)
            return

        if not self._rep003_model.all_managed:
            messagebox.showerror(
                "Inserimento nuovi brani",
                "Completare gli abbinamenti: tutti i brani devono risultare gestiti.",
                parent=self._rep003_window,
            )
            return

        assignments_snapshot = self._rep003_model.assignments_snapshot()
        if not assignments_snapshot:
            messagebox.showerror("Inserimento nuovi brani", "Nessun brano da elaborare.", parent=self._rep003_window)
            return

        self._rep003_session_state = REP003_SESSION_PROCESSING
        self._rep003_last_processed_sources = set(assignments_snapshot.keys())

        self._repertory_session_folder = None
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_INSERT_TRACKS, "")
        self._repertory_allow_session_log_updates = False
        self._repertory_expected_output_root = ""
        self._repertory_min_session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._repertory_selected_smartphone_folder = str(Path(smartphone_folder).expanduser().resolve())
        self._repertory_last_completed_smartphone_folder = None

        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()
        self._update_repertory_primary_action_state()

        self._reset_repertory_runtime_counters()
        self._repertory_started_at = time.monotonic()
        self._start_repertory_timer()
        self._set_repertory_ui_running_state(True)
        self._update_controls_state()

        if self._rep003_status_label is not None:
            self._rep003_status_label.configure(text="Inserimento nuovi brani in corso...")

        self._append_log("[REP003] Avvio elaborazione completa inserimento nuovi brani.")

        try:
            self.rep003_worker.start(
                new_tracks_dir=new_tracks_folder,
                split_repertory_dir=split_folder,
                general_repertory_dir=general_folder,
                smartphone_tablet_dir=smartphone_folder,
                assignments_snapshot=assignments_snapshot,
            )
        except Exception as error:
            self._stop_repertory_timer()
            self._set_repertory_ui_running_state(False)
            self._update_controls_state()
            self._update_repertory_primary_action_state()
            self._update_repertory_open_results_button_state()
            self._update_repertory_android_buttons_state()
            messagebox.showerror("Inserimento nuovi brani", str(error), parent=self._rep003_window)

    def _rep003_worker_progress(self, current: int, total: int, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_rep003_worker_progress, current, total, message)

    def _handle_rep003_worker_progress(self, current: int, total: int, message: str) -> None:
        if self._rep003_status_label is not None:
            self._rep003_status_label.configure(text=message)

    def _rep003_worker_log(self, message: str) -> None:
        self._schedule_tracked_after(0, self._append_log, message)

    def _rep003_worker_decision_required(self, payload: dict[str, Any]) -> None:
        self._schedule_tracked_after(0, self._handle_rep003_worker_decision_required, payload)

    def _handle_rep003_worker_decision_required(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return

        if self._rep003_session_policy == "UPDATE_ALL":
            self.rep003_worker.submit_decision(request_id, REP003_DECISION_UPDATE_AND_BYPASS_SESSION)
            return
        if self._rep003_session_policy == "SKIP_ALL":
            self.rep003_worker.submit_decision(request_id, REP003_DECISION_SKIP_AND_BYPASS_SESSION)
            return

        self._rep003_pending_decision_request_id = request_id
        self._open_rep003_decision_dialog(payload)

    def _submit_rep003_decision(self, decision: str) -> None:
        request_id = str(self._rep003_pending_decision_request_id or "").strip()
        if request_id:
            self.rep003_worker.submit_decision(request_id, decision)

        if decision == REP003_DECISION_UPDATE_AND_BYPASS_SESSION:
            self._rep003_session_policy = "UPDATE_ALL"
        elif decision == REP003_DECISION_SKIP_AND_BYPASS_SESSION:
            self._rep003_session_policy = "SKIP_ALL"

        self._rep003_pending_decision_request_id = None
        self._close_rep003_decision_dialog()

    def _open_rep003_decision_dialog(self, payload: dict[str, Any]) -> None:
        self._close_rep003_decision_dialog()
        parent = self._rep003_window if self._rep003_window is not None else self

        dialog = ManagedCTkToplevel(parent)
        dialog.title("Aggiorna o salta")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.geometry("900x520")
        dialog.minsize(820, 460)
        dialog.resizable(True, True)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)

        self._rep003_decision_dialog = dialog

        def _on_close() -> None:
            self._submit_rep003_decision(REP003_DECISION_SKIP_CURRENT)

        dialog.protocol("WM_DELETE_WINDOW", _on_close)

        body = ctk.CTkFrame(dialog)
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            body,
            text="Il file e gia presente nel repertorio.",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        source_path = str(payload.get("source_path", "") or "")
        ctk.CTkLabel(body, text="Nuovo file", anchor="w").grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 2))
        ctk.CTkLabel(body, text=source_path, anchor="w", justify="left", wraplength=840).grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        ctk.CTkLabel(body, text="Copie esistenti", anchor="w").grid(row=3, column=0, sticky="nw", padx=8, pady=(0, 4))
        text = ManagedCTkTextbox(body, height=230, wrap="none")
        text.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        text.configure(state="normal")
        existing_paths = payload.get("existing_paths", [])
        if not isinstance(existing_paths, list):
            existing_paths = []
        text.insert("1.0", "\n".join(str(item) for item in existing_paths))
        text.configure(state="disabled")

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        buttons.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            buttons,
            text="Aggiorna",
            command=lambda: self._submit_rep003_decision(REP003_DECISION_UPDATE_CURRENT),
            height=38,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        ctk.CTkButton(
            buttons,
            text="Salta aggiornamento",
            command=lambda: self._submit_rep003_decision(REP003_DECISION_SKIP_CURRENT),
            height=38,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 6))
        ctk.CTkButton(
            buttons,
            text="Aggiorna questo e tutti i successivi",
            command=lambda: self._submit_rep003_decision(REP003_DECISION_UPDATE_AND_BYPASS_SESSION),
            height=38,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            buttons,
            text="Mantieni questo e tutti i successivi",
            command=lambda: self._submit_rep003_decision(REP003_DECISION_SKIP_AND_BYPASS_SESSION),
            height=38,
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0))

    def _rep003_worker_completed(self, result: Rep003UpdateResult) -> None:
        self._schedule_tracked_after(0, self._handle_rep003_worker_completed, result)

    def _handle_rep003_worker_completed(self, result: Rep003UpdateResult) -> None:
        self._close_rep003_decision_dialog()
        self._rep003_pending_decision_request_id = None
        errors_count = int((getattr(result, "counters", {}) or {}).get("errors", getattr(result, "error_tracks", 0)) or 0)
        completion_state = (
            REP003_SESSION_COMPLETED_WITH_ERRORS if errors_count > 0 or not bool(getattr(result, "success", False)) else REP003_SESSION_COMPLETED
        )
        self._rep003_session_state = completion_state
        raw_session_folder = str(getattr(result, "session_folder", "") or "").strip()
        valid_session_folder = ""
        if self._is_existing_directory(raw_session_folder):
            valid_session_folder = str(Path(raw_session_folder).expanduser().resolve())
        self._repertory_session_folder = valid_session_folder or None
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_INSERT_TRACKS, self._repertory_session_folder)
        self._set_repertory_mode(REPERTORY_MODE_INSERT_TRACKS)
        if self._rep003_smartphone_entry is not None:
            selected_smartphone = str(self._rep003_smartphone_entry.get() or "").strip()
            if selected_smartphone:
                self._repertory_selected_smartphone_folder = selected_smartphone
                self._repertory_last_completed_smartphone_folder = selected_smartphone
        self._reset_rep003_operational_session(
            preserve_results=True,
            preserve_android_destination=True,
            clear_paths=False,
        )
        self._update_repertory_open_results_button_state()

        if self._rep003_status_label is not None:
            completion_hint = "Elaborazione completata con errori." if completion_state == REP003_SESSION_COMPLETED_WITH_ERRORS else "Elaborazione completata."
            self._rep003_status_label.configure(
                text=(
                    "Brani caricati: 0 | Gestiti: 0 | Da gestire: 0"
                    f" | {completion_hint} Premere \"Carica cartelle e brani\" per iniziare una nuova sessione."
                )
            )

        self._show_rep003_completion_summary(result)
        self._update_repertory_open_results_button_state()

    def _rep003_worker_error(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_rep003_worker_error, message)

    def _handle_rep003_worker_error(self, message: str) -> None:
        self._close_rep003_decision_dialog()
        self._rep003_pending_decision_request_id = None
        self._rep003_session_state = REP003_SESSION_LOADED_UNASSIGNED
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._update_repertory_primary_action_state()
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()
        if self._rep003_status_label is not None:
            self._rep003_status_label.configure(text="Errore durante l'aggiornamento")
        messagebox.showerror("Inserimento nuovi brani", message, parent=self._rep003_window)

    def _rep003_worker_cancelled(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_rep003_worker_cancelled, message)

    def _handle_rep003_worker_cancelled(self, message: str) -> None:
        self._close_rep003_decision_dialog()
        self._rep003_pending_decision_request_id = None
        self._rep003_session_state = REP003_SESSION_LOADED_UNASSIGNED
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._update_repertory_primary_action_state()
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()
        if self._rep003_status_label is not None:
            self._rep003_status_label.configure(text="Aggiornamento interrotto")
        messagebox.showinfo("Inserimento nuovi brani", message, parent=self._rep003_window)

    def _show_rep003_completion_summary(self, result: Rep003UpdateResult) -> None:
        parent = self._rep003_window if self._rep003_window is not None else self
        dialog = ManagedCTkToplevel(parent)
        dialog.title("Inserimento nuovi brani completato")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.geometry("760x420")
        dialog.minsize(720, 400)

        def _close() -> None:
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass
            try:
                parent.focus_force()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", _close)
        dialog.grid_columnconfigure(0, weight=1)

        body = ctk.CTkFrame(dialog)
        body.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        body.grid_columnconfigure(0, weight=1)

        counters = getattr(result, "counters", {}) or {}
        rows = [
            ("INSERIMENTO NUOVI BRANI COMPLETATO", True),
            (f"Brani elaborati .......... {result.processed_tracks}", False),
            (f"Brani inseriti ........... {int(counters.get('tracks_inserted', result.copied_tracks))}", False),
            (f"Brani aggiornati ......... {int(counters.get('tracks_updated', result.updated_tracks))}", False),
            (f"Copie create nel repertorio .... {int(counters.get('split_copied', 0))}", False),
            (f"Copie aggiornate repertorio .... {int(counters.get('split_updated', 0))}", False),
            (f"Copie create repertorio generale .... {int(counters.get('general_copied', 0))}", False),
            (f"Copie aggiornate repertorio generale .... {int(counters.get('general_updated', 0))}", False),
            (f"Copie create Android .... {int(counters.get('android_copied', 0))}", False),
            (f"Copie aggiornate Android .... {int(counters.get('android_updated', 0))}", False),
            (f"Backup creati ............ {int(counters.get('backups_created', 0))}", False),
            (f"Errori ................... {int(counters.get('errors', result.error_tracks))}", False),
        ]

        for text, emphasized in rows:
            ctk.CTkLabel(
                body,
                text=text,
                anchor="w",
                justify="left",
                font=ctk.CTkFont(weight="bold") if emphasized else None,
            ).pack(anchor="w", fill="x", padx=10, pady=1)

        button_row = ctk.CTkFrame(body, fg_color="transparent")
        button_row.pack(fill="x", padx=10, pady=(14, 4))
        button_row.grid_columnconfigure(0, weight=1)

        close_button = ctk.CTkButton(
            button_row,
            text="Chiudi",
            command=_close,
            height=38,
        )
        close_button.grid(row=0, column=0, sticky="ew")
        try:
            close_button.focus_force()
        except Exception:
            pass

        try:
            parent.wait_window(dialog)
        except Exception:
            _close()

    def open_repertory_new_tracks_window(self) -> None:
        self.open_repertory_organizer_window()
        if self._repertory_dialog is None:
            return
        self._set_repertory_mode(REPERTORY_MODE_INSERT_TRACKS)
        self._apply_repertory_mode_layout()
        try:
            self._repertory_dialog.deiconify()
            self._repertory_dialog.lift()
            self._repertory_dialog.focus_force()
        except Exception:
            pass

    def open_repertory_organizer_window(self) -> None:
        if self.repertory_worker.is_running:
            messagebox.showwarning(
                "Organizza repertorio",
                "Una organizzazione repertorio e gia in corso.",
                parent=self,
            )
            return

        if self._repertory_dialog is not None and self._repertory_dialog.winfo_exists():
            self._repertory_dialog.deiconify()
            self._repertory_dialog.lift()
            self._repertory_dialog.focus_force()
            return

        window = ManagedCTkToplevel(self)
        window.title("Organizza repertorio")
        screen_width = max(920, int(window.winfo_screenwidth()))
        screen_height = max(720, int(window.winfo_screenheight()))
        window_width, window_height, _horizontal_margin, _vertical_margin = self._compute_repertory_window_geometry(
            screen_width,
            screen_height,
        )
        x_position = max(0, (screen_width - window_width) // 2)
        y_position = max(0, (screen_height - window_height) // 2)
        window.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        window.minsize(900, 640)
        window.resizable(True, True)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_repertory_window)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=0)
        window.grid_rowconfigure(1, weight=1)
        window.grid_rowconfigure(2, weight=0)
        self._repertory_dialog = window

        mode_bar = ctk.CTkFrame(window, fg_color="transparent")
        mode_bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        mode_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(mode_bar, text="Operazione", anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8))
        radios_frame = ctk.CTkFrame(mode_bar, fg_color="transparent")
        radios_frame.grid(row=0, column=1, sticky="w")
        self._repertory_mode_selector = radios_frame
        self._repertory_mode_radios = []
        for idx, mode in enumerate((REPERTORY_MODE_UPDATE, REPERTORY_MODE_DIAGNOSTICS, REPERTORY_MODE_INSERT_TRACKS)):
            radio = ctk.CTkRadioButton(
                radios_frame,
                text=REPERTORY_MODE_LABELS[mode],
                variable=self._repertory_mode_var,
                value=mode,
                command=self._on_repertory_mode_selected,
            )
            radio.grid(row=0, column=idx, sticky="w", padx=(0, 14) if idx < 2 else 0)
            self._repertory_mode_radios.append(radio)

        frame = ctk.CTkFrame(window)
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, weight=0)
        for fixed_row in (0, 1, 2, 3, 4, 5, 6, 7, 8):
            frame.grid_rowconfigure(fixed_row, weight=0)
        frame.grid_rowconfigure(10, weight=1)
        self._repertory_mode_frame = frame

        rep003_frame = ctk.CTkFrame(window)
        rep003_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        rep003_frame.grid_columnconfigure(0, weight=1)
        rep003_frame.grid_rowconfigure(1, weight=1)
        rep003_frame.grid_remove()
        self._build_rep003_panel(rep003_frame)

        self._repertory_updates_label = ctk.CTkLabel(frame, text="Cartella aggiornamenti", anchor="w")
        self._repertory_updates_label.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self._repertory_updates_entry = ctk.CTkEntry(frame)
        self._repertory_updates_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(10, 4))
        self._repertory_updates_entry.bind("<FocusOut>", self._on_repertory_diagnostics_paths_changed)
        self._repertory_updates_entry.bind("<Return>", self._on_repertory_diagnostics_paths_changed)
        updates_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_repertory_updates_folder)
        updates_browse.grid(row=0, column=2, padx=(0, 10), pady=(10, 4))

        self._repertory_library_label = ctk.CTkLabel(frame, text="Cartella repertorio", anchor="w")
        self._repertory_library_label.grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self._repertory_library_entry = ctk.CTkEntry(frame)
        self._repertory_library_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=4)
        self._repertory_library_entry.bind("<FocusOut>", self._on_repertory_diagnostics_paths_changed)
        self._repertory_library_entry.bind("<Return>", self._on_repertory_diagnostics_paths_changed)
        library_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_repertory_library_folder)
        library_browse.grid(row=1, column=2, padx=(0, 10), pady=4)

        self._repertory_general_label = ctk.CTkLabel(frame, text="Cartella repertorio generale", anchor="w")
        self._repertory_general_label.grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self._repertory_general_entry = ctk.CTkEntry(frame)
        self._repertory_general_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)
        general_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_repertory_general_folder)
        general_browse.grid(row=2, column=2, padx=(0, 10), pady=4)

        self._repertory_results_label = ctk.CTkLabel(frame, text="Cartella risultati", anchor="w")
        self._repertory_results_label.grid(row=3, column=0, sticky="w", padx=10, pady=4)
        self._repertory_results_entry = ctk.CTkEntry(frame)
        self._repertory_results_entry.grid(row=3, column=1, sticky="ew", padx=10, pady=4)
        results_browse = ctk.CTkButton(frame, text="Sfoglia", width=84, command=self._select_repertory_results_folder)
        results_browse.grid(row=3, column=2, padx=(0, 10), pady=4)

        self._repertory_smartphone_label = ctk.CTkLabel(frame, text="Cartella Smartphone/Tablet", anchor="w")
        self._repertory_smartphone_label.grid(row=4, column=0, sticky="w", padx=10, pady=4)
        self._repertory_smartphone_entry = ctk.CTkEntry(frame)
        self._repertory_smartphone_entry.grid(row=4, column=1, sticky="ew", padx=10, pady=4)
        self._repertory_smartphone_entry.bind("<FocusOut>", self._on_repertory_diagnostics_paths_changed)
        self._repertory_smartphone_entry.bind("<Return>", self._on_repertory_diagnostics_paths_changed)
        smartphone_browse = ctk.CTkButton(
            frame,
            text="Sfoglia",
            width=84,
            command=self._select_repertory_smartphone_folder,
        )
        smartphone_browse.grid(row=4, column=2, padx=(0, 10), pady=4)
        self._repertory_smartphone_browse_button = smartphone_browse

        self._repertory_updates_entry_tooltip = self._add_tooltip(
            self._repertory_updates_entry,
            "Seleziona la cartella contenente i file MP3 da confrontare e aggiornare.",
        )
        self._repertory_library_entry_tooltip = self._add_tooltip(
            self._repertory_library_entry,
            "Seleziona la root del repertorio organizzato in cartelle e sottocartelle.",
        )
        self._repertory_general_entry_tooltip = self._add_tooltip(
            self._repertory_general_entry,
            "Seleziona la cartella piatta contenente una sola copia di ogni brano.",
        )
        self._repertory_results_entry_tooltip = self._add_tooltip(
            self._repertory_results_entry,
            "Seleziona la cartella dei risultati, se applicabile.",
        )
        self._repertory_smartphone_entry_tooltip = self._add_tooltip(
            self._repertory_smartphone_entry,
            "Seleziona la cartella di appoggio che conterra i file da trasferire al dispositivo Android.",
        )
        self._add_tooltip(updates_browse, "Seleziona la cartella contenente i file MP3 da confrontare e aggiornare.")
        self._add_tooltip(library_browse, "Seleziona la root del repertorio organizzato in cartelle e sottocartelle.")
        self._add_tooltip(general_browse, "Seleziona la cartella piatta contenente una sola copia di ogni brano.")
        self._add_tooltip(results_browse, "Seleziona la cartella dei risultati, se applicabile.")
        self._add_tooltip(
            smartphone_browse,
            "Seleziona la cartella di appoggio che conterra i file da trasferire al dispositivo Android.",
        )

        backup_check = ctk.CTkCheckBox(
            frame,
            text="Crea copia di sicurezza dei file sostituiti",
            variable=self._repertory_backup_var,
        )
        backup_check.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        self._repertory_backup_check = backup_check
        self._repertory_general_browse_button = general_browse
        self._repertory_results_browse_button = results_browse

        diagnostics_tree_container = ctk.CTkFrame(frame)
        diagnostics_tree_container.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=10, pady=(2, 6))
        diagnostics_tree_container.grid_columnconfigure(0, weight=1)
        diagnostics_tree_container.grid_rowconfigure(1, weight=1)

        diagnostics_tools_row = ctk.CTkFrame(diagnostics_tree_container, fg_color="transparent")
        diagnostics_tools_row.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        diagnostics_tools_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            diagnostics_tools_row,
            text="Sottocartelle da includere nella diagnosi",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        diagnostics_refresh_button = ctk.CTkButton(
            diagnostics_tools_row,
            text="Aggiorna elenco",
            width=110,
            command=self._refresh_repertory_diagnostics_folder_tree,
        )
        diagnostics_refresh_button.grid(row=0, column=1, padx=(8, 4), sticky="e")
        diagnostics_select_all_button = ctk.CTkButton(
            diagnostics_tools_row,
            text="Seleziona tutte",
            width=110,
            command=self._select_all_repertory_diagnostics_nodes,
        )
        diagnostics_select_all_button.grid(row=0, column=2, padx=4, sticky="e")
        diagnostics_deselect_all_button = ctk.CTkButton(
            diagnostics_tools_row,
            text="Deseleziona tutte",
            width=120,
            command=self._deselect_all_repertory_diagnostics_nodes,
        )
        diagnostics_deselect_all_button.grid(row=0, column=3, padx=(4, 0), sticky="e")

        diagnostics_tree_scrollable = ctk.CTkScrollableFrame(diagnostics_tree_container, height=150)
        diagnostics_tree_scrollable.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 6))

        self._repertory_diagnostics_tree_container = diagnostics_tree_container
        self._repertory_diagnostics_tree_scrollable = diagnostics_tree_scrollable
        self._repertory_diagnostics_refresh_button = diagnostics_refresh_button
        self._repertory_diagnostics_select_all_button = diagnostics_select_all_button
        self._repertory_diagnostics_deselect_all_button = diagnostics_deselect_all_button

        self._repertory_status_label = ctk.CTkLabel(frame, text="Pronto", anchor="w")
        self._repertory_status_label.grid(row=7, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

        counter_frame = ctk.CTkFrame(frame, fg_color="transparent")
        counter_frame.grid(row=8, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 6))
        counter_frame.grid_columnconfigure((0, 1), weight=1)

        self._repertory_file_counter_label = ctk.CTkLabel(counter_frame, text="File elaborati: 0 / 0", anchor="w")
        self._repertory_file_counter_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=1)
        self._repertory_matches_label = ctk.CTkLabel(counter_frame, text="Corrispondenze trovate: 0", anchor="w")
        self._repertory_matches_label.grid(row=0, column=1, sticky="w", pady=1)
        self._repertory_updated_label = ctk.CTkLabel(counter_frame, text="File aggiornati: 0", anchor="w")
        self._repertory_updated_label.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=1)
        self._repertory_not_found_label = ctk.CTkLabel(counter_frame, text="File non trovati: 0", anchor="w")
        self._repertory_not_found_label.grid(row=1, column=1, sticky="w", pady=1)
        self._repertory_errors_label = ctk.CTkLabel(counter_frame, text="Errori: 0", anchor="w")
        self._repertory_errors_label.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=1)
        self._repertory_elapsed_label = ctk.CTkLabel(counter_frame, text="Tempo trascorso: 00:00:00", anchor="w")
        self._repertory_elapsed_label.grid(row=2, column=1, sticky="w", pady=1)
        self._repertory_eta_label = ctk.CTkLabel(counter_frame, text="Tempo restante stimato: --", anchor="w")
        self._repertory_eta_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self._repertory_progress_bar = ctk.CTkProgressBar(frame)
        self._repertory_progress_bar.grid(row=9, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 6))
        self._repertory_progress_bar.set(0)

        self._repertory_log_box = ManagedCTkTextbox(frame, height=260)
        self._repertory_log_box.grid(row=10, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 8))
        self._repertory_log_box.configure(state="disabled")
        frame.grid_rowconfigure(10, weight=1)

        button_row = ctk.CTkFrame(window, fg_color="transparent")
        button_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        button_row.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

        self._repertory_start_button = ctk.CTkButton(
            button_row,
            text="Avvia aggiornamento",
            height=42,
            command=self._start_repertory_organization,
        )
        self._repertory_start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._repertory_stop_button = ctk.CTkButton(
            button_row,
            text="Interrompi",
            height=42,
            command=self._request_stop_repertory_organization,
            state="disabled",
        )
        self._repertory_stop_button.grid(row=0, column=1, sticky="ew", padx=(6, 6))

        self._repertory_open_results_button = ctk.CTkButton(
            button_row,
            text="Apri cartella risultati",
            height=42,
            state="disabled",
            command=self._open_repertory_results_folder,
        )
        self._repertory_open_results_button.grid(row=0, column=2, sticky="ew", padx=(6, 6))

        self._repertory_open_smartphone_button = ctk.CTkButton(
            button_row,
            text="Apri cartella Smartphone/Tablet",
            height=42,
            state="disabled",
            command=self._open_repertory_smartphone_folder,
        )
        self._repertory_open_smartphone_button.grid(row=0, column=3, sticky="ew", padx=(6, 6))

        self._repertory_reset_smartphone_button = ctk.CTkButton(
            button_row,
            text="Reset cartella Smartphone/Tablet",
            height=42,
            state="disabled",
            command=self._reset_repertory_smartphone_folder,
        )
        self._repertory_reset_smartphone_button.grid(row=0, column=4, sticky="ew", padx=(6, 6))

        self._repertory_close_button = ctk.CTkButton(
            button_row,
            text="Chiudi",
            height=42,
            command=self._close_repertory_window,
        )
        self._repertory_close_button.grid(row=0, column=5, sticky="ew", padx=(6, 0))

        self._repertory_start_button_tooltip = self._add_tooltip(
            self._repertory_start_button,
            "Avvia il confronto e l'aggiornamento dei file selezionati.",
        )
        self._repertory_stop_button_tooltip = self._add_tooltip(
            self._repertory_stop_button,
            "Richiede l'interruzione sicura dell'elaborazione in corso.",
        )
        self._repertory_open_results_button_tooltip = self._add_tooltip(
            self._repertory_open_results_button,
            "Apre la cartella della sessione contenente report, log e backup.",
        )
        self._repertory_open_smartphone_button_tooltip = self._add_tooltip(
            self._repertory_open_smartphone_button,
            "Apre la cartella di appoggio Android usata nell'ultima elaborazione completata.",
        )
        self._repertory_reset_smartphone_button_tooltip = self._add_tooltip(
            self._repertory_reset_smartphone_button,
            "Elimina il contenuto della cartella Android selezionata, mantenendo la cartella principale.",
        )
        self._repertory_backup_check_tooltip = self._add_tooltip(
            backup_check,
            "Salva le versioni precedenti dei file che verranno effettivamente modificati.",
        )
        self._repertory_diagnostics_refresh_tooltip = self._add_tooltip(
            diagnostics_refresh_button,
            "Rilegge l'alberatura senza avviare la diagnosi.",
        )
        self._repertory_diagnostics_select_all_tooltip = self._add_tooltip(
            diagnostics_select_all_button,
            "Seleziona tutte le cartelle disponibili per la diagnosi.",
        )
        self._repertory_diagnostics_deselect_all_tooltip = self._add_tooltip(
            diagnostics_deselect_all_button,
            "Rimuove tutte le selezioni dall'alberatura.",
        )

        self._repertory_path_widgets = [
            self._repertory_updates_entry,
            self._repertory_library_entry,
            self._repertory_general_entry,
            self._repertory_results_entry,
            self._repertory_smartphone_entry,
            updates_browse,
            library_browse,
            general_browse,
            results_browse,
            smartphone_browse,
            backup_check,
            diagnostics_refresh_button,
            diagnostics_select_all_button,
            diagnostics_deselect_all_button,
        ]

        self._clear_repertory_mode_path_fields()
        self._validate_repertory_diagnostics_paths(show_message=False)

        self._append_log("Apertura finestra Organizza repertorio.")
        self._reset_repertory_runtime_counters()
        self._repertory_close_requested = False
        self._clear_repertory_diagnostics_tree_widgets()
        self._set_repertory_mode(REPERTORY_MODE_UPDATE)
        self._apply_repertory_mode_layout()

    def _close_repertory_window(self) -> None:
        if self.repertory_diagnostics_worker.is_running:
            should_interrupt = messagebox.askyesno(
                "Organizza repertorio",
                "E in corso una diagnosi repertorio. Vuoi interromperla?",
                parent=self._repertory_dialog,
            )
            if should_interrupt:
                self.repertory_diagnostics_worker.cancel()
                self._append_repertory_log("Richiesta interruzione diagnosi inviata.")
                self._repertory_close_requested = True
            return

        if self.repertory_worker.is_running:
            should_interrupt = messagebox.askyesno(
                "Organizza repertorio",
                "E in corso una organizzazione repertorio. Vuoi interromperla?",
                parent=self._repertory_dialog,
            )
            if should_interrupt:
                self.repertory_worker.cancel()
                self._append_repertory_log("Richiesta interruzione inviata.")
                if self._repertory_pending_decision_request_id:
                    self.repertory_worker.submit_decision(
                        self._repertory_pending_decision_request_id,
                        "SKIP_CURRENT",
                    )
                    self._repertory_pending_decision_request_id = None
                self._close_repertory_decision_dialog()
                self._repertory_close_requested = True
            return

        if self.rep003_worker.is_running:
            should_interrupt = messagebox.askyesno(
                "Organizza repertorio",
                "E in corso l'inserimento nuovi brani. Vuoi interromperlo?",
                parent=self._repertory_dialog,
            )
            if should_interrupt:
                self.rep003_worker.cancel()
                self._append_repertory_log("Richiesta di interruzione inserimento nuovi brani inviata.")
                self._repertory_close_requested = True
            return

        self._rep003_update_session_state_from_model()
        if self._rep003_has_pending_assignments():
            should_close = self._rep003_confirm_discard_pending_assignments(for_mode_switch=False)
            if not should_close:
                return
            self._rep003_discard_pending_assignments_state()

        self._finalize_repertory_window_close()

    def _show_rep003_confirmation_dialog(
        self,
        *,
        title: str,
        body: str,
        confirm_label: str,
        cancel_label: str,
    ) -> bool:
        parent = self._repertory_dialog if self._repertory_dialog is not None else self
        dialog = ManagedCTkToplevel(parent)
        dialog.title(title)
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.geometry("760x360")
        dialog.minsize(700, 320)

        accepted = {"value": False}

        def _close(value: bool) -> None:
            accepted["value"] = bool(value)
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close(False))
        dialog.bind("<Escape>", lambda _event: _close(False))
        dialog.grid_columnconfigure(0, weight=1)

        body_frame = ctk.CTkFrame(dialog)
        body_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        body_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body_frame,
            text=body,
            justify="left",
            anchor="w",
            wraplength=700,
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 12))

        buttons = ctk.CTkFrame(body_frame, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        buttons.grid_columnconfigure((0, 1), weight=1)

        confirm_button = ctk.CTkButton(
            buttons,
            text=confirm_label,
            command=lambda: _close(True),
            height=40,
        )
        confirm_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        cancel_button = ctk.CTkButton(
            buttons,
            text=cancel_label,
            command=lambda: _close(False),
            height=40,
        )
        cancel_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        try:
            cancel_button.focus_set()
        except Exception:
            pass

        parent.wait_window(dialog)
        return bool(accepted["value"])

    def _widget_exists(self, widget: Any) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except (tk.TclError, RuntimeError, AttributeError):
            return False

    def _schedule_tracked_after(self, delay_ms: int, callback, *args):
        if self._is_destroying or not self._widget_exists(self):
            return None

        job_id = None

        def _runner() -> None:
            nonlocal job_id
            if job_id is not None:
                self._tracked_after_jobs.discard(job_id)
                job_id = None
            if self._is_destroying or not self._widget_exists(self):
                return
            callback(*args)

        try:
            job_id = self.after(delay_ms, _runner)
        except (tk.TclError, RuntimeError):
            return None

        self._tracked_after_jobs.add(job_id)
        return job_id

    def _cancel_tracked_after_job(self, job_id: Any) -> None:
        if not job_id:
            return
        self._tracked_after_jobs.discard(job_id)
        try:
            self.after_cancel(job_id)
        except (tk.TclError, RuntimeError):
            pass

    def _cancel_tracked_after_jobs(self) -> None:
        pending_jobs = list(self._tracked_after_jobs)
        self._tracked_after_jobs.clear()
        for job_id in pending_jobs:
            try:
                self.after_cancel(job_id)
            except (tk.TclError, RuntimeError):
                pass

    def _cancel_matching_after_scripts(self, *fragments: str) -> None:
        try:
            pending_jobs = self.tk.splitlist(self.tk.call("after", "info"))
        except (tk.TclError, RuntimeError):
            return

        for job_id in pending_jobs:
            try:
                callback_info = self.tk.splitlist(self.tk.call("after", "info", job_id))
            except (tk.TclError, RuntimeError):
                continue
            callback_text = " ".join(str(item) for item in callback_info)
            if any(fragment in callback_text for fragment in fragments):
                try:
                    self.tk.call("after", "cancel", job_id)
                except (tk.TclError, RuntimeError):
                    pass

    def _cleanup_tooltips(self, owner: Any | None = None, tooltips: list[Tooltip] | None = None) -> None:
        if tooltips is not None:
            targets = list(tooltips)
        elif owner is None:
            targets = list(self.tooltips)
        else:
            owner_path = str(owner)
            targets = []
            for tooltip in self.tooltips:
                try:
                    widget_path = str(tooltip.widget)
                except Exception:
                    widget_path = ""
                if widget_path == owner_path or widget_path.startswith(owner_path + "."):
                    targets.append(tooltip)

        if not targets:
            return

        for tooltip in targets:
            try:
                tooltip.destroy()
            except Exception:
                pass
        self.tooltips = [tooltip for tooltip in self.tooltips if tooltip not in targets]

    @staticmethod
    def _normalize_repertory_mode(value: Any) -> str:
        text = str(value or "").strip()
        lowered = text.casefold()
        if lowered in (REPERTORY_MODE_UPDATE, REPERTORY_MODE_DIAGNOSTICS, REPERTORY_MODE_INSERT_TRACKS):
            return lowered
        return REPERTORY_MODE_BY_LABEL.get(lowered, REPERTORY_MODE_UPDATE)

    def _set_repertory_mode(self, mode: Any) -> str:
        normalized = self._normalize_repertory_mode(mode)
        self._repertory_mode_var.set(normalized)
        self._repertory_mode_label_var.set(REPERTORY_MODE_LABELS[normalized])
        return normalized

    def _is_any_repertory_worker_running(self) -> bool:
        return bool(
            self.repertory_worker.is_running
            or self.repertory_diagnostics_worker.is_running
            or self.rep003_worker.is_running
        )

    def _can_start_repertory_update_mode(self) -> bool:
        updates_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        repertory_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""
        general_dir = self._repertory_general_entry.get().strip() if self._repertory_general_entry is not None else ""
        results_dir = self._repertory_results_entry.get().strip() if self._repertory_results_entry is not None else ""
        smartphone_dir = self._repertory_smartphone_entry.get().strip() if self._repertory_smartphone_entry is not None else str(self._repertory_smartphone_root)

        if not updates_dir or not Path(updates_dir).is_dir():
            return False
        if not repertory_dir or not Path(repertory_dir).is_dir():
            return False
        if not general_dir or not Path(general_dir).is_dir():
            return False
        if not results_dir:
            return False
        if not smartphone_dir:
            return False

        try:
            if self._is_filesystem_root(Path(smartphone_dir).expanduser().resolve()):
                return False
        except Exception:
            return False

        return True

    def _can_start_repertory_diagnostics_mode(self) -> bool:
        split_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        general_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""
        if not split_dir or not Path(split_dir).is_dir():
            return False
        if not general_dir or not Path(general_dir).is_dir():
            return False
        if self._repertory_paths_collide(split_dir, general_dir):
            return False

        selected_relative_roots, _excluded_relative_roots, include_root_files = self._build_repertory_diagnostics_selection_payload()
        selected_folder_count = self._count_selected_repertory_folders_with_mp3(split_dir, selected_relative_roots)
        root_mp3_count = self._count_root_mp3_direct(split_dir)
        return bool(selected_folder_count > 0 or (bool(include_root_files) and root_mp3_count > 0))

    def _can_start_rep003_mode(self) -> bool:
        return bool(self._rep003_model.tracks) and bool(self._rep003_model.all_managed)

    def _update_repertory_primary_action_state(self) -> None:
        if self._repertory_start_button is None:
            return

        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode == REPERTORY_MODE_INSERT_TRACKS:
            text = "Conferma e avvia aggiornamento"
            can_start = self._can_start_rep003_mode()
        elif mode == REPERTORY_MODE_DIAGNOSTICS:
            text = "Avvia diagnosi"
            can_start = self._can_start_repertory_diagnostics_mode()
        else:
            text = "Avvia aggiornamento"
            can_start = self._can_start_repertory_update_mode()

        running = self._is_any_repertory_worker_running()
        self._repertory_start_button.configure(
            text=text,
            command=self._start_repertory_organization,
            state="disabled" if running or not can_start else "normal",
        )

    def _rep003_has_pending_assignments(self) -> bool:
        if self.rep003_worker.is_running:
            return False
        if self._rep003_session_state != REP003_SESSION_ASSIGNMENTS_PENDING:
            return False
        return self._rep003_count_assigned_tracks() > 0

    def _rep003_count_assigned_tracks(self) -> int:
        assigned = 0
        for item in self._rep003_model.tracks:
            if item.status == STATUS_GESTITO and bool(item.destinations):
                assigned += 1
        return assigned

    def _rep003_update_session_state_from_model(self) -> None:
        if self.rep003_worker.is_running:
            self._rep003_session_state = REP003_SESSION_PROCESSING
            return
        if self._rep003_session_state in {
            REP003_SESSION_COMPLETED,
            REP003_SESSION_COMPLETED_WITH_ERRORS,
            REP003_SESSION_READY_FOR_NEW_SESSION,
        }:
            return
        if not self._rep003_model.tracks:
            self._rep003_session_state = REP003_SESSION_NOT_LOADED
            return
        if self._rep003_count_assigned_tracks() > 0:
            self._rep003_session_state = REP003_SESSION_ASSIGNMENTS_PENDING
            return
        self._rep003_session_state = REP003_SESSION_LOADED_UNASSIGNED

    def _reset_rep003_operational_session(
        self,
        *,
        preserve_results: bool,
        preserve_android_destination: bool,
        clear_paths: bool,
    ) -> None:
        self._close_rep003_decision_dialog()
        self._close_rep003_create_folder_dialog()
        self._rep003_pending_decision_request_id = None
        self._rep003_session_policy = "ASK"
        self._rep003_last_processed_sources = set()
        self._rep003_show_managed_var.set(True)
        self._rep003_sort_key = REP003_SORT_NAME
        self._rep003_sort_reverse = False
        self._rep003_folder_sort_key = REP003_SORT_FOLDER_RELATIVE
        self._rep003_folder_sort_reverse = False
        self._rep003_track_row_by_iid = {}
        self._rep003_folder_iid_by_relative = {}
        self._rep003_model = NewTracksAssignmentModel()

        if not preserve_results:
            self._repertory_session_folder = None
            self._set_repertory_results_folder_for_mode(REPERTORY_MODE_INSERT_TRACKS, "")
        if not preserve_android_destination:
            self._repertory_selected_smartphone_folder = None
            self._repertory_last_completed_smartphone_folder = None

        if clear_paths:
            self._clear_repertory_mode_path_fields()

        self._rep003_refresh_folders_tree(clear_selection=True)
        self._rep003_refresh_tracks_tree(clear_selection=True)
        if self._rep003_tracks_tree is not None:
            try:
                self._rep003_tracks_tree.selection_remove(self._rep003_tracks_tree.selection())
            except Exception:
                pass
        if self._rep003_folders_tree is not None:
            try:
                self._rep003_folders_tree.selection_remove(self._rep003_folders_tree.selection())
            except Exception:
                pass

        self._rep003_session_state = (
            REP003_SESSION_READY_FOR_NEW_SESSION
            if preserve_results
            else REP003_SESSION_NOT_LOADED
        )

        self._rep003_update_status()
        self._reset_repertory_runtime_counters()
        if clear_paths:
            return

        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._update_repertory_primary_action_state()
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()
        self._rep003_update_assignment_buttons_state()

    def _rep003_discard_pending_assignments_state(self) -> None:
        self._reset_rep003_operational_session(
            preserve_results=bool(self._active_repertory_results_folder()),
            preserve_android_destination=bool(str(self._active_repertory_smartphone_folder() or "").strip()),
            clear_paths=False,
        )

    def _rep003_confirm_discard_pending_assignments(self, *, for_mode_switch: bool) -> bool:
        pending_count = self._rep003_count_assigned_tracks()
        if pending_count <= 0:
            return True

        title = "Abbinamenti non ancora elaborati"
        if for_mode_switch:
            confirm_label = "Cambia modalita e annulla gli abbinamenti"
        else:
            confirm_label = "Chiudi e annulla gli abbinamenti"

        return self._show_rep003_confirmation_dialog(
            title=title,
            body=(
                "Sono presenti abbinamenti di nuovi brani che non sono ancora stati\n"
                "elaborati.\n\n"
                "Chiudendo la finestra, gli abbinamenti memorizzati nella sessione corrente\n"
                "andranno persi.\n\n"
                "Vuoi continuare?"
            ),
            confirm_label=confirm_label,
            cancel_label="Torna alla finestra",
        )

    def _on_repertory_mode_selected(self, selected_value: str | None = None) -> None:
        requested = self._normalize_repertory_mode(selected_value if selected_value is not None else self._repertory_mode_var.get())
        current_mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if current_mode == REPERTORY_MODE_INSERT_TRACKS and requested != REPERTORY_MODE_INSERT_TRACKS:
            self._rep003_update_session_state_from_model()
            if self._rep003_has_pending_assignments():
                proceed = self._rep003_confirm_discard_pending_assignments(for_mode_switch=True)
                if not proceed:
                    self._set_repertory_mode(current_mode)
                    return
                self._rep003_discard_pending_assignments_state()
        self._repertory_selected_smartphone_folder = None
        self._repertory_last_completed_smartphone_folder = None
        self._set_repertory_mode(requested)
        self._clear_repertory_mode_path_fields()
        self._apply_repertory_mode_layout()

    def _clear_repertory_mode_path_fields(self) -> None:
        entries = [
            self._repertory_updates_entry,
            self._repertory_library_entry,
            self._repertory_general_entry,
            self._repertory_results_entry,
            self._repertory_smartphone_entry,
            self._rep003_new_tracks_entry,
            self._rep003_split_entry,
            self._rep003_general_entry,
            self._rep003_smartphone_entry,
        ]
        for entry in entries:
            if entry is None:
                continue
            self._replace_entry(entry, "")

    def _apply_repertory_mode_layout(self) -> None:
        mode = self._set_repertory_mode(self._repertory_mode_var.get())
        diagnostics_mode = mode == REPERTORY_MODE_DIAGNOSTICS
        insert_tracks_mode = mode == REPERTORY_MODE_INSERT_TRACKS

        self._update_repertory_mode_tooltips(mode)

        if self._repertory_mode_frame is not None:
            try:
                if insert_tracks_mode:
                    self._repertory_mode_frame.grid_remove()
                else:
                    self._repertory_mode_frame.grid()
            except Exception:
                pass

        if self._rep003_panel_frame is not None:
            try:
                if insert_tracks_mode:
                    self._rep003_panel_frame.grid()
                else:
                    self._rep003_panel_frame.grid_remove()
            except Exception:
                pass

        if hasattr(self, "_repertory_updates_label") and self._repertory_updates_label is not None:
            self._repertory_updates_label.configure(
                text="Cartella Repertorio suddiviso" if diagnostics_mode else "Cartella aggiornamenti"
            )
        if hasattr(self, "_repertory_library_label") and self._repertory_library_label is not None:
            self._repertory_library_label.configure(
                text="Cartella Repertorio Generale" if diagnostics_mode else "Cartella repertorio suddiviso"
            )
        if hasattr(self, "_repertory_general_label") and self._repertory_general_label is not None:
            self._repertory_general_label.configure(text="Cartella repertorio generale")
        if hasattr(self, "_repertory_results_label") and self._repertory_results_label is not None:
            self._repertory_results_label.configure(text="Cartella Diagnosi" if diagnostics_mode else "Cartella risultati")
        if self._repertory_start_button is not None:
            if insert_tracks_mode:
                self._repertory_start_button.configure(text="Conferma e avvia aggiornamento")
            else:
                self._repertory_start_button.configure(text="Avvia diagnosi" if diagnostics_mode else "Avvia aggiornamento")
        if self._repertory_open_results_button is not None:
            self._update_repertory_open_results_button_state()

        widgets_to_hide = []
        if hasattr(self, "_repertory_smartphone_label") and self._repertory_smartphone_label is not None:
            widgets_to_hide.extend([
                self._repertory_smartphone_label,
                self._repertory_smartphone_entry,
                self._repertory_smartphone_browse_button,
            ])
        if hasattr(self, "_repertory_general_label") and self._repertory_general_label is not None:
            widgets_to_hide.extend([self._repertory_general_label, self._repertory_general_entry, self._repertory_general_browse_button])
        if hasattr(self, "_repertory_results_label") and self._repertory_results_label is not None:
            widgets_to_hide.extend([self._repertory_results_label, self._repertory_results_entry, self._repertory_results_browse_button])
        if hasattr(self, "_repertory_backup_check") and self._repertory_backup_check is not None:
            widgets_to_hide.append(self._repertory_backup_check)
        if self._repertory_open_smartphone_button is not None:
            widgets_to_hide.append(self._repertory_open_smartphone_button)
        if self._repertory_reset_smartphone_button is not None:
            widgets_to_hide.append(self._repertory_reset_smartphone_button)
        for widget in widgets_to_hide:
            try:
                if diagnostics_mode or insert_tracks_mode:
                    widget.grid_remove()
                else:
                    widget.grid()
            except Exception:
                pass

        if self._repertory_diagnostics_tree_container is not None:
            try:
                if diagnostics_mode:
                    self._repertory_diagnostics_tree_container.grid()
                else:
                    self._repertory_diagnostics_tree_container.grid_remove()
            except Exception:
                pass

        rep003_widgets = [
            self._rep003_new_tracks_entry,
            self._rep003_split_entry,
            self._rep003_general_entry,
            self._rep003_smartphone_entry,
            self._rep003_load_button,
            self._rep003_assign_button,
            self._rep003_remove_button,
            self._rep003_show_managed_switch,
            self._rep003_tracks_tree,
            self._rep003_folders_tree,
            self._rep003_status_label,
        ]
        for widget in rep003_widgets:
            try:
                if insert_tracks_mode:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                pass

        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(
                text=(
                    "Pronto - modalità Inserimento nuovi brani"
                    if insert_tracks_mode
                    else "Pronto - modalità Diagnosi Repertorio"
                    if diagnostics_mode
                    else "Pronto"
                )
            )

        if diagnostics_mode:
            self._refresh_repertory_diagnostics_folder_tree()
        elif insert_tracks_mode:
            self._rep003_refresh_folders_tree(clear_selection=False)
            self._rep003_refresh_tracks_tree(clear_selection=False)
            self._rep003_update_status()
            self._rep003_update_session_state_from_model()
            self._rep003_update_assignment_buttons_state()
            self._rep003_update_finalize_button_state()
        self._update_repertory_primary_action_state()
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()

    def _update_repertory_mode_tooltips(self, mode: str) -> None:
        if self._repertory_start_button_tooltip is not None:
            if mode == REPERTORY_MODE_DIAGNOSTICS:
                self._repertory_start_button_tooltip.text = "Confronta i due repertori e genera report e cartelle di esito."
            elif mode == REPERTORY_MODE_INSERT_TRACKS:
                self._repertory_start_button_tooltip.text = (
                    "In Fase 1 verifica soltanto che tutti gli abbinamenti siano completi. "
                    "L'elaborazione reale sara implementata nella Fase 2."
                )
            else:
                self._repertory_start_button_tooltip.text = "Avvia il confronto e l'aggiornamento dei file selezionati."
        if self._repertory_stop_button_tooltip is not None:
            self._repertory_stop_button_tooltip.text = (
                "Richiede l'interruzione sicura della diagnosi in corso."
                if mode == REPERTORY_MODE_DIAGNOSTICS
                else "Richiede l'interruzione sicura dell'elaborazione in corso."
            )
        if self._repertory_open_results_button_tooltip is not None:
            if mode == REPERTORY_MODE_DIAGNOSTICS:
                self._repertory_open_results_button_tooltip.text = "Apre la cartella della sessione diagnostica completata."
            elif mode == REPERTORY_MODE_INSERT_TRACKS:
                self._repertory_open_results_button_tooltip.text = (
                    "Apre la cartella della sessione di inserimento nuovi brani completata, quando disponibile."
                )
            else:
                self._repertory_open_results_button_tooltip.text = "Apre la cartella della sessione contenente report, log e backup."
        if self._repertory_updates_entry_tooltip is not None:
            self._repertory_updates_entry_tooltip.text = (
                "Seleziona il repertorio organizzato da confrontare."
                if mode == REPERTORY_MODE_DIAGNOSTICS
                else "Seleziona la cartella contenente i file MP3 da confrontare e aggiornare."
            )
        if self._repertory_library_entry_tooltip is not None:
            self._repertory_library_entry_tooltip.text = (
                "Seleziona il repertorio generale piatto da confrontare."
                if mode == REPERTORY_MODE_DIAGNOSTICS
                else "Seleziona la root del repertorio organizzato in cartelle e sottocartelle."
            )

    def _clear_repertory_diagnostics_tree_widgets(self) -> None:
        for widget in list(self._repertory_diagnostics_tree_widgets):
            try:
                widget.destroy()
            except Exception:
                pass
        self._repertory_diagnostics_tree_widgets = []
        self._repertory_diagnostics_tree_items = {}
        self._repertory_diagnostics_tree_order = []

    def _refresh_repertory_diagnostics_folder_tree(self) -> None:
        if self._repertory_diagnostics_tree_scrollable is None:
            return

        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode != REPERTORY_MODE_DIAGNOSTICS:
            return

        split_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        general_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""

        if split_dir and general_dir and self._repertory_paths_collide(split_dir, general_dir):
            self._clear_repertory_diagnostics_tree_widgets()
            placeholder = ctk.CTkLabel(
                self._repertory_diagnostics_tree_scrollable,
                text="Percorsi non validi: Repertorio suddiviso e Cartella Repertorio Generale coincidono.",
                anchor="w",
                justify="left",
                wraplength=700,
            )
            placeholder.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
            self._repertory_diagnostics_tree_widgets.append(placeholder)
            self._update_repertory_primary_action_state()
            return

        previous_states: dict[str, bool] = {}
        for key, item in self._repertory_diagnostics_tree_items.items():
            try:
                previous_states[key] = bool(item["var"].get())
            except Exception:
                previous_states[key] = True

        self._clear_repertory_diagnostics_tree_widgets()
        parent = self._repertory_diagnostics_tree_scrollable
        if not split_dir or not Path(split_dir).is_dir() or not general_dir or not Path(general_dir).is_dir():
            placeholder = ctk.CTkLabel(
                parent,
                text="Seleziona Cartella Repertorio suddiviso e Cartella Repertorio Generale per mostrare le sottocartelle.",
                anchor="w",
                justify="left",
                wraplength=700,
            )
            placeholder.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
            self._repertory_diagnostics_tree_widgets.append(placeholder)
            self._update_repertory_primary_action_state()
            return

        try:
            nodes = enumerate_split_repertory_nodes(
                split_dir,
                general_dir,
                general_dir,
            )
        except Exception as error:
            placeholder = ctk.CTkLabel(
                parent,
                text=f"Impossibile leggere le sottocartelle: {error}",
                anchor="w",
                justify="left",
                wraplength=700,
            )
            placeholder.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
            self._repertory_diagnostics_tree_widgets.append(placeholder)
            self._update_repertory_primary_action_state()
            return

        if not nodes:
            placeholder = ctk.CTkLabel(
                parent,
                text="Nessuna sottocartella trovata.",
                anchor="w",
            )
            placeholder.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
            self._repertory_diagnostics_tree_widgets.append(placeholder)
            self._update_repertory_primary_action_state()
            return

        for row_index, node in enumerate(nodes):
            relative_path = str(getattr(node, "relative_path", "") or "")
            full_path = str(getattr(node, "full_path", "") or "")
            level = int(getattr(node, "level", 0) or 0)
            auto_excluded = bool(getattr(node, "auto_excluded", False))
            reason = str(getattr(node, "auto_exclusion_reason", "") or "")
            selectable = bool(getattr(node, "selectable", True))
            is_root_files = bool(getattr(node, "is_virtual_root_files", False))
            mp3_detected = int(getattr(node, "mp3_detected", 0) or 0)

            if is_root_files:
                label = f"File presenti direttamente nella cartella principale ({mp3_detected} MP3)"
            else:
                label = relative_path or "."
            text = label if not reason else f"{label} ({reason})"
            default_value = previous_states.get(relative_path)
            if default_value is None:
                default_value = False if auto_excluded else True
            var = tk.BooleanVar(value=bool(default_value))

            checkbox = ctk.CTkCheckBox(
                parent,
                text=text,
                variable=var,
                command=lambda key=relative_path: self._on_repertory_diagnostics_tree_node_toggled(key),
            )
            checkbox.grid(row=row_index, column=0, sticky="w", padx=(10 + max(0, level) * 18, 8), pady=2)
            if auto_excluded or not selectable:
                checkbox.configure(state="disabled")

            parent_path = ""
            if not is_root_files and relative_path:
                parent_path = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""

            self._repertory_diagnostics_tree_widgets.append(checkbox)
            self._repertory_diagnostics_tree_order.append(relative_path)
            self._repertory_diagnostics_tree_items[relative_path] = {
                "var": var,
                "widget": checkbox,
                "selectable": bool(selectable and not auto_excluded),
                "auto_excluded": auto_excluded,
                "full_path": full_path,
                "level": level,
                "parent": parent_path,
                "is_root_files": is_root_files,
                "mp3_detected": mp3_detected,
            }
            self._update_repertory_primary_action_state()

    def _on_repertory_diagnostics_tree_node_toggled(self, relative_path: str) -> None:
        item = self._repertory_diagnostics_tree_items.get(relative_path)
        if not item or not bool(item.get("selectable", False)):
            return
        target_state = bool(item["var"].get())

        if target_state:
            parent_key = str(item.get("parent", "") or "")
            while parent_key:
                parent_item = self._repertory_diagnostics_tree_items.get(parent_key)
                if parent_item and bool(parent_item.get("selectable", False)):
                    parent_item["var"].set(True)
                if "/" not in parent_key:
                    break
                parent_key = parent_key.rsplit("/", 1)[0]

        for key, child in self._repertory_diagnostics_tree_items.items():
            if key == relative_path:
                continue
            if not bool(child.get("selectable", False)):
                continue
            if not key:
                continue
            if key == relative_path or key.startswith(relative_path + "/"):
                child["var"].set(target_state)
        self._update_repertory_primary_action_state()

    def _select_all_repertory_diagnostics_nodes(self) -> None:
        for item in self._repertory_diagnostics_tree_items.values():
            if bool(item.get("selectable", False)):
                item["var"].set(True)
        self._update_repertory_primary_action_state()

    def _deselect_all_repertory_diagnostics_nodes(self) -> None:
        for key, item in self._repertory_diagnostics_tree_items.items():
            if not bool(item.get("selectable", False)):
                continue
            if bool(item.get("is_root_files", False)):
                item["var"].set(False)
                continue
            item["var"].set(False)
        self._update_repertory_primary_action_state()

    def _build_repertory_diagnostics_selection_payload(self) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        selected: list[str] = []
        excluded: list[str] = []
        include_root_files = False

        for key in self._repertory_diagnostics_tree_order:
            item = self._repertory_diagnostics_tree_items.get(key)
            if not item:
                continue
            if bool(item.get("auto_excluded", False)):
                continue
            if not bool(item.get("selectable", False)):
                continue

            is_selected = bool(item["var"].get())
            is_root_files = bool(item.get("is_root_files", False)) or key == ROOT_FILES_TOKEN
            if is_root_files:
                include_root_files = is_selected
                continue

            if is_selected:
                selected.append(key)
            else:
                excluded.append(key)

        return tuple(selected), tuple(excluded), include_root_files

    @staticmethod
    def _canonical_path_for_compare(raw_path: str) -> str:
        normalized = str(raw_path or "").strip()
        if not normalized:
            return ""
        try:
            expanded = Path(normalized).expanduser().resolve(strict=False)
            normalized = str(expanded)
        except Exception:
            normalized = os.path.abspath(os.path.expanduser(normalized))
        return os.path.normcase(os.path.normpath(normalized))

    def _repertory_paths_collide(self, split_dir: str, general_dir: str) -> bool:
        left = self._canonical_path_for_compare(split_dir)
        right = self._canonical_path_for_compare(general_dir)
        if not left or not right:
            return False
        return left == right

    def _show_invalid_repertory_paths_message(self) -> None:
        messagebox.showerror(
            "Organizza repertorio",
            "PERCORSI NON VALIDI\n\n"
            "La Cartella Repertorio Generale e il Repertorio Suddiviso\n"
            "non possono coincidere.\n\n"
            "Selezionare due cartelle differenti.",
            parent=self._repertory_dialog,
        )

    def _validate_repertory_diagnostics_paths(self, *, show_message: bool) -> bool:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode != REPERTORY_MODE_DIAGNOSTICS:
            return True
        split_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        general_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""
        if not split_dir or not general_dir:
            return True
        if not self._repertory_paths_collide(split_dir, general_dir):
            return True
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Percorsi non validi: selezionare due cartelle differenti")
        if show_message:
            self._show_invalid_repertory_paths_message()
        return False

    def _on_repertory_diagnostics_paths_changed(self, _event=None) -> None:
        if self._repertory_smartphone_entry is not None:
            raw = str(self._repertory_smartphone_entry.get() or "").strip()
            selected_path: str | None = None
            if raw:
                try:
                    selected_path = str(Path(raw).expanduser().resolve())
                except Exception:
                    selected_path = None
            self._repertory_selected_smartphone_folder = selected_path
            if not selected_path:
                self._repertory_last_completed_smartphone_folder = None
            elif self._repertory_last_completed_smartphone_folder and (
                self._canonical_path_for_compare(selected_path)
                != self._canonical_path_for_compare(self._repertory_last_completed_smartphone_folder)
            ):
                self._repertory_last_completed_smartphone_folder = None
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode == REPERTORY_MODE_DIAGNOSTICS:
            self._validate_repertory_diagnostics_paths(show_message=True)
            self._refresh_repertory_diagnostics_folder_tree()
        self._update_repertory_android_buttons_state()
        self._update_repertory_primary_action_state()

    @staticmethod
    def _folder_contains_direct_or_nested_mp3(folder_path: Path) -> bool:
        for current_root, dirs, files in os.walk(folder_path, topdown=True, followlinks=False):
            current = Path(current_root)
            pruned: list[str] = []
            for directory in dirs:
                candidate = current / directory
                try:
                    if candidate.is_symlink():
                        continue
                except Exception:
                    continue
                pruned.append(directory)
            dirs[:] = pruned
            for file_name in files:
                candidate = current / file_name
                if candidate.suffix.lower() == ".mp3":
                    return True
        return False

    def _count_selected_repertory_folders_with_mp3(self, split_dir: str, selected_relative_roots: tuple[str, ...]) -> int:
        split_root = Path(split_dir)
        unique_roots: list[str] = []
        for rel in sorted({str(item or "").strip().replace("\\", "/").strip("/") for item in selected_relative_roots if str(item or "").strip()}):
            is_nested = False
            for kept in unique_roots:
                if rel == kept or rel.startswith(kept + "/"):
                    is_nested = True
                    break
            if not is_nested:
                unique_roots.append(rel)

        elaborable = 0
        for rel in unique_roots:
            folder = split_root / Path(rel)
            if not folder.is_dir():
                continue
            if self._folder_contains_direct_or_nested_mp3(folder):
                elaborable += 1
        return elaborable

    def _count_root_mp3_direct(self, split_dir: str) -> int:
        split_root = Path(split_dir)
        if not split_root.is_dir():
            return 0
        count = 0
        try:
            for item in split_root.iterdir():
                if item.is_file() and item.suffix.lower() == ".mp3":
                    count += 1
        except OSError:
            return 0
        return count

    def _finalize_repertory_window_close(self) -> None:
        self._stop_repertory_timer()
        self._close_repertory_decision_dialog()
        self._reset_rep003_operational_session(
            preserve_results=False,
            preserve_android_destination=False,
            clear_paths=True,
        )
        self._clear_repertory_diagnostics_tree_widgets()
        if self._repertory_dialog is not None:
            self._cleanup_tooltips(owner=self._repertory_dialog)
            try:
                self._repertory_dialog.destroy()
            except Exception:
                pass

        self._repertory_dialog = None
        self._repertory_general_label = None
        self._repertory_updates_entry = None
        self._repertory_library_entry = None
        self._repertory_general_entry = None
        self._repertory_results_entry = None
        self._repertory_smartphone_label = None
        self._repertory_smartphone_entry = None
        self._repertory_smartphone_browse_button = None
        self._repertory_status_label = None
        self._repertory_log_box = None
        self._repertory_progress_bar = None
        self._repertory_start_button = None
        self._repertory_stop_button = None
        self._repertory_close_button = None
        self._repertory_open_results_button = None
        self._repertory_open_smartphone_button = None
        self._repertory_reset_smartphone_button = None
        self._repertory_general_browse_button = None
        self._repertory_diagnostics_tree_container = None
        self._repertory_diagnostics_tree_scrollable = None
        self._repertory_diagnostics_refresh_button = None
        self._repertory_diagnostics_select_all_button = None
        self._repertory_diagnostics_deselect_all_button = None
        self._repertory_diagnostics_tree_items = {}
        self._repertory_diagnostics_tree_order = []
        self._repertory_diagnostics_tree_widgets = []
        self._repertory_path_widgets = []
        self._repertory_mode_selector = None
        self._repertory_mode_radios = []
        self._repertory_mode_frame = None
        self._repertory_session_folder = None
        self._repertory_result_folder_update = None
        self._repertory_result_folder_diagnostics = None
        self._repertory_result_folder_insert_tracks = None
        self._repertory_selected_smartphone_folder = None
        self._repertory_last_completed_smartphone_folder = None
        self._repertory_allow_session_log_updates = False
        self._repertory_expected_output_root = ""
        self._repertory_min_session_timestamp = ""
        self._repertory_started_at = None
        self._repertory_total_files = 0
        self._repertory_processed_files = 0
        self._repertory_mtime_bypass_active = False
        self._repertory_mtime_session_choice = "ASK"
        self._repertory_reset_in_progress = False
        self._repertory_pending_decision_request_id = None
        self._repertory_close_requested = False
        self._rep003_panel_frame = None
        self._rep003_path_widgets = []
        self._rep003_tracks_tree = None
        self._rep003_folders_tree = None
        self._rep003_new_tracks_entry = None
        self._rep003_split_entry = None
        self._rep003_general_entry = None
        self._rep003_smartphone_entry = None
        self._rep003_status_label = None
        self._rep003_load_button = None
        self._rep003_create_folder_button = None
        self._rep003_refresh_folders_button = None
        self._rep003_assign_button = None
        self._rep003_remove_button = None
        self._rep003_show_managed_switch = None
        self._rep003_browse_buttons = []
        self._rep003_track_row_by_iid = {}
        self._rep003_folder_iid_by_relative = {}
        self._rep003_folder_sort_key = REP003_SORT_FOLDER_RELATIVE
        self._rep003_folder_sort_reverse = False
        self._rep003_pending_decision_request_id = None
        self._rep003_create_folder_dialog = None
        self._rep003_create_folder_entry = None
        self._rep003_create_folder_preview_var.set("")
        self._rep003_session_policy = "ASK"
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()

    def _repertory_worker_decision_required(self, payload: dict[str, Any]) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_worker_decision_required, payload)

    def _handle_repertory_worker_decision_required(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return

        if self._repertory_mtime_session_choice == "UPDATE_ALL":
            accepted = self.repertory_worker.submit_decision(request_id, "UPDATE_AND_BYPASS_SESSION")
            if accepted:
                self._append_repertory_log(
                    "[MTIME] Decisione globale sessione riutilizzata automaticamente: UPDATE_ALL"
                )
            return
        if self._repertory_mtime_session_choice == "SKIP_ALL":
            accepted = self.repertory_worker.submit_decision(request_id, "SKIP_AND_BYPASS_SESSION")
            if accepted:
                self._append_repertory_log(
                    "[MTIME] Decisione globale sessione riutilizzata automaticamente: SKIP_ALL"
                )
            return

        self._repertory_pending_decision_request_id = request_id
        self._open_repertory_mtime_decision_dialog(payload)

    def _close_repertory_decision_dialog(self) -> None:
        self._cleanup_tooltips(tooltips=self._repertory_decision_tooltips)
        self._repertory_decision_tooltips = []
        if self._repertory_decision_dialog is not None:
            try:
                self._repertory_decision_dialog.destroy()
            except Exception:
                pass
        self._repertory_decision_dialog = None

    def _add_repertory_decision_tooltip(self, widget, text: str) -> None:
        tip = self._add_tooltip(widget, text)
        self._repertory_decision_tooltips.append(tip)

    def _submit_repertory_decision(self, decision: str) -> None:
        request_id = (self._repertory_pending_decision_request_id or "").strip()
        if not request_id:
            self._close_repertory_decision_dialog()
            return
        accepted = self.repertory_worker.submit_decision(request_id, decision)
        if accepted:
            self._append_repertory_log(f"[MTIME] Decisione utente inviata: {decision}")
        self._repertory_pending_decision_request_id = None
        if decision == "UPDATE_AND_BYPASS_SESSION":
            self._repertory_mtime_bypass_active = True
            self._repertory_mtime_session_choice = "UPDATE_ALL"
        elif decision == "SKIP_AND_BYPASS_SESSION":
            self._repertory_mtime_bypass_active = True
            self._repertory_mtime_session_choice = "SKIP_ALL"
        self._close_repertory_decision_dialog()

    def _open_repertory_mtime_decision_dialog(self, payload: dict[str, Any]) -> None:
        self._close_repertory_decision_dialog()
        parent = self._repertory_dialog if self._repertory_dialog is not None else self

        screen_width = int(parent.winfo_screenwidth())
        screen_height = int(parent.winfo_screenheight())
        horizontal_margin_per_side = 48
        vertical_margin_total = 80
        desired_width = 1040
        min_width = 640
        min_height = 500
        max_width_pixels = max(520, screen_width - (horizontal_margin_per_side * 2))
        usable_width = max_width_pixels
        initial_width = min(desired_width, usable_width)
        content_wrap = max(260, initial_width - 140)
        value_wrap = max(180, int((initial_width - 220) / 2))
        wrapped_value_labels: list[Any] = []

        dialog = ManagedCTkToplevel(parent)
        self._repertory_decision_dialog = dialog
        dialog.title("Confronto data e ora file")
        dialog.resizable(False, False)
        dialog.transient(parent)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        dialog.geometry(f"{initial_width}x560")
        dialog.minsize(min(min_width, usable_width), min_height)

        def _on_close() -> None:
            self._submit_repertory_decision("SKIP_CURRENT")

        dialog.protocol("WM_DELETE_WINDOW", _on_close)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=0)
        dialog.grid_rowconfigure(1, weight=0)

        body = ctk.CTkFrame(dialog)
        body.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(2, weight=0)
        body.grid_rowconfigure(3, weight=0)

        warning_text = (
            "Confronta i due blocchi file e scegli come procedere per questo singolo caso. "
            "Nel caso di stessa data e ora non viene applicato alcun aggiornamento automatico."
        )
        warning_label = ctk.CTkLabel(body, text=warning_text, justify="left", anchor="w", wraplength=content_wrap)
        warning_label.grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 6)
        )

        def _file_info_block(container, title: str, data: dict[str, Any]) -> None:
            frame = ctk.CTkFrame(container)
            frame.grid_columnconfigure(0, weight=0)
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text=title, anchor="w", font=ctk.CTkFont(weight="bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 8)
            )

            entries = [
                ("Nome file:", str(data.get("name", ""))),
                ("Percorso:", str(data.get("path", ""))),
                ("Dimensione:", self._format_repertory_size_text(data.get("size", 0))),
                ("Data e ora di modifica:", str(data.get("mtime_human", ""))),
            ]
            for row_index, (label_text, value_text) in enumerate(entries, start=1):
                ctk.CTkLabel(frame, text=label_text, anchor="nw", font=ctk.CTkFont(weight="bold")).grid(
                    row=row_index,
                    column=0,
                    sticky="nw",
                    padx=(10, 8),
                    pady=(0, 4),
                )
                value_label = ctk.CTkLabel(
                    frame,
                    text=value_text,
                    anchor="nw",
                    justify="left",
                    wraplength=value_wrap,
                    width=value_wrap,
                )
                value_label.grid(
                    row=row_index,
                    column=1,
                    sticky="ew",
                    padx=(0, 10),
                    pady=(0, 4),
                )
                wrapped_value_labels.append(value_label)
            return frame

        source_data = {
            "name": payload.get("source_name", ""),
            "path": payload.get("source_path", ""),
            "mtime_human": payload.get("source_mtime_human", ""),
            "size": payload.get("source_size", 0),
        }
        dest_data = {
            "name": payload.get("destination_name", ""),
            "path": payload.get("destination_path", ""),
            "mtime_human": payload.get("destination_mtime_human", ""),
            "size": payload.get("destination_size", 0),
        }

        source_frame = _file_info_block(body, "BLOCCO FILE AGGIORNAMENTI", source_data)
        source_frame.grid(row=1, column=0, sticky="new", padx=(10, 6), pady=(0, 6))
        dest_frame = _file_info_block(body, "BLOCCO FILE REPERTORIO", dest_data)
        dest_frame.grid(row=1, column=1, sticky="new", padx=(6, 10), pady=(0, 6))

        summary_text = str(payload.get("comparison_summary") or "").strip()
        if not summary_text:
            summary_text = self._build_repertory_mtime_summary(payload)
        delta_compact = str(payload.get("mtime_delta_compact") or payload.get("mtime_delta_human") or "").strip()
        if not delta_compact:
            delta_compact = "Stessa data e ora"
        comparison_reason = str(payload.get("comparison_reason") or "").strip()
        if not comparison_reason:
            comparison_reason = "Motivo confronto non disponibile"

        summary_frame = ctk.CTkFrame(body)
        summary_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
        summary_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(summary_frame, text="SINTESI DEL CONFRONTO", anchor="w", font=ctk.CTkFont(weight="bold")).grid(
            row=0,
            column=0,
            sticky="w",
            padx=10,
            pady=(8, 4),
        )
        delta_label = ctk.CTkLabel(
            summary_frame,
            text=summary_text,
            anchor="w",
            justify="left",
            font=ctk.CTkFont(weight="bold"),
            wraplength=content_wrap,
        )
        delta_label.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        delta_compact_label = ctk.CTkLabel(
            summary_frame,
            text=f"Differenza temporale: {delta_compact}",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(weight="bold"),
            wraplength=content_wrap,
        )
        delta_compact_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 3))
        reason_label = ctk.CTkLabel(
            summary_frame,
            text=f"Motivo confronto: {comparison_reason}",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(weight="bold"),
            wraplength=content_wrap,
        )
        reason_label.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._add_repertory_decision_tooltip(
            delta_label,
            "Riassume quale dei due file e piu recente e la relativa differenza temporale.",
        )

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        button_row.grid_columnconfigure((0, 1), weight=1)
        button_row.grid_columnconfigure((2, 3), weight=1)

        button_update_current = ctk.CTkButton(
            button_row,
            text="Aggiorna comunque",
            command=lambda: self._submit_repertory_decision("UPDATE_CURRENT"),
            height=40,
        )
        button_update_current.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        self._add_repertory_decision_tooltip(
            button_update_current,
            "Sostituisce solo il file visualizzato.\n\nIl controllo data e ora restera attivo per i file successivi.",
        )

        button_skip_current = ctk.CTkButton(
            button_row,
            text="Non aggiornare",
            command=lambda: self._submit_repertory_decision("SKIP_CURRENT"),
            height=40,
        )
        button_skip_current.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 6))
        self._add_repertory_decision_tooltip(
            button_skip_current,
            "Mantiene il file attualmente presente nel Repertorio.\n\nIl controllo data e ora restera attivo per i file successivi.",
        )

        button_update_all = ctk.CTkButton(
            button_row,
            text="Aggiorna questo e tutti i successivi",
            command=lambda: self._submit_repertory_decision("UPDATE_AND_BYPASS_SESSION"),
            height=40,
        )
        button_update_all.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._add_repertory_decision_tooltip(
            button_update_all,
            "Sostituisce il file visualizzato e aggiorna automaticamente tutti i successivi casi soggetti al controllo data e ora.\n\nLa scelta vale solo per questa sessione.",
        )

        button_skip_all = ctk.CTkButton(
            button_row,
            text="Mantieni questo e tutti i successivi",
            command=lambda: self._submit_repertory_decision("SKIP_AND_BYPASS_SESSION"),
            height=40,
        )
        button_skip_all.grid(row=1, column=1, sticky="ew", padx=(6, 0))
        self._add_repertory_decision_tooltip(
            button_skip_all,
            "Mantiene il file visualizzato e conserva automaticamente tutti i successivi casi soggetti al controllo data e ora.\n\nLa scelta vale solo per questa sessione.",
        )

        dialog.update_idletasks()
        req_h = max(min_height, int(dialog.winfo_reqheight()))
        max_h = max(min_height, screen_height - vertical_margin_total)
        final_w = min(desired_width, usable_width)
        final_h = min(req_h, max_h)

        for _ in range(3):
            dynamic_content_wrap = max(260, final_w - 140)
            dynamic_value_wrap = max(180, int((final_w - 220) / 2))
            warning_label.configure(wraplength=dynamic_content_wrap)
            delta_label.configure(wraplength=dynamic_content_wrap)
            delta_compact_label.configure(wraplength=dynamic_content_wrap)
            reason_label.configure(wraplength=dynamic_content_wrap)
            for value_label in wrapped_value_labels:
                value_label.configure(wraplength=dynamic_value_wrap, width=dynamic_value_wrap)

            x_pos = max(0, (screen_width - final_w) // 2)
            y_pos = max(0, (screen_height - final_h) // 2)
            dialog.geometry(f"{final_w}x{final_h}+{x_pos}+{y_pos}")
            dialog.update_idletasks()

            realized_width = int(dialog.winfo_width())
            if realized_width <= max_width_pixels:
                break

            shrink_ratio = max_width_pixels / float(max(1, realized_width))
            final_w = max(520, int(final_w * shrink_ratio))

    def _set_repertory_ui_running_state(self, running: bool) -> None:
        widget_state = "disabled" if running else "normal"
        for widget in self._repertory_path_widgets:
            try:
                widget.configure(state=widget_state)
            except Exception:
                pass
        for widget in self._rep003_path_widgets:
            try:
                widget.configure(state=widget_state)
            except Exception:
                pass
        if self._repertory_start_button is not None:
            if running:
                self._repertory_start_button.configure(state="disabled")
            else:
                self._update_repertory_primary_action_state()
        if self._repertory_stop_button is not None:
            self._repertory_stop_button.configure(state="normal" if running else "disabled")
        for radio in self._repertory_mode_radios:
            try:
                radio.configure(state="disabled" if running else "normal")
            except Exception:
                pass
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()

    def _reset_repertory_runtime_counters(self) -> None:
        self._repertory_total_files = 0
        self._repertory_processed_files = 0
        self._repertory_matches_found = 0
        self._repertory_files_updated = 0
        self._repertory_files_not_found = 0
        self._repertory_errors = 0
        self._repertory_mtime_bypass_active = False
        self._repertory_mtime_session_choice = "ASK"
        self._render_repertory_runtime_counters()

    @staticmethod
    def _format_human_delta_seconds(total_seconds: int) -> str:
        seconds = max(0, int(total_seconds))
        if seconds <= 0:
            return "Stessa data e ora"

        units = [
            (365 * 24 * 3600, "anno", "anni"),
            (30 * 24 * 3600, "mese", "mesi"),
            (24 * 3600, "giorno", "giorni"),
            (3600, "ora", "ore"),
            (60, "minuto", "minuti"),
            (1, "secondo", "secondi"),
        ]
        parts: list[str] = []
        remaining = seconds
        for unit_seconds, singular, plural in units:
            if remaining < unit_seconds:
                continue
            qty, remaining = divmod(remaining, unit_seconds)
            parts.append(f"{qty} {singular if qty == 1 else plural}")
        if not parts:
            return "Stessa data e ora"
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} e {parts[1]}"
        return ", ".join(parts[:-1]) + f" e {parts[-1]}"

    @staticmethod
    def _format_repertory_size_text(size_value: Any) -> str:
        try:
            size_bytes = max(0, int(size_value))
        except Exception:
            size_bytes = 0
        size_mb = float(size_bytes) / (1024.0 * 1024.0)
        return f"{size_mb:.2f}".replace(".", ",") + " MB"

    def _build_repertory_mtime_summary(self, payload: dict[str, Any]) -> str:
        reason = str(payload.get("comparison_reason") or "").strip()
        delta_human = str(payload.get("mtime_delta_compact") or payload.get("mtime_delta_human") or "").strip()
        if not delta_human:
            try:
                src_mtime = float(payload.get("source_mtime", 0.0))
                dst_mtime = float(payload.get("destination_mtime", 0.0))
                delta_human = self._format_human_delta_seconds(int(abs(dst_mtime - src_mtime)))
            except Exception:
                delta_human = "Stessa data e ora"

        if reason == "Stessa data e ora di modifica":
            return (
                "Il file nella cartella Aggiornamenti e il file presente nel Repertorio "
                "hanno la stessa data e ora di modifica."
            )
        if reason in {"File della cartella Aggiornamenti più recente", "File della cartella Aggiornamenti piu recente"}:
            return (
                "Il file nella cartella Aggiornamenti e piu recente di quello "
                f"presente nel Repertorio di {delta_human}."
            )
        if reason in {"File del repertorio più recente", "File del repertorio piu recente"}:
            return (
                "Il file nella cartella Aggiornamenti e piu vecchio di quello "
                f"presente nel Repertorio di {delta_human}."
            )
        return f"Differenza temporale: {delta_human}."

    def _render_repertory_runtime_counters(self) -> None:
        if self._widget_exists(self._repertory_file_counter_label):
            try:
                self._repertory_file_counter_label.configure(
                    text=f"File elaborati: {self._repertory_processed_files} / {self._repertory_total_files}"
                )
            except (tk.TclError, RuntimeError):
                pass
        if self._widget_exists(self._repertory_matches_label):
            try:
                self._repertory_matches_label.configure(text=f"Corrispondenze trovate: {self._repertory_matches_found}")
            except (tk.TclError, RuntimeError):
                pass
        if self._widget_exists(self._repertory_updated_label):
            try:
                self._repertory_updated_label.configure(text=f"File aggiornati: {self._repertory_files_updated}")
            except (tk.TclError, RuntimeError):
                pass
        if self._widget_exists(self._repertory_not_found_label):
            try:
                self._repertory_not_found_label.configure(text=f"File non trovati: {self._repertory_files_not_found}")
            except (tk.TclError, RuntimeError):
                pass
        if self._widget_exists(self._repertory_errors_label):
            try:
                self._repertory_errors_label.configure(text=f"Errori: {self._repertory_errors}")
            except (tk.TclError, RuntimeError):
                pass

    def _start_repertory_timer(self) -> None:
        if self._repertory_timer_job is not None:
            self._cancel_tracked_after_job(self._repertory_timer_job)
        self._tick_repertory_timer()

    def _stop_repertory_timer(self) -> None:
        if self._repertory_timer_job is not None:
            self._cancel_tracked_after_job(self._repertory_timer_job)
            self._repertory_timer_job = None

    def _tick_repertory_timer(self) -> None:
        if (
            self._repertory_started_at is None
            or self._repertory_close_requested
            or not self._widget_exists(self)
            or not self._widget_exists(self._repertory_dialog)
        ):
            self._repertory_timer_job = None
            return
        elapsed = max(0.0, time.monotonic() - self._repertory_started_at)
        if self._repertory_elapsed_label is not None:
            self._repertory_elapsed_label.configure(text=f"Tempo trascorso: {self._format_duration(elapsed)}")

        if self._repertory_total_files > 0 and self._repertory_processed_files > 0:
            average = elapsed / float(self._repertory_processed_files)
            remaining = max(0, self._repertory_total_files - self._repertory_processed_files)
            eta = self._format_duration(max(0.0, average * remaining))
        else:
            eta = "--"
        if self._repertory_eta_label is not None:
            self._repertory_eta_label.configure(text=f"Tempo restante stimato: {eta}")

        self._repertory_timer_job = self._schedule_tracked_after(1000, self._tick_repertory_timer)

    def _parse_repertory_progress(self, message: str) -> None:
        try:
            prefix, _, tail = message.partition(" - ")
            progress = prefix.replace("Elaborazione", "").strip()
            current_text, _, total_text = progress.partition("/")
            current = int(current_text.strip())
            total = int(total_text.strip())
            self._repertory_processed_files = max(0, current)
            self._repertory_total_files = max(0, total)
        except Exception:
            return

        if "|" not in message:
            return
        metrics = message.split("|", 1)[1].strip().split()
        for token in metrics:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            try:
                parsed = int(value)
            except ValueError:
                continue
            if key == "match":
                self._repertory_matches_found = max(0, parsed)
            elif key == "aggiornati":
                self._repertory_files_updated = max(0, parsed)
            elif key == "non_trovati":
                self._repertory_files_not_found = max(0, parsed)
            elif key == "errori":
                self._repertory_errors = max(0, parsed)

        self._render_repertory_runtime_counters()

        if "CONTROLLO_BYPASS_SESSIONE" in message:
            self._repertory_mtime_bypass_active = True

    def _process_repertory_tech_message(self, message: str) -> None:
        if message.startswith("[TECH] Sessione esiti creata | path="):
            if not self._repertory_allow_session_log_updates:
                return
            candidate = message.split("path=", 1)[1].strip()
            if not candidate:
                return
            try:
                candidate_path = Path(candidate).resolve()
            except Exception:
                return

            if self._repertory_expected_output_root:
                expected_root = Path(self._repertory_expected_output_root).resolve()
                expected_prefix = str(expected_root).casefold()
                if not str(candidate_path).casefold().startswith(expected_prefix):
                    return

            if self._repertory_min_session_timestamp:
                name = candidate_path.name
                prefix = "Organizzazione_Repertorio_"
                if name.startswith(prefix):
                    stamp = name[len(prefix):]
                    if stamp < self._repertory_min_session_timestamp:
                        return

            self._repertory_session_folder = str(candidate_path)
            current_mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
            if current_mode in (REPERTORY_MODE_UPDATE, REPERTORY_MODE_DIAGNOSTICS):
                self._set_repertory_results_folder_for_mode(current_mode, self._repertory_session_folder)
            self._update_repertory_open_results_button_state()

    def _append_repertory_log(self, message: str) -> None:
        if self._repertory_log_box is not None:
            self._repertory_log_box.configure(state="normal")
            self._repertory_log_box.insert("end", f"{message}\n")
            self._repertory_log_box.see("end")
            self._repertory_log_box.configure(state="disabled")
        self._process_repertory_tech_message(message)
        self._append_log(message)

    def _start_repertory_organization(self) -> None:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode == REPERTORY_MODE_INSERT_TRACKS:
            self._rep003_finalize_placeholder()
            return
        if mode == REPERTORY_MODE_DIAGNOSTICS:
            self._start_repertory_diagnostics()
            return

        if self.repertory_worker.is_running:
            messagebox.showwarning("Organizza repertorio", "Una organizzazione repertorio e gia in corso.", parent=self._repertory_dialog)
            return

        self._repertory_session_folder = None
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_UPDATE, "")
        self._repertory_last_completed_smartphone_folder = None
        self._repertory_allow_session_log_updates = False
        self._repertory_expected_output_root = ""
        self._repertory_min_session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()

        updates_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        repertory_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""
        repertory_general_dir = self._repertory_general_entry.get().strip() if self._repertory_general_entry is not None else ""
        results_dir = self._repertory_results_entry.get().strip() if self._repertory_results_entry is not None else ""
        smartphone_dir = self._repertory_smartphone_entry.get().strip() if self._repertory_smartphone_entry is not None else ""
        backup_enabled = bool(self._repertory_backup_var.get())

        if not updates_dir or not Path(updates_dir).is_dir():
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella aggiornamenti valida.", parent=self._repertory_dialog)
            return
        if not repertory_dir or not Path(repertory_dir).is_dir():
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella repertorio suddiviso valida.", parent=self._repertory_dialog)
            return
        if not repertory_general_dir or not Path(repertory_general_dir).is_dir():
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella repertorio generale valida.", parent=self._repertory_dialog)
            return
        if not smartphone_dir:
            messagebox.showerror(
                "Organizza repertorio",
                "Seleziona una cartella Smartphone/Tablet valida.",
                parent=self._repertory_dialog,
            )
            return
        try:
            smartphone_path = Path(smartphone_dir).expanduser().resolve()
        except Exception as error:
            messagebox.showerror(
                "Organizza repertorio",
                f"Cartella Smartphone/Tablet non valida:\n{error}",
                parent=self._repertory_dialog,
            )
            return
        if self._is_filesystem_root(smartphone_path):
            messagebox.showerror(
                "Organizza repertorio",
                "La cartella Smartphone/Tablet non puo coincidere con la root del disco.",
                parent=self._repertory_dialog,
            )
            return
        self._repertory_smartphone_root = str(smartphone_path)
        self._repertory_selected_smartphone_folder = str(smartphone_path)
        if not self._ensure_repertory_smartphone_folder_ready():
            self._append_repertory_log("Avvio annullato: cartella Smartphone/Tablet non pronta.")
            return
        if not results_dir:
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella risultati valida.", parent=self._repertory_dialog)
            return

        try:
            Path(results_dir).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Organizza repertorio", f"Impossibile creare la cartella risultati:\n{error}", parent=self._repertory_dialog)
            return

        self._repertory_expected_output_root = str(Path(results_dir).resolve())
        self._repertory_allow_session_log_updates = True
        self._reset_repertory_runtime_counters()
        self._repertory_started_at = time.monotonic()
        self._start_repertory_timer()
        self._set_repertory_ui_running_state(True)
        self._update_controls_state()

        if self._repertory_progress_bar is not None:
            self._repertory_progress_bar.set(0)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Organizzazione repertorio in corso...")

        self.settings["repertory_updates_folder"] = updates_dir
        self.settings["repertory_library_folder"] = repertory_dir
        self.settings["repertory_general_folder"] = repertory_general_dir
        self.settings["repertory_results_folder"] = results_dir
        self.settings["repertory_smartphone_folder"] = str(self._get_repertory_smartphone_root())
        self.settings["repertory_backup_enabled"] = backup_enabled
        self.save_settings()

        self._append_repertory_log("Avvio organizzazione repertorio.")

        try:
            self.repertory_worker.start(
                updates_dir=updates_dir,
                repertory_dir=repertory_dir,
                repertory_general_dir=repertory_general_dir,
                results_dir=results_dir,
                backup_enabled=backup_enabled,
                smartphone_tablet_dir=str(self._get_repertory_smartphone_root()),
            )
        except Exception as error:
            self._stop_repertory_timer()
            self._repertory_allow_session_log_updates = False
            self._set_repertory_ui_running_state(False)
            self._update_controls_state()
            messagebox.showerror("Organizza repertorio", str(error), parent=self._repertory_dialog)

    def _start_repertory_diagnostics(self) -> None:
        if self.repertory_diagnostics_worker.is_running:
            messagebox.showwarning("Organizza repertorio", "Una diagnosi repertorio e gia in corso.", parent=self._repertory_dialog)
            return

        split_dir = self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else ""
        general_dir = self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else ""

        if not split_dir or not Path(split_dir).is_dir():
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella repertorio suddiviso valida.", parent=self._repertory_dialog)
            return
        if not general_dir or not Path(general_dir).is_dir():
            messagebox.showerror("Organizza repertorio", "Seleziona una cartella repertorio generale valida.", parent=self._repertory_dialog)
            return
        if not self._validate_repertory_diagnostics_paths(show_message=True):
            return
        if not os.access(general_dir, os.W_OK):
            messagebox.showerror("Organizza repertorio", "La Cartella Repertorio Generale deve essere scrivibile.", parent=self._repertory_dialog)
            return

        self._refresh_repertory_diagnostics_folder_tree()
        selected_relative_roots, excluded_relative_roots, include_root_files = self._build_repertory_diagnostics_selection_payload()
        selected_folder_count = self._count_selected_repertory_folders_with_mp3(split_dir, selected_relative_roots)
        root_mp3_count = self._count_root_mp3_direct(split_dir)
        can_start = selected_folder_count > 0 or (bool(include_root_files) and root_mp3_count > 0)
        if not can_start:
            messagebox.showerror(
                "Organizza repertorio",
                "NESSUNA CARTELLA SELEZIONATA\n\n"
                "Selezionare almeno una cartella del repertorio\n"
                "oppure i file presenti nella cartella principale.",
                parent=self._repertory_dialog,
            )
            return

        self._repertory_session_folder = None
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_DIAGNOSTICS, "")
        self._repertory_allow_session_log_updates = False
        self._repertory_expected_output_root = ""
        self._repertory_min_session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._update_repertory_open_results_button_state()

        self._reset_repertory_runtime_counters()
        self._repertory_started_at = time.monotonic()
        self._start_repertory_timer()
        self._set_repertory_ui_running_state(True)
        self._update_controls_state()

        if self._repertory_progress_bar is not None:
            self._repertory_progress_bar.set(0)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Diagnosi repertorio in corso...")

        self._append_repertory_log("Avvio diagnosi repertorio.")

        try:
            self.repertory_diagnostics_worker.start(
                split_repertory_dir=split_dir,
                general_repertory_dir=general_dir,
                results_dir=general_dir,
                selected_relative_roots=selected_relative_roots,
                excluded_relative_roots=excluded_relative_roots,
                include_root_files=include_root_files,
            )
        except Exception as error:
            self._stop_repertory_timer()
            self._set_repertory_ui_running_state(False)
            self._update_controls_state()
            messagebox.showerror("Organizza repertorio", str(error), parent=self._repertory_dialog)

    def _request_stop_repertory_organization(self) -> None:
        mode = self._normalize_repertory_mode(self._repertory_mode_var.get())
        if mode == REPERTORY_MODE_INSERT_TRACKS:
            if not self.rep003_worker.is_running:
                return
            should_stop = messagebox.askyesno(
                "Organizza repertorio",
                "Vuoi interrompere l'inserimento nuovi brani dopo il file attualmente in elaborazione?",
                parent=self._repertory_dialog,
            )
            if not should_stop:
                return
            self.rep003_worker.cancel()
            self._append_repertory_log("Richiesta di interruzione inserimento nuovi brani inviata.")
            return
        if mode == REPERTORY_MODE_DIAGNOSTICS:
            if not self.repertory_diagnostics_worker.is_running:
                return
            should_stop = messagebox.askyesno(
                "Organizza repertorio",
                "Vuoi interrompere la diagnosi dopo il file attualmente in elaborazione?",
                parent=self._repertory_dialog,
            )
            if not should_stop:
                return
            self.repertory_diagnostics_worker.cancel()
            self._append_repertory_log("Richiesta di interruzione diagnosi inviata.")
            return

        if not self.repertory_worker.is_running:
            return
        should_stop = messagebox.askyesno(
            "Organizza repertorio",
            "Vuoi interrompere l'operazione dopo il file attualmente in elaborazione?",
            parent=self._repertory_dialog,
        )
        if not should_stop:
            return
        self.repertory_worker.cancel()
        self._append_repertory_log("Richiesta di interruzione inviata.")

    def _repertory_worker_progress(self, current: int, total: int, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_worker_progress, current, total, message)

    def _handle_repertory_worker_progress(self, current: int, total: int, message: str) -> None:
        self._repertory_processed_files = max(0, int(current))
        self._repertory_total_files = max(self._repertory_total_files, int(total))
        self._parse_repertory_progress(message)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text=message)
        if self._repertory_progress_bar is not None:
            self._repertory_progress_bar.set(0 if total <= 0 else min(1.0, max(0.0, current / float(total))))
        self._render_repertory_runtime_counters()

    def _repertory_worker_log(self, message: str) -> None:
        self._schedule_tracked_after(0, self._append_repertory_log, message)

    def _repertory_worker_completed(self, result) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_worker_completed, result)

    def _handle_repertory_worker_completed(self, result) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._repertory_pending_decision_request_id = None
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)

        self._repertory_processed_files = max(self._repertory_processed_files, int(result.processed_source_files))
        self._repertory_total_files = max(self._repertory_total_files, int(result.total_source_files))
        self._repertory_session_folder = str(getattr(result, "session_folder", "") or "")
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_UPDATE, self._repertory_session_folder)
        completed_ok = bool(getattr(result, "success", not bool(getattr(result, "interrupted", False))))
        if completed_ok:
            selected = str(self._repertory_selected_smartphone_folder or "").strip()
            self._repertory_last_completed_smartphone_folder = selected or None
        else:
            self._repertory_last_completed_smartphone_folder = None

        counters = getattr(result, "counters", {})
        self._repertory_matches_found = max(self._repertory_matches_found, int(
            counters.get(RepertoryStatus.AGGIORNATO.value, 0)
            + counters.get(RepertoryStatus.AGGIORNATO_MULTIPLO.value, 0)
            + counters.get(RepertoryStatus.ERRORE_BACKUP.value, 0)
            + counters.get(RepertoryStatus.ERRORE_COPIA.value, 0)
            + counters.get(RepertoryStatus.ERRORE_VERIFICA.value, 0)
        ))
        self._repertory_files_updated = max(self._repertory_files_updated, int(
            counters.get(RepertoryStatus.AGGIORNATO.value, 0)
            + counters.get(RepertoryStatus.AGGIORNATO_MULTIPLO.value, 0)
        ))
        self._repertory_files_not_found = max(
            self._repertory_files_not_found,
            int(counters.get(RepertoryStatus.NON_TROVATO.value, 0)),
        )
        self._repertory_errors = max(
            self._repertory_errors,
            int(counters.get(RepertoryStatus.ERRORE_SORGENTE.value, 0))
            + int(counters.get(RepertoryStatus.ERRORE_BACKUP.value, 0))
            + int(counters.get(RepertoryStatus.ERRORE_COPIA.value, 0))
            + int(counters.get(RepertoryStatus.ERRORE_VERIFICA.value, 0))
            + int(counters.get(RepertoryStatus.AMBIGUO.value, 0)),
        )

        self._render_repertory_runtime_counters()

        if self._repertory_progress_bar is not None:
            total = max(0, int(result.total_source_files))
            done = max(0, int(result.processed_source_files))
            self._repertory_progress_bar.set(0 if total <= 0 else min(1.0, done / float(total)))

        self._update_repertory_open_results_button_state()
        self._update_repertory_android_buttons_state()

        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(
                text="Operazione interrotta" if bool(getattr(result, "interrupted", False)) else "Operazione completata"
            )

        self._append_repertory_log(f"Report CSV: {result.report_paths.get('csv', '')}")
        self._append_repertory_log(f"Report HTML: {result.report_paths.get('html', '')}")
        self._append_repertory_log(f"Report XLSX: {result.report_paths.get('xlsx', '')}")
        self._append_repertory_log(f"Log sessione: {result.log_path}")
        self._append_repertory_log(f"Cartella sessione: {self._repertory_session_folder}")

        if bool(getattr(result, "interrupted", False)):
            message = (
                "Operazione interrotta.\n"
                f"File elaborati: {result.processed_source_files}/{result.total_source_files}\n"
                f"Risultati parziali: {self._repertory_session_folder}"
            )
        else:
            repertory_update_errors = (
                int(counters.get(RepertoryStatus.ERRORE_SORGENTE.value, 0))
                + int(counters.get(RepertoryStatus.ERRORE_BACKUP.value, 0))
                + int(counters.get(RepertoryStatus.ERRORE_COPIA.value, 0))
                + int(counters.get(RepertoryStatus.ERRORE_VERIFICA.value, 0))
            )
            smartphone_copied = int(counters.get(COUNTER_SMARTPHONE_TABLET_COPIATI, 0))
            smartphone_errors = int(counters.get(COUNTER_SMARTPHONE_TABLET_ERRORI, 0))
            repertory_updated_copies = int(counters.get(COUNTER_COPIE_AGGIORNATE_REPERTORIO, 0))
            updated_tracks = int(counters.get(COUNTER_BRANI_AGGIORNATI, 0))
            files_updated = int(
                counters.get(RepertoryStatus.AGGIORNATO.value, 0)
                + counters.get(RepertoryStatus.AGGIORNATO_MULTIPLO.value, 0)
            )
            if repertory_updated_copies <= 0:
                repertory_updated_copies = files_updated
            if updated_tracks <= 0 and files_updated > 0:
                updated_tracks = min(int(result.processed_source_files), files_updated)
            files_kept = int(counters.get(COUNTER_FILE_MANTENUTI, 0))
            repertory_not_found_count = int(counters.get(COUNTER_FILE_NON_TROVATI_NEL_REPERTORIO, 0))
            repertory_not_found_copy_errors = int(counters.get(COUNTER_FILE_NON_TROVATI_ERRORI_COPIA, 0))
            repertory_to_insert_count = int(counters.get(COUNTER_BRANI_DA_INSERIRE, 0))
            repertory_to_insert_errors = int(counters.get(COUNTER_BRANI_DA_INSERIRE_ERRORI, 0))
            total_errors = (
                repertory_update_errors
                + smartphone_errors
                + repertory_not_found_copy_errors
                + repertory_to_insert_errors
            )

            summary_lines = [
                "ORGANIZZAZIONE REPERTORIO COMPLETATA",
                "",
                f"Brani elaborati: {result.processed_source_files}",
                f"Brani aggiornati: {updated_tracks}",
                f"Copie aggiornate nel Repertorio: {repertory_updated_copies}",
                f"Brani mantenuti: {files_kept}",
                f"Brani non trovati nel Repertorio: {repertory_not_found_count}",
                f"Brani non trovati da inserire: {repertory_to_insert_count}",
                f"Copie in cartella per dispositivo Android: {smartphone_copied}",
                f"Errori: {total_errors}",
                "",
            ]
            if total_errors > 0:
                summary_lines.append("Consultare i report per il dettaglio degli errori.")
            summary_lines.extend(
                [
                    "I dettagli completi sono disponibili nei report.",
                    "Risultati salvati nella cartella della sessione.",
                ]
            )
            message = "\n".join(summary_lines)
        messagebox.showinfo("Organizza repertorio", message, parent=self._repertory_dialog)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

    def _repertory_worker_error(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_worker_error, message)

    def _handle_repertory_worker_error(self, message: str) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._repertory_pending_decision_request_id = None
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Errore organizzazione repertorio")
        self._append_repertory_log(f"ERRORE: {message}")
        messagebox.showerror("Organizza repertorio", message, parent=self._repertory_dialog)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

    def _repertory_worker_cancelled(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_worker_cancelled, message)

    def _handle_repertory_worker_cancelled(self, message: str) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._repertory_pending_decision_request_id = None
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Organizzazione repertorio interrotta")
        self._append_repertory_log(message)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

    def _repertory_diagnostics_worker_progress(self, current: int, total: int, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_diagnostics_worker_progress, current, total, message)

    def _handle_repertory_diagnostics_worker_progress(self, current: int, total: int, message: str) -> None:
        self._repertory_processed_files = max(0, int(current))
        self._repertory_total_files = max(self._repertory_total_files, int(total))
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text=message)
        if self._repertory_progress_bar is not None:
            self._repertory_progress_bar.set(0 if total <= 0 else min(1.0, max(0.0, current / float(total))))
        self._render_repertory_runtime_counters()

    def _repertory_diagnostics_worker_log(self, message: str) -> None:
        self._schedule_tracked_after(0, self._append_repertory_log, message)

    def _repertory_diagnostics_worker_completed(self, result) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_diagnostics_worker_completed, result)

    def _handle_repertory_diagnostics_worker_completed(self, result) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._repertory_pending_decision_request_id = None
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)

        self._repertory_session_folder = str(getattr(result, "session_folder", "") or "")
        self._set_repertory_results_folder_for_mode(REPERTORY_MODE_DIAGNOSTICS, self._repertory_session_folder)
        self._repertory_processed_files = int(getattr(result, "analyzed_split_files", 0)) + int(getattr(result, "analyzed_general_files", 0))
        self._repertory_total_files = max(self._repertory_processed_files, 1)
        self._render_repertory_runtime_counters()

        if self._repertory_progress_bar is not None:
            self._repertory_progress_bar.set(1)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Diagnosi repertorio completata")
        self._update_repertory_open_results_button_state()

        missing_in_general = int(getattr(result, "only_split", 0))
        missing_in_split = int(getattr(result, "only_general", 0))
        total_errors = int(getattr(result, "read_errors", 0)) + int(getattr(result, "copy_errors", 0))
        is_aligned = (missing_in_general == 0 and missing_in_split == 0 and total_errors == 0)

        if is_aligned:
            summary = (
                "DIAGNOSI REPERTORIO COMPLETATA\n\n"
                "ESITO: QUADRATURA COMPLETA\n\n"
                f"Errori: {total_errors}\n\n"
                "I dettagli completi sono disponibili nei report."
            )
        else:
            summary = (
                "DIAGNOSI REPERTORIO COMPLETATA\n\n"
                "ESITO: REPERTORI NON ALLINEATI\n\n"
                f"File presenti in entrambi i repertori: {getattr(result, 'matched_both', 0)}\n"
                f"File mancanti nella Cartella Generale: {missing_in_general}\n"
                f"File mancanti nel Repertorio suddiviso: {missing_in_split}\n\n"
                f"Errori: {total_errors}\n"
                + ("Consultare i report per il dettaglio degli errori.\n\n" if total_errors > 0 else "\n")
                + "I dettagli completi sono disponibili nei report."
            )
        self._append_repertory_log("Diagnosi repertorio completata.")
        self._append_repertory_log(f"Report CSV: {result.report_paths.get('csv', '')}")
        self._append_repertory_log(f"Report cartelle CSV: {getattr(result, 'folder_report_paths', {}).get('csv', '')}")
        self._append_repertory_log(f"Report XLSX: {result.report_paths.get('xlsx', '')}")
        self._append_repertory_log(f"Report HTML: {result.report_paths.get('html', '')}")
        messagebox.showinfo("Organizza repertorio", summary, parent=self._repertory_dialog)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

    def _repertory_diagnostics_worker_error(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_diagnostics_worker_error, message)

    def _handle_repertory_diagnostics_worker_error(self, message: str) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Errore diagnosi repertorio")
        self._append_repertory_log(f"ERRORE: {message}")
        messagebox.showerror("Organizza repertorio", message, parent=self._repertory_dialog)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

    def _repertory_diagnostics_worker_cancelled(self, message: str) -> None:
        self._schedule_tracked_after(0, self._handle_repertory_diagnostics_worker_cancelled, message)

    def _handle_repertory_diagnostics_worker_cancelled(self, message: str) -> None:
        self._stop_repertory_timer()
        self._repertory_allow_session_log_updates = False
        self._close_repertory_decision_dialog()
        self._set_repertory_ui_running_state(False)
        self._update_controls_state()
        self._schedule_tracked_after(0, self._update_repertory_primary_action_state)
        if self._repertory_status_label is not None:
            self._repertory_status_label.configure(text="Diagnosi repertorio interrotta")
        self._append_repertory_log(message)
        if self._repertory_close_requested:
            self._finalize_repertory_window_close()

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

    def _configure_main_window_geometry(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        window_width = min(1180, max(900, screen_width - 80))
        window_height = min(790, max(650, screen_height - 120))

        x_position = max(0, (screen_width - window_width) // 2)
        y_position = max(0, (screen_height - window_height) // 2)

        self.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")
        self.minsize(900, 630)
        self.resizable(True, True)

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
                "repertory_updates_folder": self._repertory_updates_entry.get().strip() if self._repertory_updates_entry is not None else str(self.settings.get("repertory_updates_folder", "")),
                "repertory_library_folder": self._repertory_library_entry.get().strip() if self._repertory_library_entry is not None else str(self.settings.get("repertory_library_folder", "")),
                "repertory_general_folder": self._repertory_general_entry.get().strip() if self._repertory_general_entry is not None else str(self.settings.get("repertory_general_folder", "")),
                "repertory_results_folder": self._repertory_results_entry.get().strip() if self._repertory_results_entry is not None else str(self.settings.get("repertory_results_folder", "")),
                "repertory_smartphone_folder": self._repertory_smartphone_entry.get().strip() if self._repertory_smartphone_entry is not None else str(self.settings.get("repertory_smartphone_folder", self._repertory_smartphone_root)),
                "repertory_backup_enabled": bool(self._repertory_backup_var.get()),
                "appearance_mode": self.appearance_combo.get() if hasattr(self, "appearance_combo") else str(self.settings.get("appearance_mode", "blue"))
            }
        )
        self.settings_manager.save(self.settings)

    def on_close(self, _shutdown_after_cancel: bool = False) -> None:
        if getattr(self, "_destroy_completed", False) or getattr(self, "_is_destroying", False):
            return

        def _is_running(attr_name: str) -> bool:
            worker = getattr(self, attr_name, None)
            return bool(getattr(worker, "is_running", False))

        def _cancel_worker(attr_name: str) -> None:
            worker = getattr(self, attr_name, None)
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()

        if _is_running("worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "La creazione del mix è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if _is_running("extract_worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "L'estrazione song è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("extract_worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if _is_running("diagnostics_worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "La diagnostica MP3 è ancora in corso.\n"
                    "Vuoi annullarla e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("diagnostics_worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if _is_running("recovery_worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "Il recupero MP3 è ancora in corso.\n"
                    "Vuoi interromperlo e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("recovery_worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if _is_running("repertory_worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "L'organizzazione repertorio e ancora in corso.\n"
                    "Vuoi interromperla e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("repertory_worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if _is_running("rep003_worker"):
            if not _shutdown_after_cancel:
                confirm = messagebox.askyesno(
                    "Operazione in corso",
                    "L'inserimento nuovi brani e ancora in corso.\n"
                    "Vuoi interromperlo e chiudere il programma?"
                )
                if not confirm:
                    return
            _cancel_worker("rep003_worker")
            self._shutdown_after_job = self._schedule_tracked_after(150, self.on_close, True)
            return

        if not self._confirm_save_if_dirty():
            return

        self.save_settings()
        self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_destroy_completed", False):
            return
        if getattr(self, "_is_destroying", False):
            return

        def _safe_get(name: str, default=None):
            return getattr(self, name, default)

        def _safe_cleanup_tooltips(owner=None) -> None:
            cleanup_tooltips = getattr(self, "_cleanup_tooltips", None)
            if callable(cleanup_tooltips):
                try:
                    cleanup_tooltips(owner=owner)
                except Exception:
                    pass

        self._is_destroying = True
        try:
            try:
                self._cancel_matching_after_scripts(
                    "check_dpi_scaling",
                    "update",
                    "_windows_set_titlebar_icon",
                    "_revert_withdraw_after_windows_set_titlebar_color",
                    "_check_if_scrollbars_needed",
                    "focus_set",
                    "<lambda>",
                )
            except Exception:
                pass

            shutdown_after_job = _safe_get("_shutdown_after_job")
            if shutdown_after_job is not None:
                try:
                    self._cancel_tracked_after_job(shutdown_after_job)
                except Exception:
                    pass
            self._shutdown_after_job = None

            for stopper_name in ("_stop_repertory_timer", "_stop_recovery_timer", "_stop_diagnostics_timer"):
                stopper = _safe_get(stopper_name)
                if callable(stopper):
                    try:
                        stopper()
                    except Exception:
                        pass

            timer_job = _safe_get("timer_job")
            if timer_job is not None:
                try:
                    self._cancel_tracked_after_job(timer_job)
                except Exception:
                    pass
            self.timer_job = None

            try:
                grab_widget = self.grab_current()
            except Exception:
                grab_widget = None
            if grab_widget is not None:
                try:
                    grab_widget.grab_release()
                except Exception:
                    pass

            close_repertory_decision_dialog = _safe_get("_close_repertory_decision_dialog")
            if callable(close_repertory_decision_dialog):
                try:
                    close_repertory_decision_dialog()
                except Exception:
                    pass

            repertory_dialog = _safe_get("_repertory_dialog")
            if repertory_dialog is not None:
                finalize_repertory_window_close = _safe_get("_finalize_repertory_window_close")
                if callable(finalize_repertory_window_close):
                    try:
                        finalize_repertory_window_close()
                    except Exception:
                        pass

            extract_progress_dialog = _safe_get("_extract_progress_dialog")
            if extract_progress_dialog is not None:
                try:
                    extract_progress_dialog.destroy()
                except Exception:
                    pass
                self._extract_progress_dialog = None

            recovery_forced_confirmation_dialog = _safe_get("_recovery_forced_confirmation_dialog")
            if recovery_forced_confirmation_dialog is not None:
                try:
                    recovery_forced_confirmation_dialog.destroy()
                except Exception:
                    pass
                self._recovery_forced_confirmation_dialog = None

            recovery_dialog = _safe_get("_recovery_dialog")
            if recovery_dialog is not None:
                _safe_cleanup_tooltips(owner=recovery_dialog)
                try:
                    recovery_dialog.destroy()
                except Exception:
                    pass
                self._recovery_dialog = None

            close_rep003_window = _safe_get("_close_rep003_window")
            if callable(close_rep003_window):
                try:
                    close_rep003_window()
                except Exception:
                    pass

            diagnostics_window = _safe_get("diagnostics_window")
            if diagnostics_window is not None:
                _safe_cleanup_tooltips(owner=diagnostics_window)
                try:
                    diagnostics_window.destroy()
                except Exception:
                    pass
                self.diagnostics_window = None

            _safe_cleanup_tooltips()

            cancel_tracked_after_jobs = _safe_get("_cancel_tracked_after_jobs")
            if callable(cancel_tracked_after_jobs):
                try:
                    cancel_tracked_after_jobs()
                except Exception:
                    pass

            try:
                if self._widget_exists(self):
                    self.update_idletasks()
            except Exception:
                pass

            try:
                super().destroy()
            except (tk.TclError, RuntimeError):
                pass
        finally:
            tracked_after_jobs = _safe_get("_tracked_after_jobs")
            if isinstance(tracked_after_jobs, set):
                tracked_after_jobs.clear()
            self._destroy_completed = True
            self._is_destroying = False
