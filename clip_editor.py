# -*- coding: utf-8 -*-
"""
clip_editor.py

Finestra modale per l'editing delle clip ad hoc con riproduzione MP3.
"""

from pathlib import Path
import ctypes
import time
from typing import Callable, Optional
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import vlc

from clip_info import ClipInfo
from clip_player_vlc import ClipPlayerVLC
from tooltip import Tooltip
from waveform_widget import WaveformWidget
from waveform_analyzer import ENABLE_PERFORMANCE_LOG, log_perf


OUTER_MARGIN = 10
CARD_GAP = 8
CARD_PADDING = 10
TOP_TOOLBAR_HEIGHT = 54
TOP_GROUP_HEIGHT = 42
TIME_FIELD_HEIGHT = 36
MOVE_BUTTON_HEIGHT = 38
TRANSPORT_BUTTON_HEIGHT = 36
SECONDARY_ACTION_HEIGHT = 36
CONFIRM_BUTTON_HEIGHT = 36
STATUS_BAR_HEIGHT = 24
CONTROLS_PANEL_HEIGHT = 80
CARD_CORNER_RADIUS = 8

TITLE_TEXT_COLOR = ("#9099a3", "#9099a3")
VALUE_TEXT_COLOR = ("#e7ebf0", "#e7ebf0")
ACCENT_TEXT_COLOR = ("#89a8c6", "#89a8c6")

WINDOW_BG_COLOR = ("#131820", "#131820")
CARD_BG_COLOR = ("#181e26", "#181e26")
TOOLBAR_BG_COLOR = ("#151b23", "#151b23")
STATUS_BG_COLOR = ("#11161d", "#11161d")
BORDER_COLOR = ("#242b34", "#242b34")

PRIMARY_BUTTON_FG = ("#2d4f72", "#2d4f72")
PRIMARY_BUTTON_HOVER = ("#3a628b", "#3a628b")
PRIMARY_BUTTON_BORDER = ("#3a628b", "#3a628b")
PRIMARY_BUTTON_TEXT = VALUE_TEXT_COLOR

SECONDARY_BUTTON_FG = ("#2f353d", "#2f353d")
SECONDARY_BUTTON_HOVER = ("#3a424b", "#3a424b")
SECONDARY_BUTTON_BORDER = ("#3a424b", "#3a424b")
SECONDARY_BUTTON_TEXT = VALUE_TEXT_COLOR

TOOL_BUTTON_FG = ("#4B5563", "#4B5563")
TOOL_BUTTON_HOVER = ("#5B6675", "#5B6675")
TOOL_BUTTON_BORDER = ("#6B7280", "#6B7280")
TOOL_BUTTON_TEXT = VALUE_TEXT_COLOR

TITLE_FONT_SIZE = 9
VALUE_FONT_SIZE = 17
METRIC_LABEL_FONT_SIZE = 8
METRIC_VALUE_FONT_SIZE = 17

BUTTON_TEXT_SIZE = 13
TOOLBAR_VALUE_SIZE = 18

SMALL_BUTTON_WIDTH = 74


class _PerfTracer:
    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        log_perf(label, now - self._last)
        self._last = now

    def total(self, label: str) -> None:
        if not ENABLE_PERFORMANCE_LOG:
            return
        log_perf(label, time.perf_counter() - self._start)


def _format_timecode(milliseconds: int) -> str:
    if milliseconds < 0:
        milliseconds = 0

    hours = milliseconds // 3_600_000
    remainder = milliseconds % 3_600_000
    minutes = remainder // 60_000
    seconds = (remainder % 60_000) / 1000.0

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"

    return f"{minutes:02d}:{seconds:06.3f}"


def _parse_timecode(value: str) -> int:
    if value is None:
        raise ValueError("Valore mancante")

    text = value.strip().replace(",", ".")
    if not text:
        raise ValueError("Valore mancante")

    parts = text.split(":")
    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    elif len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        seconds = float(parts[1])
    elif len(parts) == 1:
        hours = 0
        minutes = 0
        seconds = float(parts[0])
    else:
        raise ValueError("Formato orario non valido")

    if minutes < 0 or seconds < 0 or hours < 0:
        raise ValueError("Valori temporali non validi")

    total_ms = int(round((hours * 3600 + minutes * 60 + seconds) * 1000))
    return total_ms


class ClipEditorDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        mp3_path: str | Path,
        clip_info: ClipInfo | None,
        callback: Callable[[ClipInfo], None]
    ) -> None:
        super().__init__(parent)

        self._perf = _PerfTracer()

        self.parent = parent
        self.mp3_path = Path(mp3_path)
        self.clip_info = clip_info.copy() if clip_info is not None else ClipInfo()
        self.callback = callback
        self.tooltips: list[Tooltip] = []
        self.after_id: str | None = None
        self.slider_after_id: str | None = None
        self._slider_dragging = False
        self._seek_in_progress = False
        self._seek_target_ms: int | None = None
        self._seek_started_at = 0.0
        self._seek_poll_after_id: str | None = None
        self._seek_source = ""
        self._manual_waveform_seek = False
        self._seek_tolerance_ms = 180
        self._seek_timeout_ms = 1800
        self._play_start_pending_until = 0.0
        self.play_state = "stopped"
        self.previewing_clip = False
        self.current_ms = 0
        self.preview_end_ms = 0
        self._ignore_slider_change = False
        self.player: ClipPlayerVLC | None = None
        self.waveform_widget: Optional[WaveformWidget] = None
        self.position_slider: Optional[ctk.CTkSlider] = None
        self._file_name_after_id: str | None = None
        self._full_file_name = self.mp3_path.name
        self.status_audio_label: Optional[ctk.CTkLabel] = None
        self.status_help_label: Optional[ctk.CTkLabel] = None
        self.elapsed_from_in_label: Optional[ctk.CTkLabel] = None
        self.elapsed_from_in_value_label: Optional[ctk.CTkLabel] = None
        self._start_maximized = False

        self.title("Clip Editor")
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.resizable(True, True)
        self.minsize(1180, 720)
        self.geometry("1240x740")
        try:
            self.state("zoomed")
            self._start_maximized = True
        except Exception:
            self._start_maximized = False
        self.configure(fg_color=WINDOW_BG_COLOR)
        self.after(0, self._apply_windows_titlebar_style)
        self._perf.mark("window_created")

        try:
            self._initialize_audio()
            self._perf.mark("vlc_media_ready")
            self.total_duration_ms = self.player.get_duration_ms() if self.player else 0
            if self.total_duration_ms <= 0:
                raise RuntimeError("Impossibile determinare la durata del file MP3.")
            self._perf.mark("media_metadata_ready")
        except Exception as error:
            messagebox.showerror(
                "Errore",
                f"Impossibile caricare il file MP3:\n{error}"
            )
            self.destroy()
            return

        self.total_seconds = self.total_duration_ms / 1000.0
        self.in_ms = self.clip_info.clip_start_ms if self.clip_info.use_custom_clip else 0
        self.out_ms = self.clip_info.clip_end_ms if self.clip_info.use_custom_clip else self.total_duration_ms
        self.in_ms = max(0, min(self.in_ms, self.total_duration_ms))
        self.out_ms = max(self.in_ms + 1, min(self.out_ms, self.total_duration_ms))
        self.current_ms = self.in_ms
        self.preview_end_ms = self.out_ms

        self._build_ui()
        self.after_idle(self._center_on_parent)
        self._perf.mark("ui_built")
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.bind("<Return>", self._on_confirm_shortcut, add="+")
        self.bind("<Escape>", self._on_cancel_shortcut, add="+")
        self._refresh_ui()
        self._schedule_update()
        self._perf.total("editor_interactive_ready_total")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header_compact(row=0)

        self._build_waveform(row=1)

        self._build_in_out_controls(row=2)
        self._build_controls_panels(row=3)
        self._build_action_buttons(row=4)
        self._build_status_bar(row=5)
        self._update_file_name_display()

    def _build_header_compact(self, row: int) -> None:
        header_frame = ctk.CTkFrame(
            self,
            corner_radius=CARD_CORNER_RADIUS,
            fg_color=TOOLBAR_BG_COLOR,
            border_width=0,
            border_color=BORDER_COLOR,
            height=TOP_TOOLBAR_HEIGHT,
        )
        header_frame.grid(row=row, column=0, sticky="ew", padx=OUTER_MARGIN, pady=0)
        header_frame.grid_propagate(False)
        # Keep this row compact and naturally sized.
        header_frame.grid_rowconfigure(0, weight=1)
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_columnconfigure(1, weight=0)
        header_frame.grid_columnconfigure(2, weight=0)
        header_frame.grid_columnconfigure(3, weight=0)
        header_frame.grid_columnconfigure(4, weight=0)
        header_frame.grid_columnconfigure(5, weight=0)
        header_frame.grid_columnconfigure(6, weight=0)
        header_frame.grid_columnconfigure(7, weight=0)
        header_frame.grid_columnconfigure(8, weight=0)

        file_frame = ctk.CTkFrame(
            header_frame,
            fg_color="transparent",
            height=TOP_GROUP_HEIGHT,
        )
        file_frame.grid(row=0, column=0, sticky="nsew", padx=(CARD_PADDING, 8), pady=0)
        file_frame.grid_propagate(False)
        file_frame.grid_rowconfigure(0, weight=1)
        file_frame.grid_columnconfigure(2, weight=1)

        self.file_icon_label = ctk.CTkLabel(
            file_frame,
            text="♪",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=ACCENT_TEXT_COLOR,
            width=24,
        )
        self.file_icon_label.grid(row=0, column=0, padx=(0, 6), sticky="w")

        file_type_label = ctk.CTkLabel(
            file_frame,
            text="MP3",
            font=ctk.CTkFont(size=8, weight="bold"),
            text_color=ACCENT_TEXT_COLOR,
        )
        file_type_label.grid(row=0, column=1, padx=(0, 8), sticky="w")

        self.file_name_label = ctk.CTkLabel(
            file_frame,
            text=self._full_file_name,
            anchor="w",
            text_color=VALUE_TEXT_COLOR,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.file_name_label.grid(row=0, column=2, sticky="nsew")
        self._add_tooltip(self.file_name_label, self._full_file_name)

        self._build_toolbar_separator(header_frame, 1)
        self._build_metric_box(header_frame, 2, "Totale", _format_timecode(self.total_duration_ms), "total_duration_value")
        self._build_toolbar_separator(header_frame, 3)
        self._build_metric_box(header_frame, 4, "Posizione", "00:00.000", "current_position_label")
        self._build_toolbar_separator(header_frame, 5)
        self._build_metric_box(header_frame, 6, "Clip", "00:00.000", "clip_duration_label")
        self._build_toolbar_separator(header_frame, 7)

        zoom_frame = ctk.CTkFrame(
            header_frame,
            corner_radius=6,
            fg_color="transparent",
            height=TOP_GROUP_HEIGHT,
        )
        zoom_frame.grid(row=0, column=8, sticky="e", padx=(4, CARD_PADDING), pady=0)
        zoom_frame.grid_propagate(False)
        zoom_frame.grid_rowconfigure(0, weight=1)
        zoom_frame.grid_columnconfigure((0, 1, 2), weight=0)

        self.zoom_minus_button = ctk.CTkButton(
            zoom_frame,
            text="🔍−",
            width=30,
            height=28,
            command=self._zoom_out_ui,
            fg_color=TOOL_BUTTON_FG,
            hover_color=TOOL_BUTTON_HOVER,
            border_width=1,
            border_color=TOOL_BUTTON_BORDER,
            text_color=TOOL_BUTTON_TEXT,
            corner_radius=6,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.zoom_minus_button.grid(row=0, column=0, padx=(0, 2), pady=0)

        self.zoom_plus_button = ctk.CTkButton(
            zoom_frame,
            text="🔍+",
            width=30,
            height=28,
            command=self._zoom_in_ui,
            fg_color=TOOL_BUTTON_FG,
            hover_color=TOOL_BUTTON_HOVER,
            border_width=1,
            border_color=TOOL_BUTTON_BORDER,
            text_color=TOOL_BUTTON_TEXT,
            corner_radius=6,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.zoom_plus_button.grid(row=0, column=1, padx=(0, 2), pady=0)

        self.zoom_fit_button = ctk.CTkButton(
            zoom_frame,
            text="Mostra tutto",
            width=92,
            height=28,
            command=self._zoom_fit_ui,
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            text_color=PRIMARY_BUTTON_TEXT,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            font=ctk.CTkFont(size=9, weight="bold"),
            corner_radius=6,
        )
        self.zoom_fit_button.grid(row=0, column=2, padx=0, pady=0)

        self._add_tooltip(self.zoom_minus_button, "Riduci zoom waveform\nCtrl + rotellina giu")
        self._add_tooltip(self.zoom_plus_button, "Aumenta zoom waveform\nCtrl + rotellina su")
        self._add_tooltip(self.zoom_fit_button, "Ripristina lo zoom 1x")

    def _build_toolbar_separator(self, parent, column: int) -> None:
        separator = ctk.CTkFrame(parent, width=1, fg_color=("#2a313b", "#2a313b"))
        separator.grid(row=0, column=column, sticky="ns", pady=0)

    def _build_metric_box(self, parent, column: int, title: str, value: str, attr_name: str) -> None:
        box = ctk.CTkFrame(
            parent,
            fg_color="transparent",
            height=TOP_GROUP_HEIGHT,
        )
        box.grid(row=0, column=column, sticky="nsew", padx=(5, 5), pady=0)
        box.grid_propagate(False)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=0)
        box.grid_columnconfigure(1, weight=1)
        metric_label = ctk.CTkLabel(
            box,
            text=title,
            font=ctk.CTkFont(size=METRIC_LABEL_FONT_SIZE, weight="bold"),
            text_color=TITLE_TEXT_COLOR,
        )
        metric_label.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 0))
        value_label = ctk.CTkLabel(
            box,
            text=value,
            font=ctk.CTkFont(size=TOOLBAR_VALUE_SIZE, weight="bold"),
            text_color=VALUE_TEXT_COLOR,
            anchor="w",
        )
        value_label.grid(row=0, column=1, sticky="w")
        setattr(self, attr_name, value_label)

    def _build_in_out_controls(self, row: int) -> None:
        panel = ctk.CTkFrame(self, fg_color="transparent")
        self._in_out_panel = panel
        panel.grid(row=row, column=0, sticky="ew", padx=OUTER_MARGIN, pady=(0, CARD_GAP))
        panel.grid_columnconfigure(0, weight=35)
        panel.grid_columnconfigure(1, weight=30)
        panel.grid_columnconfigure(2, weight=35)

        in_frame = ctk.CTkFrame(
            panel,
            corner_radius=CARD_CORNER_RADIUS,
            fg_color=CARD_BG_COLOR,
            border_width=1,
            border_color=BORDER_COLOR,
            height=100,
        )
        in_frame.grid(row=0, column=0, sticky="nsew", padx=(0, CARD_GAP), pady=0)
        in_frame.grid_propagate(False)
        in_frame.grid_columnconfigure(0, weight=0)
        in_frame.grid_columnconfigure(1, weight=1)
        in_frame.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(
            in_frame,
            text="Punto IN",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TITLE_TEXT_COLOR,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(6, 4))
        self.in_entry = ctk.CTkEntry(
            in_frame,
            height=TIME_FIELD_HEIGHT,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=("#141b24", "#141b24"),
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.in_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(CARD_PADDING, 8), pady=(0, 8))
        self.in_entry.insert(0, _format_timecode(self.in_ms))
        self.in_entry.bind("<FocusOut>", lambda _event: self._normalize_time_fields())
        self.in_entry.bind("<Return>", self._on_time_entry_return)
        self._add_tooltip(
            self.in_entry,
            "Inserisci manualmente il tempo di inizio della clip."
        )

        set_in_button = ctk.CTkButton(
            in_frame,
            text="Imposta IN",
            width=124,
            height=36,
            command=self._set_in_from_current,
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            corner_radius=6,
        )
        set_in_button.grid(row=1, column=2, sticky="ew", padx=(0, CARD_PADDING), pady=(0, 8))
        self._add_tooltip(
            set_in_button,
            "Imposta il punto IN sul valore corrente della riproduzione."
        )

        center_frame = ctk.CTkFrame(
            panel,
            corner_radius=CARD_CORNER_RADIUS,
            fg_color=CARD_BG_COLOR,
            border_width=1,
            border_color=BORDER_COLOR,
            height=124,
        )
        center_frame.grid(row=0, column=1, sticky="nsew", padx=(0, CARD_GAP), pady=0)
        center_frame.grid_propagate(False)
        center_frame.grid_columnconfigure(0, weight=0)
        center_frame.grid_columnconfigure(1, weight=0)
        center_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            center_frame,
            text="Durata clip",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TITLE_TEXT_COLOR,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(6, 4))
        self.clip_duration_panel_label = ctk.CTkLabel(
            center_frame,
            text=_format_timecode(max(0, self.out_ms - self.in_ms)),
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=VALUE_TEXT_COLOR,
            anchor="w"
        )
        self.clip_duration_panel_label.grid(row=1, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(0, 2))
        self.clip_range_label = ctk.CTkLabel(
            center_frame,
            text=f"{_format_timecode(self.in_ms)} -> {_format_timecode(self.out_ms)}",
            font=ctk.CTkFont(size=11),
            anchor="w",
            text_color=TITLE_TEXT_COLOR,
        )
        self.clip_range_label.grid(row=2, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(0, 6))

        self.elapsed_from_in_label = ctk.CTkLabel(
            center_frame,
            text="Da IN:",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
            text_color=TITLE_TEXT_COLOR,
        )
        self.elapsed_from_in_label.grid(row=3, column=0, sticky="w", padx=CARD_PADDING, pady=(0, 6))

        self.elapsed_from_in_value_label = ctk.CTkLabel(
            center_frame,
            text="00:00.000",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
            text_color=VALUE_TEXT_COLOR,
        )
        self.elapsed_from_in_value_label.grid(row=3, column=1, columnspan=2, sticky="w", padx=(6, 0), pady=(0, 6))

        out_frame = ctk.CTkFrame(
            panel,
            corner_radius=CARD_CORNER_RADIUS,
            fg_color=CARD_BG_COLOR,
            border_width=1,
            border_color=BORDER_COLOR,
            height=100,
        )
        out_frame.grid(row=0, column=2, sticky="nsew", padx=0, pady=0)
        out_frame.grid_propagate(False)
        out_frame.grid_columnconfigure(0, weight=0)
        out_frame.grid_columnconfigure(1, weight=1)
        out_frame.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(
            out_frame,
            text="Punto OUT",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TITLE_TEXT_COLOR,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(6, 4))
        self.out_entry = ctk.CTkEntry(
            out_frame,
            height=TIME_FIELD_HEIGHT,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=("#141b24", "#141b24"),
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.out_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(CARD_PADDING, 8), pady=(0, 8))
        self.out_entry.insert(0, _format_timecode(self.out_ms))
        self.out_entry.bind("<FocusOut>", lambda _event: self._normalize_time_fields())
        self.out_entry.bind("<Return>", self._on_time_entry_return)
        self._add_tooltip(
            self.out_entry,
            "Inserisci manualmente il tempo di fine della clip."
        )

        set_out_button = ctk.CTkButton(
            out_frame,
            text="Imposta OUT",
            width=124,
            height=36,
            command=self._set_out_from_current,
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            corner_radius=6,
        )
        set_out_button.grid(row=1, column=2, sticky="ew", padx=(0, CARD_PADDING), pady=(0, 8))
        self._add_tooltip(
            set_out_button,
            "Imposta il punto OUT sul valore corrente della riproduzione."
        )

    def _build_controls_panels(self, row: int) -> None:
        controls_row = ctk.CTkFrame(self, fg_color="transparent")
        self._controls_row = controls_row
        controls_row.grid(row=row, column=0, sticky="ew", padx=OUTER_MARGIN, pady=(0, CARD_GAP))
        controls_row.grid_columnconfigure(0, weight=4)
        controls_row.grid_columnconfigure(1, weight=2)
        controls_row.grid_columnconfigure(2, weight=2)
        controls_row.grid_columnconfigure(3, weight=2)

        self._build_precision_controls(controls_row, 0)
        self._build_playback_controls(controls_row, 1)
        self._build_preview_controls(controls_row, 2)
        self._build_extra_controls(controls_row, 3)

    def _build_precision_controls(self, parent, column: int) -> None:
        precision_frame = ctk.CTkFrame(
            parent,
            corner_radius=6,
            fg_color=("#171d25", "#171d25"),
            border_width=0,
            border_color=BORDER_COLOR,
            height=78,
        )
        precision_frame.grid(row=0, column=column, sticky="nsew", padx=(0, CARD_GAP), pady=0)
        precision_frame.grid_propagate(False)
        precision_frame.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

        ctk.CTkLabel(precision_frame, text="Sposta posizione", font=ctk.CTkFont(size=10, weight="bold"), text_color=TITLE_TEXT_COLOR).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=CARD_PADDING, pady=(4, 2)
        )

        buttons = [
            ("|◀ 10 s", lambda: self._move_current_position(-10_000), "Torna indietro di 10 secondi"),
            ("◀ 1 s", lambda: self._move_current_position(-1_000), "Torna indietro di 1 secondo"),
            ("◀ 100 ms", lambda: self._move_current_position(-100), "Torna indietro di 100 ms"),
            ("100 ms ▶", lambda: self._move_current_position(100), "Avanza di 100 ms"),
            ("1 s ▶", lambda: self._move_current_position(1_000), "Avanza di 1 secondo"),
            ("10 s ▶|", lambda: self._move_current_position(10_000), "Avanza di 10 secondi")
        ]

        for index, (label, command, tip) in enumerate(buttons):
            button = ctk.CTkButton(
                precision_frame,
                text=label,
                height=36,
                width=70,
                font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
                command=command,
                fg_color=TOOL_BUTTON_FG,
                hover_color=TOOL_BUTTON_HOVER,
                border_width=1,
                border_color=TOOL_BUTTON_BORDER,
                text_color=TOOL_BUTTON_TEXT,
                corner_radius=6,
            )
            button.grid(row=1, column=index, sticky="ew", padx=(0 if index == 0 else 3, 0), pady=(0, 6))
            self._add_tooltip(button, tip)

    def _build_waveform(self, row: int) -> None:
        waveform_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._waveform_frame = waveform_frame
        waveform_frame.grid(row=row, column=0, sticky="nsew", padx=OUTER_MARGIN, pady=(0, CARD_GAP))
        waveform_frame.grid_rowconfigure(0, weight=1)
        waveform_frame.grid_rowconfigure(1, weight=0)
        waveform_frame.grid_columnconfigure(0, weight=1)

        try:
            waveform_start = time.perf_counter()
            self.waveform_widget = WaveformWidget(
                waveform_frame,
                height=120,
                on_seek=self._on_waveform_seek,
                on_waveform_event=self._on_waveform_event,
            )
            self.waveform_widget.grid(row=0, column=0, sticky="nsew")
            self.waveform_widget.load_audio(self.mp3_path, self.total_duration_ms)
            self._perf.mark("waveform_analysis_started")
            self.waveform_widget.set_selection(self.in_ms, self.out_ms)
            self.waveform_widget.set_position(self.current_ms)
            try:
                self.waveform_widget.controls_frame.grid_remove()
            except Exception:
                pass
            if ENABLE_PERFORMANCE_LOG:
                log_perf("waveform_widget_ready", time.perf_counter() - waveform_start)

            self.position_slider = ctk.CTkSlider(
                waveform_frame,
                from_=0.0,
                to=self.total_seconds,
                number_of_steps=max(int(self.total_seconds * 20), 100),
                command=self._on_slider_move
            )
            self.position_slider.grid(
                row=1,
                column=0,
                sticky="ew",
                padx=0,
                pady=(6, 0)
            )
            self.position_slider.bind("<ButtonPress-1>", self._on_slider_press, add="+")
            self.position_slider.bind("<ButtonRelease-1>", self._on_slider_release, add="+")
            self.position_slider.bind("<B1-Motion>", self._on_slider_move_event, add="+")
            self._add_tooltip(
                self.position_slider,
                "Trascina per spostare la posizione corrente di riproduzione nel brano."
            )
        except Exception as error:
            self.waveform_widget = None
            fallback_label = ctk.CTkLabel(
                waveform_frame,
                text="Waveform non disponibile",
                anchor="center",
                justify="center"
            )
            fallback_label.grid(row=0, column=0, sticky="nsew")

    def _on_waveform_event(self, event_name: str, payload: dict) -> None:
        if event_name == "waveform_ready":
            timings = payload.get("timings", {}) if isinstance(payload, dict) else {}
            total = timings.get("waveform_ready_total")
            if isinstance(total, (int, float)):
                log_perf("waveform_ready_total", float(total))
        elif event_name == "waveform_error":
            if ENABLE_PERFORMANCE_LOG:
                error_text = payload.get("error") if isinstance(payload, dict) else "unknown"
                print(f"[ClipEditorPerf] waveform_error_gui: {error_text}")

    def _on_waveform_seek(self, position_ms: int) -> None:
        self._request_seek(position_ms, source="waveform")

    def _build_playback_controls(self, parent, column: int) -> None:
        controls_frame = ctk.CTkFrame(
            parent,
            corner_radius=6,
            fg_color=("#171d25", "#171d25"),
            border_width=0,
            border_color=BORDER_COLOR,
            height=78,
        )
        controls_frame.grid(row=0, column=column, sticky="nsew", padx=(0, CARD_GAP), pady=0)
        controls_frame.grid_propagate(False)
        controls_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(controls_frame, text="Riproduzione", font=ctk.CTkFont(size=10, weight="bold"), text_color=TITLE_TEXT_COLOR).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=CARD_PADDING, pady=(4, 2)
        )
        btn_font = ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold")
        btn_width = 84
        btn_height = TRANSPORT_BUTTON_HEIGHT

        play_button = ctk.CTkButton(
            controls_frame,
            text="▶  Play",
            command=self._play,
            width=btn_width,
            height=btn_height,
            font=btn_font,
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            corner_radius=6,
        )
        play_button.grid(row=1, column=0, sticky="ew", padx=(CARD_PADDING, 4), pady=(0, 6))
        self._add_tooltip(play_button, "Avvia o riprende la riproduzione - Spazio")

        self.pause_button = ctk.CTkButton(
            controls_frame,
            text="Ⅱ  Pausa",
            command=self._pause,
            width=btn_width,
            height=btn_height,
            font=btn_font,
            fg_color=SECONDARY_BUTTON_FG,
            hover_color=SECONDARY_BUTTON_HOVER,
            border_width=1,
            border_color=SECONDARY_BUTTON_BORDER,
            text_color=SECONDARY_BUTTON_TEXT,
            corner_radius=6,
        )
        self.pause_button.grid(row=1, column=1, sticky="ew", padx=4, pady=(0, 6))
        self._add_tooltip(self.pause_button, "Mette in pausa la riproduzione - Spazio")

        stop_button = ctk.CTkButton(
            controls_frame,
            text="■  Stop",
            command=self._stop,
            width=btn_width,
            height=btn_height,
            font=btn_font,
            fg_color=SECONDARY_BUTTON_FG,
            hover_color=SECONDARY_BUTTON_HOVER,
            border_width=1,
            border_color=SECONDARY_BUTTON_BORDER,
            text_color=SECONDARY_BUTTON_TEXT,
            corner_radius=6,
        )
        stop_button.grid(row=1, column=2, sticky="ew", padx=(4, CARD_PADDING), pady=(0, 6))
        self._add_tooltip(stop_button, "Arresta la riproduzione - S")

    def _build_preview_controls(self, parent, column: int) -> None:
        preview_frame = ctk.CTkFrame(
            parent,
            corner_radius=6,
            fg_color=("#171d25", "#171d25"),
            border_width=0,
            border_color=BORDER_COLOR,
            height=78,
        )
        preview_frame.grid(row=0, column=column, sticky="nsew", padx=(0, CARD_GAP), pady=0)
        preview_frame.grid_propagate(False)
        preview_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(preview_frame, text="Anteprima clip", font=ctk.CTkFont(size=10, weight="bold"), text_color=TITLE_TEXT_COLOR).grid(
            row=0, column=0, sticky="w", padx=CARD_PADDING, pady=(4, 2)
        )

        preview_button = ctk.CTkButton(
            preview_frame,
            text="▶  Ascolta clip",
            command=self._preview_clip,
            height=SECONDARY_ACTION_HEIGHT,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            corner_radius=6,
        )
        preview_button.grid(row=1, column=0, sticky="ew", padx=CARD_PADDING, pady=(0, 6))
        self._add_tooltip(preview_button, "Riproduce solo l'intervallo IN-OUT - A")

    def _build_extra_controls(self, parent, column: int) -> None:
        extra_frame = ctk.CTkFrame(
            parent,
            corner_radius=6,
            fg_color=("#171d25", "#171d25"),
            border_width=0,
            border_color=BORDER_COLOR,
            height=78,
        )
        extra_frame.grid(row=0, column=column, sticky="nsew", padx=0, pady=0)
        extra_frame.grid_propagate(False)
        extra_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(extra_frame, text="Altre azioni", font=ctk.CTkFont(size=10, weight="bold"), text_color=TITLE_TEXT_COLOR).grid(
            row=0, column=0, sticky="w", padx=CARD_PADDING, pady=(4, 2)
        )

        go_in_button = ctk.CTkButton(
            extra_frame,
            text="|◀  Vai a IN",
            command=self._go_to_in,
            height=SECONDARY_ACTION_HEIGHT,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            corner_radius=6,
        )
        go_in_button.grid(row=1, column=0, sticky="ew", padx=CARD_PADDING, pady=(0, 6))
        self._add_tooltip(go_in_button, "Sposta la posizione corrente sul punto IN - Home")

    def _build_action_buttons(self, row: int) -> None:
        action_frame = ctk.CTkFrame(self, fg_color="transparent", height=46)
        self._action_frame = action_frame
        action_frame.grid(row=row, column=0, sticky="ew", padx=OUTER_MARGIN, pady=(0, CARD_GAP))
        action_frame.grid_propagate(False)
        action_frame.grid_columnconfigure((0, 1), weight=1)

        confirm_button = ctk.CTkButton(
            action_frame,
            text="✓ Conferma clip",
            height=36,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            command=self._confirm,
            fg_color=PRIMARY_BUTTON_FG,
            hover_color=PRIMARY_BUTTON_HOVER,
            border_width=1,
            border_color=PRIMARY_BUTTON_BORDER,
            text_color=PRIMARY_BUTTON_TEXT,
            corner_radius=6,
        )
        confirm_button.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(3, 3))
        self._add_tooltip(confirm_button, "Salva i punti IN e OUT - Invio")

        cancel_button = ctk.CTkButton(
            action_frame,
            text="✕ Annulla",
            height=36,
            font=ctk.CTkFont(size=BUTTON_TEXT_SIZE, weight="bold"),
            command=self.on_cancel,
            fg_color=SECONDARY_BUTTON_FG,
            hover_color=SECONDARY_BUTTON_HOVER,
            border_width=1,
            border_color=SECONDARY_BUTTON_BORDER,
            text_color=SECONDARY_BUTTON_TEXT,
            corner_radius=6,
        )
        cancel_button.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(3, 3))
        self._add_tooltip(cancel_button, "Chiude senza salvare - Esc")

    def _zoom_in_ui(self) -> None:
        if self.waveform_widget is None:
            return
        self.waveform_widget.zoom_in(center_ms=self.current_ms)

    def _zoom_out_ui(self) -> None:
        if self.waveform_widget is None:
            return
        self.waveform_widget.zoom_out(center_ms=self.current_ms)

    def _zoom_fit_ui(self) -> None:
        if self.waveform_widget is None:
            return
        self.waveform_widget.fit_all()

    def _build_status_bar(self, row: int) -> None:
        status = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=STATUS_BG_COLOR,
            border_width=0,
            border_color=BORDER_COLOR,
            height=STATUS_BAR_HEIGHT,
        )
        self._status_frame = status
        status.grid(row=row, column=0, sticky="ew", padx=OUTER_MARGIN, pady=(0, OUTER_MARGIN))
        status.grid_propagate(False)
        status.grid_columnconfigure(0, weight=3)
        status.grid_columnconfigure(1, weight=4)

        self.status_audio_label = ctk.CTkLabel(
            status,
            text=self._build_audio_status_text(),
            font=ctk.CTkFont(size=8),
            anchor="w",
            text_color=TITLE_TEXT_COLOR,
        )
        self.status_audio_label.grid(row=0, column=0, sticky="w", padx=(8, 8), pady=(1, 1))

        self.status_help_label = ctk.CTkLabel(
            status,
            text="Suggerimento: Ctrl + rotellina = zoom | Rotellina = scorrimento",
            font=ctk.CTkFont(size=8),
            anchor="e",
            text_color=TITLE_TEXT_COLOR,
        )
        self.status_help_label.grid(row=0, column=1, sticky="e", padx=(8, 8), pady=(1, 1))

    def _build_audio_status_text(self) -> str:
        return "MP3 | 44.1 kHz n/d | Stereo n/d | 320 kbps n/d | Spazio: Play/Pausa | Frecce: spostamento"

    def _on_window_configure(self, _event: tk.Event) -> None:
        if self._file_name_after_id is not None:
            try:
                self.after_cancel(self._file_name_after_id)
            except Exception:
                pass
        self._file_name_after_id = self.after(120, self._update_file_name_display)

    def _update_file_name_display(self) -> None:
        self._file_name_after_id = None
        if not hasattr(self, "file_name_label"):
            return
        available = max(24, self.file_name_label.winfo_width() // 7)
        text = self._full_file_name
        if len(text) > available:
            text = text[:max(8, available - 1)] + "..."
        self.file_name_label.configure(text=text)
        self._update_tooltip(self.file_name_label, self._full_file_name)

    def _go_to_in(self) -> None:
        self._request_seek(self.in_ms, source="go-to-in")

    def _on_confirm_shortcut(self, _event: tk.Event) -> None:
        widget = self.focus_get()
        if isinstance(widget, ctk.CTkEntry) or isinstance(widget, tk.Entry):
            return
        self._confirm()

    def _on_cancel_shortcut(self, _event: tk.Event) -> None:
        self.on_cancel()

    def _apply_windows_titlebar_style(self) -> None:
        if tk.TkVersion <= 0 or not str(self.tk.call("tk", "windowingsystem")) == "win32":
            return

        try:
            hwnd = int(self.winfo_id())
        except Exception:
            return

        GWL_STYLE = -16
        WS_MINIMIZEBOX = 0x00020000
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_NOOWNERZORDER = 0x0200
        SWP_FRAMECHANGED = 0x0020

        user32 = ctypes.windll.user32
        set_style = getattr(user32, "SetWindowLongPtrW", None)
        get_style = getattr(user32, "GetWindowLongPtrW", None)

        if set_style is None or get_style is None:
            set_style = user32.SetWindowLongW
            get_style = user32.GetWindowLongW

        try:
            style = int(get_style(hwnd, GWL_STYLE))
            style_no_min = style & ~WS_MINIMIZEBOX
            if style_no_min != style:
                set_style(hwnd, GWL_STYLE, style_no_min)
                user32.SetWindowPos(
                    hwnd,
                    0,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
                )
        except Exception:
            pass

    def _add_tooltip(self, widget, text: str) -> None:
        self.tooltips.append(Tooltip(widget, text))

    def _update_tooltip(self, widget, text: str) -> None:
        for tip in self.tooltips:
            try:
                if getattr(tip, "widget", None) is widget:
                    tip.text = text
                    return
            except Exception:
                continue

    def _set_pause_button_state(self, is_playing: bool) -> None:
        if self.pause_button is None:
            return
        try:
            if is_playing:
                self.pause_button.configure(text="❚❚  Pausa")
                self._update_tooltip(self.pause_button, "Mette in pausa la riproduzione - Spazio")
            else:
                self.pause_button.configure(text="▶  Riprendi")
                self._update_tooltip(self.pause_button, "Avvia o riprende la riproduzione - Spazio")
        except Exception:
            pass

    def _initialize_audio(self) -> None:
        self.player = ClipPlayerVLC(self.mp3_path)

    def _center_on_parent(self) -> None:
        if self._start_maximized:
            return
        parent = self.parent
        if parent is None or not parent.winfo_exists():
            return

        parent.update_idletasks()
        self.update_idletasks()

        window_width = self.winfo_width()
        window_height = self.winfo_height()

        if window_width <= 1:
            window_width = self.winfo_reqwidth()
        if window_height <= 1:
            window_height = self.winfo_reqheight()

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()

        x = parent_x + (parent_width - window_width) // 2
        y = parent_y + (parent_height - window_height) // 2

        self.geometry(f"{window_width}x{window_height}+{x}+{y}")


    def _parse_field(self, entry: ctk.CTkEntry, current_value: int) -> int:
        try:
            value = _parse_timecode(entry.get())
        except ValueError:
            return current_value

        return max(0, min(value, self.total_duration_ms))

    def _normalize_time_fields(self) -> None:
        self.in_ms = self._parse_field(self.in_entry, self.in_ms)
        self.out_ms = self._parse_field(self.out_entry, self.out_ms)

        if self.out_ms <= self.in_ms:
            self.out_ms = min(self.total_duration_ms, self.in_ms + 1)

        self._update_time_fields()
        if self.waveform_widget is not None:
            self.waveform_widget.set_selection(self.in_ms, self.out_ms)

    def _on_time_entry_return(self, event: tk.Event) -> None:
        self._normalize_time_fields()
        self._refresh_ui()

    def _update_time_fields(self) -> None:
        self.in_entry.delete(0, tk.END)
        self.in_entry.insert(0, _format_timecode(self.in_ms))
        self.out_entry.delete(0, tk.END)
        self.out_entry.insert(0, _format_timecode(self.out_ms))
        self._refresh_clip_duration()

    def _refresh_clip_duration(self) -> None:
        duration_ms = max(0, self.out_ms - self.in_ms)
        self.clip_duration_label.configure(
            text=_format_timecode(duration_ms)
        )
        if hasattr(self, "clip_duration_panel_label") and self.clip_duration_panel_label is not None:
            try:
                self.clip_duration_panel_label.configure(text=_format_timecode(duration_ms))
            except Exception:
                pass
        if hasattr(self, "clip_range_label") and self.clip_range_label is not None:
            try:
                self.clip_range_label.configure(text=f"{_format_timecode(self.in_ms)} -> {_format_timecode(self.out_ms)}")
            except Exception:
                pass
        self._refresh_elapsed_from_in()

    def _refresh_position_label(self) -> None:
        self.current_position_label.configure(
            text=_format_timecode(self.current_ms)
        )
        self._refresh_elapsed_from_in()

    def _refresh_elapsed_from_in(self) -> None:
        if self.elapsed_from_in_value_label is None:
            return

        elapsed_ms = max(0, int(self.current_ms) - int(self.in_ms))
        self.elapsed_from_in_value_label.configure(
            text=_format_timecode(elapsed_ms)
        )

    def _set_in_from_current(self) -> None:
        self.in_ms = min(self.current_ms, self.total_duration_ms - 1)
        if self.out_ms <= self.in_ms:
            self.out_ms = min(self.total_duration_ms, self.in_ms + 1)
        self._update_time_fields()
        if self.waveform_widget is not None:
            self.waveform_widget.set_selection(self.in_ms, self.out_ms)

    def _set_out_from_current(self) -> None:
        self.out_ms = max(self.current_ms, self.in_ms + 1)
        self.out_ms = min(self.out_ms, self.total_duration_ms)
        self._update_time_fields()
        if self.waveform_widget is not None:
            self.waveform_widget.set_selection(self.in_ms, self.out_ms)

    def _move_current_position(self, delta_ms: int) -> None:
        base_ms = self.current_ms
        if self.play_state in ("playing", "preview_playing") and not self._seek_in_progress:
            base_ms = self._get_playback_position_ms()

        self._request_seek(base_ms + delta_ms, source="precision-buttons")

    def _on_slider_press(self, event: tk.Event) -> None:
        self._slider_dragging = True

    def _on_slider_release(self, event: tk.Event) -> None:
        self._slider_dragging = False
        if self.position_slider is None:
            return
        target_ms = int(round(self.position_slider.get() * 1000.0))
        self._request_seek(target_ms, source="slider-release")

    def _on_slider_move(self, value: float) -> None:
        if self._ignore_slider_change:
            return

        target_ms = self._normalize_seek_target(int(round(value * 1000.0)))
        self._apply_position_to_ui(target_ms, sync_slider=False)

        if not self._slider_dragging:
            self._request_seek(target_ms, source="slider-change")

    def _on_slider_move_event(self, event: tk.Event) -> None:
        if self._slider_dragging and self.position_slider is not None:
            value = self.position_slider.get()
            target_ms = self._normalize_seek_target(int(round(value * 1000.0)))
            self._apply_position_to_ui(target_ms, sync_slider=False)

    def _seek(self, new_ms: int, restart_if_playing: bool = False) -> None:
        self._request_seek(new_ms, source="legacy-seek")

    def _normalize_seek_target(self, target_ms: int, clamp_to_preview: bool = True) -> int:
        value = max(0, min(int(target_ms), self.total_duration_ms))
        if clamp_to_preview and self.previewing_clip and self.play_state in ("preview_playing", "preview_paused"):
            value = max(self.in_ms, min(value, self.preview_end_ms))
        return value

    def _apply_position_to_ui(self, position_ms: int, sync_slider: bool = True, clamp_to_preview: bool = True) -> None:
        self.current_ms = self._normalize_seek_target(position_ms, clamp_to_preview=clamp_to_preview)
        self._refresh_position_label()
        if self.waveform_widget is not None:
            self.waveform_widget.set_position(self.current_ms)
        if sync_slider and not self._slider_dragging:
            self._set_slider_position(self.current_ms / 1000.0)

    def _request_seek(self, target_ms: int, source: str) -> None:
        clamp_to_preview = source != "waveform"
        self._manual_waveform_seek = not clamp_to_preview
        target = self._normalize_seek_target(target_ms, clamp_to_preview=clamp_to_preview)
        self._apply_position_to_ui(target, clamp_to_preview=clamp_to_preview)

        if self.play_state == "stopped" or self.player is None:
            self._cancel_seek_tracking("stopped/no-player")
            return

        self._seek_in_progress = True
        self._seek_target_ms = target
        self._seek_started_at = time.monotonic()
        self._seek_source = source
        self.player.set_time_ms(target)
        self._schedule_seek_poll()

    def _schedule_seek_poll(self) -> None:
        if self._seek_poll_after_id is not None:
            return
        self._seek_poll_after_id = self.after(60, self._poll_seek_status)

    def _poll_seek_status(self) -> None:
        self._seek_poll_after_id = None
        if not self._seek_in_progress:
            return
        if self.player is None or self._seek_target_ms is None:
            self._cancel_seek_tracking("missing-player")
            return

        current_vlc_ms = self._get_playback_position_ms()
        delta = abs(current_vlc_ms - self._seek_target_ms)
        elapsed_ms = int((time.monotonic() - self._seek_started_at) * 1000)

        if delta <= self._seek_tolerance_ms:
            self._complete_seek(current_vlc_ms, elapsed_ms)
            return

        if elapsed_ms >= self._seek_timeout_ms:
            print(
                "[ClipEditor] seek timeout "
                f"source={self._seek_source} target={self._seek_target_ms} "
                f"vlc={current_vlc_ms} elapsed_ms={elapsed_ms}"
            )
            self._cancel_seek_tracking("timeout")
            return

        self._schedule_seek_poll()

    def _complete_seek(self, resolved_ms: int, elapsed_ms: int) -> None:
        print(
            "[ClipEditor] seek applied "
            f"source={self._seek_source} target={self._seek_target_ms} "
            f"vlc={resolved_ms} elapsed_ms={elapsed_ms}"
        )
        clamp_to_preview = not self._manual_waveform_seek
        self._seek_in_progress = False
        self._seek_target_ms = None
        self._seek_started_at = 0.0
        self._seek_source = ""
        self._apply_position_to_ui(resolved_ms, clamp_to_preview=clamp_to_preview)
        self._manual_waveform_seek = False

    def _cancel_seek_tracking(self, reason: str) -> None:
        if self._seek_in_progress:
            print(f"[ClipEditor] seek tracking ended: {reason}")
        self._seek_in_progress = False
        self._seek_target_ms = None
        self._seek_started_at = 0.0
        self._seek_source = ""
        self._manual_waveform_seek = False
        if self._seek_poll_after_id is not None:
            try:
                self.after_cancel(self._seek_poll_after_id)
            except Exception:
                pass
            self._seek_poll_after_id = None

    def _set_slider_position(self, seconds: float) -> None:
        if self.position_slider is None:
            return
        self._ignore_slider_change = True
        self.position_slider.set(seconds)
        if self.slider_after_id is not None:
            try:
                self.after_cancel(self.slider_after_id)
            except Exception:
                pass
        self.slider_after_id = self.after(50, self._clear_ignore_slider_flag)

    def _clear_ignore_slider_flag(self) -> None:
        self._ignore_slider_change = False

    def _get_playback_position_ms(self) -> int:
        if self.player is None:
            return self.current_ms

        return max(0, min(self.total_duration_ms, self.player.get_time_ms()))

    def _play(self) -> None:
        if self.player is None:
            return

        state = self.player.get_state()
        print("[ClipEditor] Play requested")
        print(f"[ClipEditor] VLC state before play: {state}")
        print(f"[ClipEditor] current position before play: {self.current_ms}")
        print(f"[ClipEditor] seek in progress: {self._seek_in_progress}")

        if self.play_state in ("paused", "preview_paused"):
            print("[ClipEditor] play method used: resume")
            self._resume_playback()
            return

        if self.play_state in ("playing", "preview_playing") and self.player.is_playing():
            print("[ClipEditor] play method used: already-playing")
            return

        try:
            if state in (vlc.State.Paused,):
                self.player.play()
                print("[ClipEditor] play method used: resume")
            elif state in (vlc.State.Playing,):
                print("[ClipEditor] play method used: already-playing")
            else:
                self._cancel_seek_tracking("play-request")
                self.player.play_from_ms(self.current_ms)
                print("[ClipEditor] play method used: play_from_ms")
        except RuntimeError as error:
            messagebox.showerror(
                "Errore riproduzione",
                f"Impossibile avviare la riproduzione:\n{error}"
            )
            return

        self._play_start_pending_until = time.monotonic() + 1.2
        self.play_state = "preview_playing" if self.previewing_clip else "playing"
        self._set_pause_button_state(True)

    def _pause(self) -> None:
        if self.player is None:
            return

        if self.play_state in ("playing", "preview_playing"):
            self.player.pause()
            self.current_ms = self._get_playback_position_ms()
            self.play_state = "preview_paused" if self.play_state == "preview_playing" else "paused"
            self._set_pause_button_state(False)
            self._refresh_ui()
            return

        if self.play_state in ("paused", "preview_paused"):
            self._resume_playback()
            return

        # Se lo stato è stopped, non fare nulla

    def _stop_audio(self) -> None:
        if self.player is None:
            return

        try:
            self.player.stop()
        except Exception:
            pass

    def _stop(self) -> None:
        self._cancel_seek_tracking("stop")
        self._play_start_pending_until = 0.0
        self._stop_audio()
        self.play_state = "stopped"
        self.previewing_clip = False
        self.current_ms = self.in_ms if self.in_ms < self.total_duration_ms else 0
        self._set_pause_button_state(True)
        self._refresh_ui()

    def _restart_playback(self) -> None:
        self._stop_audio()
        self._play()

    def _resume_playback(self) -> None:
        if self.player is None or self.play_state not in ("paused", "preview_paused"):
            return

        self.player.set_time_ms(self.current_ms)
        try:
            self.player.play()
        except RuntimeError as error:
            messagebox.showerror(
                "Errore riproduzione",
                f"Impossibile riprendere la riproduzione:\n{error}"
            )
            return

        self.play_state = "preview_playing" if self.play_state == "preview_paused" else "playing"
        self._play_start_pending_until = time.monotonic() + 1.2
        self._set_pause_button_state(True)

    def _preview_clip(self) -> None:
        try:
            self.in_ms = _parse_timecode(self.in_entry.get())
            self.out_ms = _parse_timecode(self.out_entry.get())
        except ValueError:
            messagebox.showerror(
                "Errore",
                "I campi IN e OUT devono essere formattati correttamente in HH:MM:SS.mmm oppure MM:SS.mmm."
            )
            self._update_time_fields()
            return

        if self.in_ms < 0 or self.out_ms > self.total_duration_ms or self.out_ms <= self.in_ms:
            messagebox.showerror(
                "Errore",
                "I valori IN e OUT devono essere validi e OUT deve essere maggiore di IN."
            )
            return

        self.preview_end_ms = self.out_ms
        self.current_ms = self.in_ms
        self.previewing_clip = True
        self._cancel_seek_tracking("preview-start")

        # Ensure any current playback is stopped before starting preview
        self._stop_audio()
        self._refresh_ui()

        # Log and check VLC state prior to starting preview (helps debugging Ended state)
        if self.player is not None:
            try:
                state = self.player.get_state()
                print(f"[ClipEditor] VLC state before preview: {state}")
            except Exception:
                pass

        try:
            if self.player is None:
                self._initialize_audio()
            # Use robust play_from_ms() to handle Ended/Stopped states reliably
            self.player.play_from_ms(self.in_ms)
        except RuntimeError as error:
            messagebox.showerror(
                "Errore riproduzione",
                f"Impossibile avviare la riproduzione della clip:\n{error}"
            )
            return

        self.play_state = "preview_playing"
        self._set_pause_button_state(True)
        if self.waveform_widget is not None:
            self.waveform_widget.set_position(self.current_ms)

    def _load_audio_for_position(self) -> bool:
        return self.player is not None

    def _schedule_update(self) -> None:
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except Exception:
                pass
        self._refresh_ui()
        self.after_id = self.after(100, self._schedule_update)

    def _refresh_ui(self) -> None:
        vlc_ms = None
        vlc_state = None
        is_playing_now = False
        if self.player is not None and self.play_state in ("playing", "preview_playing"):
            vlc_ms = self._get_playback_position_ms()
            vlc_state = self.player.get_state()
            is_playing_now = self.player.is_playing()

            if is_playing_now:
                self._play_start_pending_until = 0.0

            if self._seek_in_progress and self._seek_target_ms is not None:
                if abs(vlc_ms - self._seek_target_ms) <= self._seek_tolerance_ms:
                    self._complete_seek(vlc_ms, int((time.monotonic() - self._seek_started_at) * 1000))
            elif not self._slider_dragging:
                self.current_ms = vlc_ms

            effective_position = self._seek_target_ms if self._seek_in_progress and self._seek_target_ms is not None else self.current_ms

            if self.play_state == "preview_playing" and effective_position >= self.preview_end_ms:
                self._stop()
                self.current_ms = self.preview_end_ms
            elif self.play_state == "playing":
                start_pending = time.monotonic() < self._play_start_pending_until
                initializing = vlc_state in (vlc.State.Opening, vlc.State.Buffering)
                if (vlc_ms is not None and vlc_ms >= self.total_duration_ms) or (
                    not self._seek_in_progress and
                    not is_playing_now and
                    not start_pending and
                    not initializing
                ):
                    self._stop()
                    self.current_ms = min(self.current_ms, self.total_duration_ms)

        self._refresh_position_label()
        self._refresh_clip_duration()
        if self.waveform_widget is not None:
            follow_viewport = self.play_state in ("playing", "preview_playing") and not self._slider_dragging
            self.waveform_widget.set_position(self.current_ms, follow=follow_viewport)
        if not self._slider_dragging:
            self._set_slider_position(self.current_ms / 1000.0)

    def _confirm(self) -> None:
        try:
            self.in_ms = _parse_timecode(self.in_entry.get())
            self.out_ms = _parse_timecode(self.out_entry.get())
        except ValueError:
            messagebox.showerror(
                "Errore",
                "I campi IN e OUT devono essere formattati correttamente in HH:MM:SS.mmm oppure MM:SS.mmm."
            )
            return

        self.clip_info.use_custom_clip = True
        self.clip_info.clip_start_ms = max(0, min(self.in_ms, self.total_duration_ms - 1))
        self.clip_info.clip_end_ms = min(max(self.out_ms, self.clip_info.clip_start_ms + 1), self.total_duration_ms)

        try:
            self.clip_info.validate(self.total_duration_ms)
        except ValueError as error:
            messagebox.showerror(
                "Errore",
                str(error)
            )
            return

        self._cleanup_audio()
        self.callback(self.clip_info)
        self.destroy()

    def on_cancel(self) -> None:
        self._cleanup_audio()
        self.destroy()

    def _cleanup_audio(self) -> None:
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

        if self.slider_after_id is not None:
            try:
                self.after_cancel(self.slider_after_id)
            except Exception:
                pass
            self.slider_after_id = None

        self._cancel_seek_tracking("cleanup")

        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self.player.release()
            except Exception:
                pass
            self.player = None

    def destroy(self) -> None:
        self._cleanup_audio()
        super().destroy()
