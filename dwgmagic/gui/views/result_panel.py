"""Zone D — the run result, with the artefacts reachable.

Replaces the old end-of-run modal. That modal was unresizable and uncopyable,
and whether to show it at all was decided by string-sniffing the formatted
summary (``line.startswith(("  ✗", "Stage")) or "FAILED" in line``) — so any
wording change silently broke failure detection. ``build_manifest`` already
returns exactly this information as structured data, so that is what drives
this panel.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import copy_to_clipboard, open_path, reveal_path


def _format_size(size: Optional[int]) -> str:
    if size is None:
        return "missing"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


class ResultPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        on_show_job: Callable[[str], None],
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=theme.color("surface.raised"), **kwargs)
        self._on_show_job = on_show_job
        self._manifest: Dict[str, Any] = {}
        self._manifest_path: Optional[Path] = None
        self._logs_dir: Optional[Path] = None
        self.grid_columnconfigure(0, weight=1)

        self.headline = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=15, weight="bold"), anchor="w"
        )
        self.headline.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        self.rows = ctk.CTkFrame(self, fg_color="transparent")
        self.rows.grid(row=1, column=0, sticky="ew", padx=14)
        self.rows.grid_columnconfigure(0, weight=1)

        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=2, column=0, sticky="w", padx=14, pady=(8, 12))

    # ----------------------------------------------------------------------
    def clear(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        for child in self.actions.winfo_children():
            child.destroy()
        self.headline.configure(text="")

    def show(
        self,
        manifest: Dict[str, Any],
        *,
        elapsed: Optional[float] = None,
        cancelled: bool = False,
        manifest_path: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
    ) -> None:
        self.clear()
        self._manifest = manifest
        self._manifest_path = manifest_path
        self._logs_dir = logs_dir

        jobs = manifest.get("jobs") or []
        failed_jobs = [job for job in jobs if not job.get("succeeded")]
        succeeded = bool(manifest.get("succeeded")) and not cancelled

        if cancelled:
            self.headline.configure(
                text="■  Run cancelled", text_color=theme.color("warning")
            )
        elif succeeded:
            duration = f" in {int(elapsed) // 60}:{int(elapsed) % 60:02d}" if elapsed else ""
            self.headline.configure(
                text=f"✓  Run completed{duration}", text_color=theme.color("success")
            )
        else:
            if failed_jobs:
                text = f"✗  {len(failed_jobs)} of {len(jobs)} jobs failed"
            else:
                stage = next(
                    (s for s in manifest.get("stages") or [] if not s.get("succeeded")), None
                )
                text = f"✗  Stage '{stage['name']}' failed" if stage else "✗  Run failed"
            self.headline.configure(text=text, text_color=theme.color("danger"))

        if succeeded or not failed_jobs:
            self._render_deliverables(manifest)
        if failed_jobs:
            self._render_failures(failed_jobs)

        self._render_actions(manifest)

    # ----------------------------------------------------------------------
    def _render_deliverables(self, manifest: Dict[str, Any]) -> None:
        for entry in manifest.get("deliverables") or []:
            path = Path(str(entry.get("path")))
            exists = bool(entry.get("exists"))
            row = ctk.CTkFrame(self.rows, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row,
                text=f"{'✓' if exists else '✗'}  {path.name}",
                font=ctk.CTkFont(size=12),
                text_color=theme.color("success" if exists else "danger"),
                anchor="w",
                width=280,
            ).pack(side="left")
            ctk.CTkLabel(
                row,
                text=_format_size(entry.get("size_bytes")),
                font=ctk.CTkFont(size=11),
                text_color=theme.color("text.muted"),
                width=90,
                anchor="w",
            ).pack(side="left")
            if exists:
                self._small_button(row, "Open", lambda p=path: open_path(p)).pack(side="left", padx=4)
                self._small_button(
                    row, "Show in folder", lambda p=path: reveal_path(p)
                ).pack(side="left")

    def _render_failures(self, failed_jobs) -> None:
        for job in failed_jobs[:12]:
            name = str(job.get("name"))
            reason = job.get("failure_reason") or f"exit code {job.get('returncode')}"
            row = ctk.CTkFrame(self.rows, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row,
                text=f"✗  {name}",
                font=ctk.CTkFont(size=12),
                text_color=theme.color("danger"),
                anchor="w",
                width=280,
            ).pack(side="left")
            ctk.CTkLabel(
                row,
                text=str(reason)[:80],
                font=ctk.CTkFont(size=11),
                text_color=theme.color("text.muted"),
                anchor="w",
            ).pack(side="left", padx=(0, 8))
            self._small_button(
                row, "Show output", lambda n=name: self._on_show_job(n)
            ).pack(side="right")
        if len(failed_jobs) > 12:
            ctk.CTkLabel(
                self.rows,
                text=f"…and {len(failed_jobs) - 12} more (see the work view)",
                font=ctk.CTkFont(size=11),
                text_color=theme.color("text.muted"),
                anchor="w",
            ).pack(fill="x", pady=2)

    def _render_actions(self, manifest: Dict[str, Any]) -> None:
        if self._logs_dir is not None:
            self._small_button(
                self.actions, "Open logs folder", lambda: open_path(self._logs_dir)
            ).pack(side="left", padx=(0, 6))
        if self._manifest_path is not None and self._manifest_path.exists():
            self._small_button(
                self.actions, "Open manifest", lambda: open_path(self._manifest_path)
            ).pack(side="left", padx=(0, 6))
        self._small_button(
            self.actions, "Copy report", lambda: self._copy_report(manifest)
        ).pack(side="left")

    def _small_button(self, master, text: str, command: Callable[[], None]) -> ctk.CTkButton:
        return ctk.CTkButton(
            master,
            text=text,
            command=command,
            height=26,
            width=max(70, 9 * len(text)),
            fg_color="transparent",
            border_width=1,
            border_color=theme.color("border"),
            text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            font=ctk.CTkFont(size=11),
        )

    def _copy_report(self, manifest: Dict[str, Any]) -> None:
        copy_to_clipboard(self, json.dumps(manifest, indent=2, ensure_ascii=False, default=str))


__all__ = ["ResultPanel"]
