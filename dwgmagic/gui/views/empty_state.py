"""Zone G — what the window shows before a project is open.

Previously this was an empty stage table above an empty task tree, which says
nothing about what the app wants from you. A drop target does.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import elide_path


class EmptyState(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        on_open: Callable[[], None],
        on_recent: Callable[[str], None],
        dnd_available: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_recent = on_recent
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(
            self,
            fg_color=theme.color("surface.raised"),
            border_width=2,
            border_color=theme.color("border"),
            corner_radius=12,
        )
        hero.grid(row=1, column=0, padx=60, pady=20, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hero, text="📁", font=ctk.CTkFont(size=44)).grid(row=0, column=0, pady=(28, 4))
        ctk.CTkLabel(
            hero,
            text=(
                "Drop a project folder here"
                if dnd_available
                else "Open a project folder to begin"
            ),
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=1, column=0)
        ctk.CTkLabel(
            hero,
            text="A folder of Revit-exported DWGs, an originals/ folder, or an original.zip",
            font=ctk.CTkFont(size=11),
            text_color=theme.color("text.muted"),
        ).grid(row=2, column=0, pady=(4, 14))
        ctk.CTkButton(
            hero, text="Open Project…", width=160, height=36, command=on_open
        ).grid(row=3, column=0, pady=(0, 20))

        self.recent_frame = ctk.CTkFrame(hero, fg_color="transparent")
        self.recent_frame.grid(row=4, column=0, pady=(0, 20))

    def set_recent(self, recent: Sequence[str]) -> None:
        for child in self.recent_frame.winfo_children():
            child.destroy()
        if not recent:
            return
        ctk.CTkLabel(
            self.recent_frame,
            text="Recent",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=theme.color("text.muted"),
        ).pack(pady=(0, 4))
        for entry in list(recent)[:5]:
            ctk.CTkButton(
                self.recent_frame,
                text=elide_path(Path(entry).name + "   —   " + elide_path(entry, 48), 70),
                command=lambda p=entry: self._on_recent(p),
                height=26,
                fg_color="transparent",
                text_color=theme.color("accent"),
                hover_color=theme.color("surface.sunken"),
                font=ctk.CTkFont(size=11),
            ).pack(fill="x", padx=20)


__all__ = ["EmptyState"]
