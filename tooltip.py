# -*- coding: utf-8 -*-
"""
tooltip.py

Classe tooltip riutilizzabile per Tkinter e CustomTkinter.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional


try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover
    ctk = None


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _on_enter(self, _event: tk.Event) -> None:
        self._schedule()

    def _on_leave(self, _event: tk.Event) -> None:
        self._unschedule()
        self._hide_tip()

    def _on_destroy(self, _event: tk.Event) -> None:
        self._hide_tip()
        self._after_id = None

    def _schedule(self) -> None:
        self._unschedule()

        if not self._widget_exists():
            return

        try:
            self._after_id = self.widget.after(self.delay, self._show_tip)
        except (tk.TclError, RuntimeError):
            self._after_id = None

    def _unschedule(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except (tk.TclError, RuntimeError):
                pass

            self._after_id = None

    def _show_tip(self) -> None:
        self._after_id = None
        if not self._widget_exists() or self._tip_window is not None:
            return

        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        except tk.TclError:
            return

        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)

        bg_color, fg_color = self._colors()

        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background=bg_color,
            foreground=fg_color,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 13),
            padx=8,
            pady=4,
            wraplength=300
        )
        label.pack(fill="both", expand=True)
        tw.update_idletasks()

        try:
            screen_w = tw.winfo_screenwidth()
            screen_h = tw.winfo_screenheight()
            tip_w = tw.winfo_reqwidth()
            tip_h = tw.winfo_reqheight()

            x = min(max(0, x), max(0, screen_w - tip_w))
            y = min(max(0, y), max(0, screen_h - tip_h))
        except tk.TclError:
            pass

        tw.geometry(f"+{x}+{y}")

    def _hide_tip(self) -> None:
        if self._tip_window is not None:
            try:
                self._tip_window.destroy()
            except tk.TclError:
                pass

            self._tip_window = None

        self._unschedule()

    def destroy(self) -> None:
        self._hide_tip()

    def _widget_exists(self) -> bool:
        try:
            return bool(self.widget.winfo_exists())
        except tk.TclError:
            return False

    def _colors(self) -> tuple[str, str]:
        if ctk is not None:
            try:
                mode = ctk.get_appearance_mode()
            except Exception:
                mode = None
        else:
            mode = None

        if mode == "Light":
            return "#f8f8f8", "#202020"

        return "#2b2b2b", "#f5f5f5"
