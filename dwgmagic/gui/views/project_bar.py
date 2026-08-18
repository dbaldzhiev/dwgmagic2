"""Zone A — the project bar.

Answers "what am I pointed at, and is the environment sane" in one strip:
project identity, what the folder contains, and a single health pill that
expands into the preflight detail. Collapsed by default, because
everything-is-fine is the common case and does not deserve a third of the
window the way the old sidebar gave it.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import Chip, Tooltip, elide_path

#: check key -> label shown in the expanded detail
CHECKS = {
    "autocad": "AutoCAD",
    "plugin": "Plugin (tectonica.dll)",
    "trusted": "Trusted path",
}


class ProjectBar(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        on_open: Callable[[], None],
        on_recent: Callable[[str], None],
        on_recheck: Callable[[], None],
        on_fix_trusted: Callable[[], None],
        on_reveal: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=0, fg_color=theme.color("surface.raised"), **kwargs)
        self._on_open = on_open
        self._on_recent = on_recent
        self._on_recheck = on_recheck
        self._on_fix_trusted = on_fix_trusted
        self._on_reveal = on_reveal

        self._project: Optional[Path] = None
        self._recent: List[str] = []
        self._checks: Dict[str, dict] = {}
        self._detail_open = False
        self._files_open = False
        self._file_groups: Dict[str, Sequence[str]] = {}

        self.grid_columnconfigure(1, weight=1)

        # --- Row 0: identity and actions -------------------------------
        identity = ctk.CTkFrame(self, fg_color="transparent")
        identity.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(10, 6))
        identity.grid_columnconfigure(1, weight=1)

        self.name_label = ctk.CTkLabel(
            identity, text="No project", font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
        )
        self.name_label.grid(row=0, column=0, sticky="w")

        self.path_label = ctk.CTkLabel(
            identity,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=theme.color("text.muted"),
            anchor="w",
            cursor="hand2",
        )
        self.path_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.path_label.bind("<Button-1>", lambda _e: self._on_reveal())
        self._path_tooltip = Tooltip(self.path_label, "")

        actions = ctk.CTkFrame(identity, fg_color="transparent")
        actions.grid(row=0, column=2, sticky="e")
        self.open_button = ctk.CTkButton(actions, text="Open…", width=76, command=self._on_open)
        self.open_button.pack(side="left", padx=(0, 6))
        self.recent_button = ctk.CTkButton(
            actions,
            text="Recent ▾",
            width=84,
            fg_color="transparent",
            border_width=1,
            border_color=theme.color("border"),
            text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=self._show_recent_menu,
        )
        self.recent_button.pack(side="left")

        # --- Row 1: content chips + health pill -------------------------
        strip = ctk.CTkFrame(self, fg_color="transparent")
        strip.grid(row=1, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 10))
        strip.grid_columnconfigure(0, weight=1)

        self.chip_row = ctk.CTkFrame(strip, fg_color="transparent")
        self.chip_row.grid(row=0, column=0, sticky="w")
        self._chips: List[Chip] = []

        self.health_pill = ctk.CTkButton(
            strip,
            text="Checking…",
            width=120,
            height=26,
            corner_radius=13,
            command=self._toggle_detail,
        )
        self.health_pill.grid(row=0, column=1, sticky="e")

        # --- Row 2: expandable file list --------------------------------
        self.files_frame = ctk.CTkFrame(self, fg_color=theme.color("surface.sunken"))
        self.files_text = ctk.CTkTextbox(
            self.files_frame, height=110, wrap="word", font=("Consolas", 11)
        )
        self.files_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.files_text.configure(state="disabled")

        # --- Row 3: expandable preflight detail -------------------------
        self.detail_frame = ctk.CTkFrame(self, fg_color=theme.color("surface.sunken"))
        self.detail_frame.grid_columnconfigure(1, weight=1)
        self._detail_labels: Dict[str, ctk.CTkLabel] = {}
        for index, (key, label) in enumerate(CHECKS.items()):
            ctk.CTkLabel(
                self.detail_frame,
                text=f"{label}:",
                font=ctk.CTkFont(size=11, weight="bold"),
                anchor="w",
            ).grid(row=index, column=0, sticky="w", padx=(12, 8), pady=2)
            value = ctk.CTkLabel(
                self.detail_frame, text="—", font=ctk.CTkFont(size=11), anchor="w", justify="left"
            )
            value.grid(row=index, column=1, sticky="w", pady=2)
            self._detail_labels[key] = value

        detail_actions = ctk.CTkFrame(self.detail_frame, fg_color="transparent")
        detail_actions.grid(row=len(CHECKS), column=0, columnspan=2, sticky="w", padx=12, pady=(4, 10))
        self.fix_button = ctk.CTkButton(
            detail_actions,
            text="Fix trusted path",
            width=130,
            height=26,
            fg_color=theme.color("warning"),
            hover_color=theme.color("warning.hover"),
            command=self._on_fix_trusted,
        )
        self.recheck_button = ctk.CTkButton(
            detail_actions,
            text="Re-check",
            width=90,
            height=26,
            fg_color="transparent",
            border_width=1,
            border_color=theme.color("border"),
            text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=self._on_recheck,
        )
        self.recheck_button.pack(side="left")

    # -- project identity -------------------------------------------------
    def set_project(self, project: Optional[Path]) -> None:
        self._project = project
        if project is None:
            self.name_label.configure(text="No project")
            self.path_label.configure(text="")
            self._path_tooltip.set_text("")
            self._set_chips([])
            return
        self.name_label.configure(text=project.name)
        self.path_label.configure(text=elide_path(project, 70))
        self._path_tooltip.set_text(f"{project}\n(click to open in Explorer)")

    def set_recent(self, recent: Sequence[str]) -> None:
        self._recent = list(recent)

    def _show_recent_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        if not self._recent:
            menu.add_command(label="(no recent projects)", state="disabled")
        for entry in self._recent:
            menu.add_command(
                label=elide_path(entry, 70), command=lambda p=entry: self._on_recent(p)
            )
        try:
            menu.tk_popup(
                self.recent_button.winfo_rootx(),
                self.recent_button.winfo_rooty() + self.recent_button.winfo_height(),
            )
        finally:
            menu.grab_release()

    # -- content chips ----------------------------------------------------
    def set_content(
        self,
        *,
        dwg: int,
        sheets: int,
        views: int,
        ignored: Sequence[str] = (),
        orphans: Sequence[str] = (),
        mode: str = "",
    ) -> None:
        """Show what the folder holds, from data the load already computed."""

        self._file_groups = {}
        chips: List[tuple[str, str]] = []
        if mode:
            chips.append((f"{mode}", "text.muted"))
        chips.append((f"{dwg} DWG", "text.muted"))
        chips.append((f"{sheets} sheets", "text.muted"))
        chips.append((f"{views} views", "text.muted"))
        if ignored:
            chips.append((f"⚠ {len(ignored)} ignored", "warning"))
            self._file_groups["Ignored (matched no naming convention)"] = list(ignored)
        if orphans:
            chips.append((f"⚠ {len(orphans)} orphan views", "warning"))
            self._file_groups["Views with no matching sheet"] = list(orphans)
        self._set_chips(chips)

    def _set_chips(self, chips: Sequence[tuple[str, str]]) -> None:
        for chip in self._chips:
            chip.destroy()
        self._chips = []
        for text, role in chips:
            chip = Chip(
                self.chip_row,
                text,
                role=role,
                command=self._toggle_files if self._file_groups else None,
            )
            chip.pack(side="left", padx=(0, 6))
            self._chips.append(chip)

    def _toggle_files(self) -> None:
        self._files_open = not self._files_open
        if self._files_open:
            body = []
            for title, names in self._file_groups.items():
                body.append(title)
                body.extend(f"    {name}" for name in names)
                body.append("")
            self.files_text.configure(state="normal")
            self.files_text.delete("1.0", tk.END)
            self.files_text.insert("1.0", "\n".join(body).rstrip())
            self.files_text.configure(state="disabled")
            self.files_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 10))
        else:
            self.files_frame.grid_forget()

    # -- health -----------------------------------------------------------
    def set_check(self, key: str, status: str, ok: Optional[bool], **extra) -> None:
        self._checks[key] = {"status": status, "ok": ok, **extra}
        label = self._detail_labels.get(key)
        if label is not None:
            label.configure(
                text=status,
                text_color=(
                    theme.color("text.muted")
                    if ok is None
                    else theme.color("success" if ok else "danger")
                ),
            )
        self._refresh_pill()

    def _refresh_pill(self) -> None:
        states = [entry.get("ok") for entry in self._checks.values()]
        if not states or any(state is None for state in states):
            text, role = "Checking…", "text.muted"
        elif all(states):
            text, role = "✓ Ready", "success"
        else:
            failures = sum(1 for state in states if state is False)
            blocked = self._checks.get("plugin", {}).get("ok") is False
            text = "✗ Blocked" if blocked else f"⚠ {failures} issue{'s' if failures > 1 else ''}"
            role = "danger" if blocked else "warning"
        self.health_pill.configure(
            text=text,
            fg_color=theme.color(role) if role != "text.muted" else theme.color("surface.sunken"),
            hover_color=theme.color(f"{role}.hover") if role in {"success", "warning", "danger"} else theme.color("surface.sunken"),
            text_color=theme.color("text.inverted") if role != "text.muted" else theme.color("text.muted"),
        )
        # The fix action only makes sense while the trusted check is fixable.
        trusted = self._checks.get("trusted", {})
        if trusted.get("fixable"):
            self.fix_button.pack(side="left", padx=(0, 8))
        else:
            self.fix_button.pack_forget()

    @property
    def is_healthy(self) -> bool:
        states = [entry.get("ok") for entry in self._checks.values()]
        return bool(states) and all(state is True for state in states)

    def expand_detail(self) -> None:
        if not self._detail_open:
            self._toggle_detail()

    def _toggle_detail(self) -> None:
        self._detail_open = not self._detail_open
        if self._detail_open:
            self.detail_frame.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 10))
        else:
            self.detail_frame.grid_forget()


__all__ = ["ProjectBar"]
