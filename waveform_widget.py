# -*- coding: utf-8 -*-
"""
waveform_widget.py

Waveform professionale per Clip Editor:
- min/max per colonna
- zoom 1x..16x
- scroll orizzontale
- righello temporale
- marker IN/OUT e cursore posizione
"""

from __future__ import annotations

import threading
import time
import math
from pathlib import Path
from typing import Callable, Optional
import tkinter as tk

from waveform_analyzer import (
    ENABLE_PERFORMANCE_LOG,
    get_or_build_waveform,
    log_perf,
)

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None

MIN_ZOOM = 1.0
MAX_ZOOM = 200.0
ZOOM_STEP = 1.08


def is_waveform_result_obsolete(
    request_id: int,
    active_request_id: int,
    current_file: Path | None,
    result_file: Path,
    cancelled: bool,
    widget_exists: bool,
) -> bool:
    if cancelled or not widget_exists:
        return True
    if request_id != active_request_id:
        return True
    if current_file is None:
        return True

    try:
        return current_file.resolve() != result_file.resolve()
    except OSError:
        return True


class WaveformWidget(ctk.CTkFrame if ctk is not None else tk.Frame):
    def __init__(
        self,
        parent,
        height: int = 160,
        on_seek: Optional[Callable[[int], None]] = None,
        on_waveform_event: Optional[Callable[[str, dict], None]] = None
    ) -> None:
        super().__init__(parent)

        self.on_seek = on_seek
        self.on_waveform_event = on_waveform_event
        self.file_path: Optional[Path] = None
        self.duration_ms = 0
        self.loaded = False
        self._levels: dict[int, list[tuple[float, float]]] = {}

        self._canvas_height = max(100, int(height))
        self._pending_resize: Optional[str] = None
        self._pending_redraw: Optional[str] = None
        self._poll_after_id: Optional[str] = None
        self._load_thread: Optional[threading.Thread] = None
        self._cancelled = False
        self._pending_result: Optional[tuple[int, Path, dict]] = None
        self._waveform_request_id = 0
        self._active_waveform_request_id = 0
        self._loading_message_after_id: Optional[str] = None

        self._selection_in_ms = 0
        self._selection_out_ms = 0
        self._position_ms = 0

        self._zoom = MIN_ZOOM
        self._visible_start_ms = 0
        self._manual_scroll_until = 0.0
        self._auto_follow_margin = 0.12
        self._ruler_font = ("Segoe UI", 12)

        self._message_id: Optional[int] = None
        self._position_line_id: Optional[int] = None
        self._perf_render_pending = False

        self.configure(fg_color=self._bg_panel_color())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_controls()
        self._build_ruler()
        self._build_canvas()
        self._build_scrollbar()

        self._show_message("Analisi waveform...")

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------
    def _build_controls(self) -> None:
        frame_class = ctk.CTkFrame if ctk is not None else tk.Frame
        button_class = ctk.CTkButton if ctk is not None else tk.Button

        self.controls_frame = frame_class(self, fg_color="transparent" if ctk is not None else self._bg_panel_color())
        self.controls_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 4))
        self.controls_frame.grid_columnconfigure(3, weight=1)

        self.zoom_out_button = button_class(self.controls_frame, text="🔍−", width=96, command=self.zoom_out)
        self.zoom_out_button.grid(row=0, column=0, padx=(0, 6), sticky="w")

        self.zoom_in_button = button_class(self.controls_frame, text="🔍+", width=96, command=self.zoom_in)
        self.zoom_in_button.grid(row=0, column=1, padx=(0, 6), sticky="w")

        self.fit_all_button = button_class(self.controls_frame, text="Mostra tutto", width=110, command=self.fit_all)
        self.fit_all_button.grid(row=0, column=2, padx=(0, 6), sticky="w")

    def _build_ruler(self) -> None:
        self.ruler = tk.Canvas(
            self,
            height=26,
            highlightthickness=0,
            bg=self._bg_panel_color(),
        )
        self.ruler.grid(row=1, column=0, sticky="ew")

    def _build_canvas(self) -> None:
        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            bg=self._bg_wave_color(),
        )
        self.canvas.grid(row=2, column=0, sticky="nsew")

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        # Linux fallback
        self.canvas.bind("<Button-4>", self._on_linux_wheel_up)
        self.canvas.bind("<Button-5>", self._on_linux_wheel_down)

    def _build_scrollbar(self) -> None:
        if ctk is not None:
            self.h_scroll = ctk.CTkScrollbar(self, orientation="horizontal", command=self._on_scrollbar)
        else:
            self.h_scroll = tk.Scrollbar(self, orient="horizontal", command=self._on_scrollbar)
        self.h_scroll.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._update_scrollbar()

    # ------------------------------------------------------------------
    # Colors
    # ------------------------------------------------------------------
    def _appearance_mode(self) -> str:
        if ctk is None:
            return "Light"
        try:
            return ctk.get_appearance_mode()
        except Exception:
            return "Light"

    def _bg_panel_color(self) -> str:
        return "#f4f6f8" if self._appearance_mode() == "Light" else "#25282d"

    def _bg_wave_color(self) -> str:
        return "#fbfcfe" if self._appearance_mode() == "Light" else "#1d2024"

    def _wave_colors(self) -> tuple[str, str]:
        if self._appearance_mode() == "Light":
            return "#1f2c3a", "#7d8a97"
        return "#e0e8f0", "#6f7a84"

    def _grid_color(self) -> str:
        return "#d3dde7" if self._appearance_mode() == "Light" else "#3a4149"

    def _axis_color(self) -> str:
        return "#8fa1b3" if self._appearance_mode() == "Light" else "#5b6672"

    def _accent_color(self) -> str:
        return "#1f74d0" if self._appearance_mode() == "Light" else "#6db2ff"

    def _in_color(self) -> str:
        return "#0f8d4a" if self._appearance_mode() == "Light" else "#55c985"

    def _out_color(self) -> str:
        return "#d05731" if self._appearance_mode() == "Light" else "#ff9d7a"

    def _mask_color(self) -> str:
        return "#ebeff4" if self._appearance_mode() == "Light" else "#2b3036"

    def _text_color(self) -> str:
        return "#1a1e24" if self._appearance_mode() == "Light" else "#e5ecf4"

    # ------------------------------------------------------------------
    # Public API (compatible + new)
    # ------------------------------------------------------------------
    def clear(self, show_loading_message: bool = True) -> None:
        self._cancel_load()
        self._levels = {}
        self.loaded = False
        self._selection_in_ms = 0
        self._selection_out_ms = 0
        self._position_ms = 0
        self._zoom = MIN_ZOOM
        self._visible_start_ms = 0
        self.canvas.delete("all")
        self.ruler.delete("all")
        if show_loading_message:
            self._show_message("Analisi waveform...")
        self._update_zoom_label()
        self._update_scrollbar()

    def load_audio(self, file_path: str | Path, duration_ms: int) -> None:
        incoming_path = Path(file_path)
        if (
            self.file_path is not None
            and self.file_path == incoming_path
            and (
                self.loaded
                or (self._load_thread is not None and self._load_thread.is_alive())
            )
        ):
            return

        self.clear(show_loading_message=False)
        self.file_path = incoming_path
        self.duration_ms = max(0, int(duration_ms))
        self._cancelled = False

        if not self.file_path.is_file() or self.duration_ms <= 0:
            self._show_message("Waveform non disponibile")
            return

        self._waveform_request_id += 1
        request_id = self._waveform_request_id
        self._active_waveform_request_id = request_id

        self._schedule_loading_message(delay_ms=140)

        if self.on_waveform_event is not None:
            self.on_waveform_event(
                "waveform_analysis_started",
                {
                    "request_id": request_id,
                    "file_path": str(self.file_path),
                },
            )

        self._load_thread = threading.Thread(
            target=self._generate_waveform,
            args=(request_id, self.file_path, self.duration_ms),
            daemon=True,
        )
        self._load_thread.start()
        self._schedule_thread_poll()

    def set_position(self, position_ms: int, follow: bool = True) -> None:
        self._position_ms = max(0, min(int(position_ms), self.duration_ms))
        if follow and self._zoom > MIN_ZOOM:
            self._auto_follow_position()
        self._update_position_line()

    def set_selection(self, in_ms: int, out_ms: int) -> None:
        self._selection_in_ms = max(0, min(int(in_ms), self.duration_ms))
        self._selection_out_ms = max(self._selection_in_ms + 1, min(int(out_ms), self.duration_ms))
        if self.loaded:
            self._request_redraw()

    def zoom_in(self, center_ms: Optional[int] = None) -> None:
        self.set_zoom(self._zoom * ZOOM_STEP, center_ms=center_ms)

    def zoom_out(self, center_ms: Optional[int] = None) -> None:
        self.set_zoom(self._zoom / ZOOM_STEP, center_ms=center_ms)

    def fit_all(self) -> None:
        self.set_zoom(MIN_ZOOM)

    def set_zoom(self, level: float, center_ms: Optional[int] = None) -> None:
        level = max(MIN_ZOOM, min(MAX_ZOOM, float(level)))

        old_center = self._current_view_center_ms()
        self._zoom = level

        if center_ms is None:
            if self._position_ms > 0:
                center_ms = self._position_ms
            else:
                center_ms = old_center

        self._set_visible_window_center(center_ms)
        self._update_zoom_label()
        self._update_scrollbar()
        self._request_redraw()

    def get_zoom(self) -> float:
        return self._zoom

    def scroll_to_ms(self, position_ms: int) -> None:
        self._set_visible_window_center(int(position_ms))
        self._update_scrollbar()
        self._request_redraw()

    def get_visible_range(self) -> tuple[int, int]:
        end_ms = self._visible_start_ms + self._visible_window_ms()
        end_ms = min(self.duration_ms, end_ms)
        return int(self._visible_start_ms), int(end_ms)

    # ------------------------------------------------------------------
    # Loading and cache
    # ------------------------------------------------------------------
    def _cancel_load(self) -> None:
        if not hasattr(self, "_poll_after_id"):
            return
        self._cancelled = True
        self._active_waveform_request_id += 1
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        if self._loading_message_after_id is not None:
            try:
                self.after_cancel(self._loading_message_after_id)
            except Exception:
                pass
            self._loading_message_after_id = None
        if self._pending_resize is not None:
            try:
                self.after_cancel(self._pending_resize)
            except Exception:
                pass
            self._pending_resize = None
        if self._pending_redraw is not None:
            try:
                self.after_cancel(self._pending_redraw)
            except Exception:
                pass
            self._pending_redraw = None
        self._load_thread = None
        self._pending_result = None

    def _schedule_thread_poll(self) -> None:
        if self._poll_after_id is not None:
            return
        self._poll_after_id = self.after(60, self._poll_waveform_thread)

    def _schedule_loading_message(self, delay_ms: int = 120) -> None:
        if self._loading_message_after_id is not None:
            try:
                self.after_cancel(self._loading_message_after_id)
            except Exception:
                pass
        self._loading_message_after_id = self.after(delay_ms, self._show_loading_message)

    def _show_loading_message(self) -> None:
        self._loading_message_after_id = None
        if self.loaded:
            return
        self._show_message("Analisi waveform...")

    def _poll_waveform_thread(self) -> None:
        self._poll_after_id = None
        if self._cancelled:
            return

        if self._pending_result is not None:
            request_id, result_path, result = self._pending_result
            self._pending_result = None
            self._load_thread = None

            if self._is_result_obsolete(request_id, result_path):
                return

            if result.get("ok"):
                self._on_waveform_ready(result)
            else:
                self._on_waveform_error(result)
            return

        if self._load_thread is not None and self._load_thread.is_alive():
            self._schedule_thread_poll()

    def _generate_waveform(self, request_id: int, file_path: Path, duration_ms: int) -> None:
        if self._cancelled:
            return

        result = get_or_build_waveform(file_path=file_path, duration_ms=duration_ms)
        if self._cancelled:
            return
        self._pending_result = (request_id, file_path, result)

    def _is_result_obsolete(self, request_id: int, result_path: Path) -> bool:
        return is_waveform_result_obsolete(
            request_id=request_id,
            active_request_id=self._active_waveform_request_id,
            current_file=self.file_path,
            result_file=result_path,
            cancelled=self._cancelled,
            widget_exists=self.winfo_exists(),
        )

    def _on_waveform_ready(self, result: dict) -> None:
        if self._cancelled or not self.winfo_exists():
            return

        if self._loading_message_after_id is not None:
            try:
                self.after_cancel(self._loading_message_after_id)
            except Exception:
                pass
            self._loading_message_after_id = None

        levels = result.get("levels", {})
        self._levels = levels
        self.loaded = True
        self.canvas.delete("message")
        self._perf_render_pending = True
        self._update_zoom_label()
        self._update_scrollbar()

        timings = result.get("timings", {})
        for key, value in timings.items():
            if isinstance(value, (int, float)):
                log_perf(key, float(value))

        if self.on_waveform_event is not None:
            payload = {
                "cache_hit": bool(result.get("cache_hit", False)),
                "timings": timings,
            }
            self.on_waveform_event("waveform_ready", payload)

        self._request_redraw()

    def _on_waveform_error(self, result: Optional[dict] = None) -> None:
        if self._cancelled or not self.winfo_exists():
            return

        if self._loading_message_after_id is not None:
            try:
                self.after_cancel(self._loading_message_after_id)
            except Exception:
                pass
            self._loading_message_after_id = None

        error = None
        if isinstance(result, dict):
            error = result.get("error")
            timings = result.get("timings", {})
            for key, value in timings.items():
                if isinstance(value, (int, float)):
                    log_perf(key, float(value))
        else:
            timings = {}

        if ENABLE_PERFORMANCE_LOG and error:
            print(f"[ClipEditorPerf] waveform_error: {error}")

        self.loaded = False
        self.canvas.delete("all")
        self.ruler.delete("all")
        self._show_message("Waveform non disponibile")

        if self.on_waveform_event is not None:
            self.on_waveform_event(
                "waveform_error",
                {
                    "error": error,
                    "timings": timings,
                },
            )

    # ------------------------------------------------------------------
    # Draw and geometry
    # ------------------------------------------------------------------
    def _on_configure(self, event: tk.Event) -> None:
        self._canvas_height = max(100, int(event.height))
        if self._pending_resize is not None:
            self.after_cancel(self._pending_resize)
        self._pending_resize = self.after(120, self._handle_resize)

    def _handle_resize(self) -> None:
        self._pending_resize = None
        self._update_scrollbar()
        self._request_redraw()

    def _request_redraw(self) -> None:
        if self._pending_redraw is not None:
            return
        self._pending_redraw = self.after(20, self._draw_wave_scene)

    def _draw_wave_scene(self) -> None:
        self._pending_redraw = None
        render_start = time.perf_counter()
        self.canvas.configure(bg=self._bg_wave_color())
        self.ruler.configure(bg=self._bg_panel_color())

        if not self.loaded or not self._levels:
            return

        width = max(1, self.canvas.winfo_width())
        height = max(80, self.canvas.winfo_height())
        center_y = height / 2

        self.canvas.delete("grid")
        self.canvas.delete("selection")
        self.canvas.delete("waveform")
        self.canvas.delete("markers")
        self.canvas.delete("position")
        self.canvas.delete("text")
        self.canvas.delete("message")

        visible_start, visible_end = self.get_visible_range()

        self._draw_grid_and_ruler(width, height, visible_start, visible_end)
        self._draw_selection_masks(width, height, visible_start, visible_end)
        self._draw_wave(width, height, center_y, visible_start, visible_end)
        self._draw_markers(width, height, visible_start, visible_end)

        if self._position_line_id is None or not self.canvas.type(self._position_line_id):
            self._position_line_id = self.canvas.create_line(0, 0, 0, height, fill=self._accent_color(), width=2, tags=("position",))

        self._update_position_line()

        # z-order obbligatorio
        self.canvas.tag_lower("grid")
        self.canvas.tag_raise("selection")
        self.canvas.tag_raise("waveform")
        self.canvas.tag_raise("markers")
        self.canvas.tag_raise("position")
        self.canvas.tag_raise("text")

        if self._perf_render_pending:
            self._perf_render_pending = False
            log_perf("waveform_render", time.perf_counter() - render_start)

        line_count = len(self.canvas.find_withtag("waveform"))
        if ENABLE_PERFORMANCE_LOG:
            print(
                "[Waveform] draw stats "
                f"zoom={self._zoom} peaks={len(self._active_level())} "
                f"canvas_width={width} lines={line_count} "
                f"in_x={self._time_to_x(self._selection_in_ms, visible_start, visible_end, width)} "
                f"out_x={self._time_to_x(self._selection_out_ms, visible_start, visible_end, width)}"
            )

    def _draw_grid_and_ruler(self, width: int, height: int, visible_start: int, visible_end: int) -> None:
        self.ruler.delete("all")
        major_ms, minor_ms = self._tick_intervals(max(1, visible_end - visible_start), width)
        grid = self._grid_color()

        first_minor = (visible_start // minor_ms) * minor_ms
        if first_minor < visible_start:
            first_minor += minor_ms

        t = first_minor
        while t <= visible_end:
            x = self._time_to_x(t, visible_start, visible_end, width)
            is_major = (t % major_ms) == 0
            if is_major:
                self.canvas.create_line(x, 0, x, height, fill=grid, width=1, tags=("grid",))
                self.ruler.create_line(x, 14, x, 24, fill=grid, width=1)
                self.ruler.create_text(
                    x + 2,
                    7,
                    text=self._format_ruler_time(t, major_ms),
                    fill=self._text_color(),
                    anchor="w",
                    font=self._ruler_font,
                )
            else:
                self.canvas.create_line(x, 0, x, height, fill=grid, width=1, tags=("grid",))
                self.canvas.itemconfigure("grid", stipple="gray50")
                self.ruler.create_line(x, 18, x, 24, fill=grid, width=1)
            t += minor_ms

        # zero axis
        self.canvas.create_line(0, height / 2, width, height / 2, fill=self._axis_color(), width=1, tags=("grid",))

    def _draw_selection_masks(self, width: int, height: int, visible_start: int, visible_end: int) -> None:
        in_x = self._time_to_x(self._selection_in_ms, visible_start, visible_end, width)
        out_x = self._time_to_x(self._selection_out_ms, visible_start, visible_end, width)

        if in_x > 0:
            self.canvas.create_rectangle(0, 0, in_x, height, fill=self._mask_color(), outline="", tags=("selection",))
        if out_x < width:
            self.canvas.create_rectangle(out_x, 0, width, height, fill=self._mask_color(), outline="", tags=("selection",))

        # outline area selezionata (senza coprire waveform)
        if out_x > in_x:
            self.canvas.create_rectangle(in_x, 0, out_x, height, outline=self._accent_color(), width=1, tags=("selection",))

    def _draw_wave(self, width: int, height: int, center_y: float, visible_start: int, visible_end: int) -> None:
        peaks = self._active_level()
        if not peaks:
            return

        sample_count = len(peaks)
        duration = max(1, self.duration_ms)
        start_idx = math.floor((visible_start / duration) * sample_count)
        end_idx = math.ceil((visible_end / duration) * sample_count)
        start_idx = max(0, min(start_idx, sample_count - 1))
        end_idx = max(start_idx + 1, min(end_idx, sample_count))

        active_color, muted_color = self._wave_colors()
        span = max(1, end_idx - start_idx - 1)
        amp = max(1.0, (height * 0.45))

        for sample_index in range(start_idx, end_idx):
            x = int(((sample_index - start_idx) / span) * (width - 1))
            min_val, max_val = peaks[sample_index]
            top = center_y - (max_val * amp)
            bottom = center_y - (min_val * amp)
            t_ms = int((sample_index / max(1, sample_count - 1)) * self.duration_ms)
            color = active_color if self._selection_in_ms <= t_ms <= self._selection_out_ms else muted_color
            self.canvas.create_line(x, top, x, bottom, fill=color, width=1, tags=("waveform",))

    def _draw_markers(self, width: int, height: int, visible_start: int, visible_end: int) -> None:
        self._draw_marker(self._selection_in_ms, "IN", self._in_color(), width, height, visible_start, visible_end, left_edge=True)
        self._draw_marker(self._selection_out_ms, "OUT", self._out_color(), width, height, visible_start, visible_end, left_edge=False)

    def _draw_marker(
        self,
        time_ms: int,
        text: str,
        color: str,
        width: int,
        height: int,
        visible_start: int,
        visible_end: int,
        left_edge: bool,
    ) -> None:
        if time_ms < visible_start:
            x = 2
            if left_edge:
                self.canvas.create_polygon(2, 4, 2, 14, 9, 9, fill=color, outline="", tags=("markers",))
                self.canvas.create_text(12, 9, text=text, fill=color, anchor="w", font=("Segoe UI", 8, "bold"), tags=("text",))
            return
        if time_ms > visible_end:
            x = width - 2
            if not left_edge:
                self.canvas.create_polygon(width - 2, 4, width - 2, 14, width - 9, 9, fill=color, outline="", tags=("markers",))
                self.canvas.create_text(width - 12, 9, text=text, fill=color, anchor="e", font=("Segoe UI", 8, "bold"), tags=("text",))
            return

        x = self._time_to_x(time_ms, visible_start, visible_end, width)
        self.canvas.create_line(x, 0, x, height, fill=color, width=2, tags=("markers",))
        self.canvas.create_polygon(x - 6, 0, x + 6, 0, x, 8, fill=color, outline="", tags=("markers",))
        self.canvas.create_text(x + 8, 9, text=text, fill=color, anchor="w", font=("Segoe UI", 8, "bold"), tags=("text",))

    def _update_position_line(self) -> None:
        if self._position_line_id is None or not self.canvas.type(self._position_line_id):
            return

        width = max(1, self.canvas.winfo_width())
        height = max(80, self.canvas.winfo_height())
        visible_start, visible_end = self.get_visible_range()

        if self._position_ms < visible_start or self._position_ms > visible_end:
            self.canvas.itemconfigure(self._position_line_id, state="hidden")
            return

        x = self._time_to_x(self._position_ms, visible_start, visible_end, width)
        self.canvas.coords(self._position_line_id, x, 0, x, height)
        self.canvas.itemconfigure(self._position_line_id, state="normal")

    # ------------------------------------------------------------------
    # Time mapping, zoom and scrolling
    # ------------------------------------------------------------------
    def _visible_window_ms(self) -> int:
        if self.duration_ms <= 0:
            return 1
        return max(1, int(round(self.duration_ms / max(MIN_ZOOM, self._zoom))))

    def _max_visible_start(self) -> int:
        return max(0, self.duration_ms - self._visible_window_ms())

    def _set_visible_window_center(self, center_ms: int) -> None:
        half = self._visible_window_ms() // 2
        start = int(center_ms) - half
        start = max(0, min(start, self._max_visible_start()))
        self._visible_start_ms = start

    def _current_view_center_ms(self) -> int:
        return self._visible_start_ms + self._visible_window_ms() // 2

    def _time_to_x(self, time_ms: int, visible_start: int, visible_end: int, width: int) -> int:
        if visible_end <= visible_start:
            return 0
        ratio = (time_ms - visible_start) / float(visible_end - visible_start)
        ratio = max(0.0, min(1.0, ratio))
        return int(ratio * (width - 1))

    def _x_to_time(self, x: int, width: int) -> int:
        if width <= 1:
            return self._visible_start_ms
        visible_start, visible_end = self.get_visible_range()
        ratio = max(0.0, min(1.0, x / float(width - 1)))
        return int(visible_start + ratio * (visible_end - visible_start))

    def _active_level(self) -> list[tuple[float, float]]:
        peaks = self._levels.get(self._zoom)
        if peaks is not None:
            return peaks
        # fallback al livello più vicino
        if not self._levels:
            return []
        nearest = min(self._levels.keys(), key=lambda z: abs(float(z) - self._zoom))
        return self._levels[nearest]

    def _update_zoom_label(self) -> None:
        return

    def _update_scrollbar(self) -> None:
        if self.duration_ms <= 0 or self._zoom <= MIN_ZOOM + 1e-9:
            try:
                self.h_scroll.set(0.0, 1.0)
                self.h_scroll.grid_remove()
            except Exception:
                pass
            self._visible_start_ms = 0
            return

        self.h_scroll.grid()
        window_ms = self._visible_window_ms()
        max_start = self._max_visible_start()
        self._visible_start_ms = max(0, min(self._visible_start_ms, max_start))

        start_frac = self._visible_start_ms / float(max(1, self.duration_ms))
        end_frac = (self._visible_start_ms + window_ms) / float(max(1, self.duration_ms))
        end_frac = min(1.0, max(start_frac, end_frac))

        try:
            self.h_scroll.set(start_frac, end_frac)
        except Exception:
            pass

    def _on_scrollbar(self, *args) -> None:
        if self._zoom <= MIN_ZOOM + 1e-9:
            return

        self._manual_scroll_until = time.monotonic() + 0.8

        if not args:
            return

        cmd = args[0]
        max_start = self._max_visible_start()
        window_ms = self._visible_window_ms()

        if cmd == "moveto" and len(args) > 1:
            frac = max(0.0, min(1.0, float(args[1])))
            window_fraction = min(1.0, window_ms / float(max(1, self.duration_ms)))
            movable_fraction = max(1e-9, 1.0 - window_fraction)
            normalized = max(0.0, min(1.0, frac / movable_fraction))
            self._visible_start_ms = int(round(max_start * normalized))
        elif cmd == "scroll" and len(args) > 2:
            units = int(args[1])
            what = args[2]
            step = int(window_ms * (0.08 if what == "units" else 0.8))
            self._visible_start_ms += units * step

        self._visible_start_ms = max(0, min(self._visible_start_ms, max_start))
        self._update_scrollbar()
        self._request_redraw()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _on_click(self, event: tk.Event) -> None:
        if self.duration_ms <= 0 or self.on_seek is None:
            return

        width = max(1, self.canvas.winfo_width())
        position_ms = self._x_to_time(event.x, width)
        self.set_position(position_ms)
        try:
            self.on_seek(position_ms)
        except Exception:
            pass

    def _on_ctrl_mousewheel(self, event: tk.Event) -> None:
        width = max(1, self.canvas.winfo_width())
        if 0 <= int(event.x) < width:
            center_ms = self._x_to_time(event.x, width)
        else:
            center_ms = self._position_ms if self._position_ms > 0 else self._current_view_center_ms()

        if event.delta > 0:
            self.zoom_in(center_ms=center_ms)
        elif event.delta < 0:
            self.zoom_out(center_ms=center_ms)

    def _on_shift_mousewheel(self, event: tk.Event) -> None:
        self._scroll_from_wheel(event.delta)

    def _on_mousewheel(self, event: tk.Event) -> None:
        # Senza CTRL, la rotella resta dedicata allo scroll quando zoom > 1x
        if self._zoom > MIN_ZOOM + 1e-9:
            self._scroll_from_wheel(event.delta)

    def _on_linux_wheel_up(self, event: tk.Event) -> None:
        if (event.state & 0x0004) != 0:  # Ctrl
            width = max(1, self.canvas.winfo_width())
            if 0 <= int(event.x) < width:
                center_ms = self._x_to_time(event.x, width)
            else:
                center_ms = self._position_ms if self._position_ms > 0 else self._current_view_center_ms()
            self.zoom_in(center_ms=center_ms)
        elif self._zoom > MIN_ZOOM + 1e-9:
            self._scroll_from_wheel(120)

    def _on_linux_wheel_down(self, event: tk.Event) -> None:
        if (event.state & 0x0004) != 0:  # Ctrl
            width = max(1, self.canvas.winfo_width())
            if 0 <= int(event.x) < width:
                center_ms = self._x_to_time(event.x, width)
            else:
                center_ms = self._position_ms if self._position_ms > 0 else self._current_view_center_ms()
            self.zoom_out(center_ms=center_ms)
        elif self._zoom > MIN_ZOOM + 1e-9:
            self._scroll_from_wheel(-120)

    def _scroll_from_wheel(self, delta: int) -> None:
        if self._zoom <= MIN_ZOOM + 1e-9:
            return
        step = max(20, int(self._visible_window_ms() * 0.06))
        direction = -1 if delta > 0 else 1
        self._visible_start_ms += direction * step
        self._visible_start_ms = max(0, min(self._visible_start_ms, self._max_visible_start()))
        self._manual_scroll_until = time.monotonic() + 0.8
        self._update_scrollbar()
        self._request_redraw()

    def _auto_follow_position(self) -> None:
        if self._zoom <= MIN_ZOOM + 1e-9 or self.duration_ms <= 0:
            return
        if time.monotonic() < self._manual_scroll_until:
            return

        visible_start, visible_end = self.get_visible_range()
        visible_span = max(1, visible_end - visible_start)
        margin = int(visible_span * self._auto_follow_margin)

        # Segui solo vicino al bordo destro o quando fuori vista
        if self._position_ms > visible_end - margin:
            target_center = self._position_ms + margin
            self._set_visible_window_center(target_center)
            self._update_scrollbar()
            self._request_redraw()
        elif self._position_ms < visible_start:
            self._set_visible_window_center(self._position_ms)
            self._update_scrollbar()
            self._request_redraw()

    # ------------------------------------------------------------------
    # Ruler helpers
    # ------------------------------------------------------------------
    def _tick_intervals(self, visible_ms: int, width_px: int) -> tuple[int, int]:
        if width_px <= 0:
            return 1000, 200

        nice = [
            100, 200, 500,
            1000, 2000, 5000, 10000, 15000, 30000,
            60000, 120000, 300000,
        ]

        target_major = max(100, int(visible_ms / max(2, width_px // 120)))
        major = nice[-1]
        for value in nice:
            if value >= target_major:
                major = value
                break

        if major >= 60000:
            minor = major // 4
        elif major >= 5000:
            minor = major // 5
        else:
            minor = max(100, major // 5)

        return major, minor

    def _format_ruler_time(self, ms: int, major_step_ms: int) -> str:
        total_seconds = ms / 1000.0
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        millis = int(ms % 1000)

        if major_step_ms <= 500:
            if hours > 0:
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
            return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    # ------------------------------------------------------------------
    # Message and destroy
    # ------------------------------------------------------------------
    def _show_message(self, message: str) -> None:
        self.canvas.delete("message")
        width = max(10, self.canvas.winfo_width())
        height = max(50, self.canvas.winfo_height())
        self._message_id = self.canvas.create_text(
            width // 2,
            height // 2,
            text=message,
            fill=self._text_color(),
            font=("Segoe UI", 11),
            justify="center",
            tags=("message",),
        )

    def destroy(self) -> None:
        self._cancel_load()
        super().destroy()
