"""Small shared widgets and helpers used across the GUI views."""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from dwgmagic.gui import theme


def elide_path(path: Path | str, limit: int = 64) -> str:
    """Shorten a path in the middle, keeping the drive and the leaf readable."""

    text = str(path)
    if len(text) <= limit:
        return text
    parts = Path(text).parts
    if len(parts) <= 2:
        return text[: limit - 1] + "…"
    head, tail = parts[0], parts[-1]
    middle = "…"
    candidate = os.path.join(head, middle, tail)
    # Grow back towards the front while it still fits.
    for index in range(len(parts) - 2, 0, -1):
        longer = os.path.join(head, middle, *parts[index:])
        if len(longer) > limit:
            break
        candidate = longer
    return candidate


def open_path(path: Path) -> None:
    """Open a file or folder with the shell's default handler."""

    try:
        os.startfile(str(path))  # noqa: S606 - Windows shell open, user-initiated
    except (OSError, AttributeError):
        pass


def reveal_path(path: Path) -> None:
    """Show a file in Explorer with it selected, or open the folder itself."""

    try:
        if path.is_dir():
            open_path(path)
            return
        subprocess.Popen(  # noqa: S603 - fixed argv
            ["explorer", "/select,", str(path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError):
        open_path(path.parent)


def copy_to_clipboard(widget: tk.Misc, text: str) -> None:
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except tk.TclError:  # pragma: no cover - clipboard can be locked
        pass


class Tooltip:
    """Minimal hover tooltip; Tk has none built in."""

    def __init__(self, widget: tk.Misc, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self._window: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _show(self, _event=None) -> None:
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            window = tk.Toplevel(self.widget)
            window.wm_overrideredirect(True)
            window.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                window,
                text=self.text,
                justify="left",
                background=theme.color("surface.raised"),
                foreground=theme.color("text"),
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 9),
                padx=6,
                pady=3,
            )
            label.pack()
            self._window = window
        except tk.TclError:  # pragma: no cover
            self._window = None

    def _hide(self, _event=None) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:  # pragma: no cover
                pass
            self._window = None


class Chip(ctk.CTkButton):
    """A compact, low-emphasis pill used for counts and filters."""

    def __init__(
        self,
        master,
        text: str,
        *,
        command: Optional[Callable[[], None]] = None,
        role: str = "text.muted",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            height=24,
            # CTkButton defaults to 140px; a chip must hug its label.
            width=max(52, 8 * len(text) + 20),
            corner_radius=12,
            border_width=1,
            fg_color="transparent",
            border_color=theme.color("border"),
            text_color=theme.color(role),
            hover_color=theme.color("surface.sunken"),
            font=ctk.CTkFont(size=11),
            **kwargs,
        )
        self._role = role

    def restyle(self, *, role: Optional[str] = None, active: bool = False) -> None:
        if role is not None:
            self._role = role
        self.configure(
            border_color=theme.color("accent") if active else theme.color("border"),
            text_color=theme.color("text") if active else theme.color(self._role),
            fg_color=theme.color("surface.sunken") if active else "transparent",
            hover_color=theme.color("surface.sunken"),
        )


__all__ = [
    "Chip",
    "Tooltip",
    "copy_to_clipboard",
    "elide_path",
    "open_path",
    "reveal_path",
]
