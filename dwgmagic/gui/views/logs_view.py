"""Zone E — the log pane.

Adds the level filter, search and autoscroll the old pane lacked, and a hard
cap on the widget itself. Previously ``_MAX_TASK_LOG_LINES`` trimmed only the
backing list, so every AutoCAD console line from every job accumulated in Tk's
text buffer for the life of the session.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import copy_to_clipboard, open_path

#: Hard ceiling on lines held in the widget *and* the backing buffer.
MAX_LINES = 5000

_LEVELS = ("all", "info", "warning", "error")
_ORDER = {"info": 0, "warning": 1, "error": 2}


class LogsView(ctk.CTkFrame):
    def __init__(self, master, *, run_log_resolver: Callable[[], Optional[Path]], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._run_log_resolver = run_log_resolver
        self._entries: List[Tuple[str, str]] = []
        self._level = "all"
        self._query = ""
        self._autoscroll = True

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.grid_columnconfigure(2, weight=1)

        self.level_menu = ctk.CTkOptionMenu(
            bar, values=[level.capitalize() for level in _LEVELS], width=110,
            command=self._on_level,
        )
        self.level_menu.set("All")
        self.level_menu.grid(row=0, column=0, padx=(0, 8))

        self.search = ctk.CTkEntry(bar, placeholder_text="Search…", width=220)
        self.search.grid(row=0, column=1, padx=(0, 8))
        self.search.bind("<KeyRelease>", self._on_search)

        self.autoscroll_box = ctk.CTkCheckBox(
            bar, text="Autoscroll", command=self._on_autoscroll, width=90
        )
        self.autoscroll_box.select()
        self.autoscroll_box.grid(row=0, column=3, padx=(0, 8))

        ctk.CTkButton(
            bar, text="Open run.log", width=110, height=28,
            fg_color="transparent", border_width=1,
            border_color=theme.color("border"), text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=self._open_run_log,
        ).grid(row=0, column=4, padx=(0, 6))
        ctk.CTkButton(
            bar, text="Copy", width=70, height=28,
            fg_color="transparent", border_width=1,
            border_color=theme.color("border"), text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=self._copy,
        ).grid(row=0, column=5)

        self.text = ctk.CTkTextbox(self, wrap="word", font=("Consolas", 11))
        self.text.grid(row=1, column=0, sticky="nsew")
        for level, role in (("warning", "warning"), ("error", "danger")):
            try:
                self.text.tag_config(level, foreground=theme.color(role))
            except Exception:  # noqa: BLE001 - depends on ctk internals
                pass
        self.text.configure(state="disabled")

    # ----------------------------------------------------------------------
    def append(self, message: str, level: str = "info") -> None:
        level = level if level in _ORDER else "info"
        self._entries.append((level, message))
        if len(self._entries) > MAX_LINES:
            del self._entries[: len(self._entries) - MAX_LINES]
            self._rerender()
            return
        if self._passes(level, message):
            self._write(level, message)

    def _passes(self, level: str, message: str) -> bool:
        if self._level != "all" and _ORDER[level] < _ORDER[self._level]:
            return False
        return self._query in message.lower() if self._query else True

    def _write(self, level: str, message: str) -> None:
        self.text.configure(state="normal")
        try:
            self.text.insert(tk.END, message + "\n", () if level == "info" else (level,))
        except Exception:  # noqa: BLE001 - tag support differences
            self.text.insert(tk.END, message + "\n")
        # Trim the widget, not just the buffer.
        try:
            excess = int(self.text.index("end-1c").split(".")[0]) - MAX_LINES
            if excess > 0:
                self.text.delete("1.0", f"{excess + 1}.0")
        except (tk.TclError, ValueError):  # pragma: no cover
            pass
        if self._autoscroll:
            self.text.see(tk.END)
        self.text.configure(state="disabled")

    def _rerender(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.configure(state="disabled")
        for level, message in self._entries:
            if self._passes(level, message):
                self._write(level, message)

    # ----------------------------------------------------------------------
    def _on_level(self, value: str) -> None:
        self._level = value.lower()
        self._rerender()

    def _on_search(self, _event=None) -> None:
        self._query = self.search.get().strip().lower()
        self._rerender()

    def _on_autoscroll(self) -> None:
        self._autoscroll = bool(self.autoscroll_box.get())

    def _open_run_log(self) -> None:
        path = self._run_log_resolver()
        if path is not None and path.exists():
            open_path(path)

    def _copy(self) -> None:
        copy_to_clipboard(
            self, "\n".join(msg for lvl, msg in self._entries if self._passes(lvl, msg))
        )


__all__ = ["LogsView", "MAX_LINES"]
