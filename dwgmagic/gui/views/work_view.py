"""Zone C — the single work hierarchy.

Replaces the old stage table plus task tree. Those competed: two hierarchies
for one run, and the task tree was rooted at *Merge* with sheets nested under
it, which reads as "merge contains the sheets" — the inverse of the real
dependency. Batch structure, which actually governs execution order, was
invisible entirely.

Here stages are the spine, the AutoCAD stage owns its batches, and each batch
owns its jobs.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import Chip, copy_to_clipboard, open_path

_MAX_OUTPUT_LINES = 2000

_GLYPHS = {
    "pending": "·",
    "queued": "•",
    "running": "▶",
    "completed": "✓",
    "failed": "✗",
    "skipped": "–",
}

_BATCH_TITLES = {"views": "Views", "sheets": "Sheets", "merge": "Merge"}


class WorkView(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        job_log_resolver: Callable[[str], Optional[Path]],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._job_log_resolver = job_log_resolver

        self._output: Dict[str, List[str]] = {}
        self._info: Dict[str, dict] = {}
        self._selected: Optional[str] = None
        self._filter = "all"
        self._hidden: Dict[str, tuple] = {}
        self._autocad_node: Optional[str] = None
        self._batch_counts: Dict[str, list] = {}

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- filter chips ------------------------------------------------
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self._filter_chips: Dict[str, Chip] = {}
        for key, label in (("all", "All"), ("running", "Running"), ("failed", "Failed")):
            chip = Chip(chips, label, command=lambda k=key: self.set_filter(k))
            chip.pack(side="left", padx=(0, 6))
            self._filter_chips[key] = chip
        self._refresh_chips()

        # --- tree ----------------------------------------------------------
        paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, sashwidth=5, borderwidth=0,
            bg=theme.color("surface"),
        )
        paned.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self._paned = paned

        tree_frame = ctk.CTkFrame(paned, fg_color=theme.color("surface.raised"))
        paned.add(tree_frame, minsize=360, width=540)
        # PanedWindow ignores the requested width until it is mapped.
        self.after(120, lambda: self._place_sash(540))

        self.style = ttk.Style()
        try:
            self.style.theme_use("default")
        except tk.TclError:  # pragma: no cover
            pass

        self.tree = ttk.Treeview(
            tree_frame, columns=("status", "info", "time"), show="tree headings", selectmode="browse"
        )
        self.tree.heading("#0", text="Stage / job")
        self.tree.heading("status", text="Status")
        self.tree.heading("info", text="Detail")
        self.tree.heading("time", text="Time")
        self.tree.column("#0", minwidth=180, width=240, stretch=True)
        self.tree.column("status", width=96, anchor="w", stretch=False)
        self.tree.column("info", width=110, anchor="w", stretch=False)
        self.tree.column("time", width=62, anchor="e", stretch=False)

        scroll = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # --- detail --------------------------------------------------------
        detail = ctk.CTkFrame(paned, fg_color=theme.color("surface.raised"))
        paned.add(detail, minsize=260)

        head = ctk.CTkFrame(detail, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.detail_title = ctk.CTkLabel(
            head, text="—", font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        )
        self.detail_title.pack(side="left")
        self.detail_status = ctk.CTkLabel(
            head, text="", font=ctk.CTkFont(size=11), text_color=theme.color("text.muted")
        )
        self.detail_status.pack(side="left", padx=(10, 0))

        self.detail_actions = ctk.CTkFrame(detail, fg_color="transparent")
        self.detail_actions.pack(fill="x", padx=10)
        self.open_log_button = ctk.CTkButton(
            self.detail_actions, text="Open job log", width=110, height=26,
            command=self._open_job_log,
        )
        self.copy_button = ctk.CTkButton(
            self.detail_actions, text="Copy output", width=110, height=26,
            fg_color="transparent", border_width=1,
            border_color=theme.color("border"), text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=self._copy_output,
        )

        self.output = ctk.CTkTextbox(detail, wrap="none", font=("Consolas", 11))
        self.output.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.output.configure(state="disabled")

        self.apply_theme()

    def _place_sash(self, x: int) -> None:
        try:
            if self._paned.winfo_width() > x + 200:
                self._paned.sash_place(0, x, 0)
        except tk.TclError:  # pragma: no cover - not yet mapped
            pass

    # -- theming ----------------------------------------------------------
    def apply_theme(self) -> None:
        self.style.configure(
            "Treeview",
            background=theme.color("tree.background"),
            foreground=theme.color("tree.foreground"),
            fieldbackground=theme.color("tree.background"),
            borderwidth=0,
            rowheight=26,
            font=("Segoe UI", 10),
        )
        self.style.map("Treeview", background=[("selected", theme.color("tree.selected"))])
        self.style.configure(
            "Treeview.Heading",
            background=theme.color("tree.heading"),
            foreground=theme.color("tree.foreground"),
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padding=(6, 4),
        )
        for status in theme.STATUS_KEYS:
            self.tree.tag_configure(f"status:{status}", foreground=theme.status_color(status))
        try:
            self._paned.configure(bg=theme.color("surface"))
        except tk.TclError:  # pragma: no cover
            pass

    # -- structure --------------------------------------------------------
    def reset(self, stage_names: Sequence[str]) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._output.clear()
        self._info.clear()
        self._hidden.clear()
        self._batch_counts.clear()
        self._autocad_node = None
        self._selected = None
        self._show_output(None)

        for name in stage_names:
            node = f"stage:{name}"
            self.tree.insert(
                "", tk.END, iid=node, text=self._humanise(name),
                values=("· Pending", "", ""), tags=("status:pending",),
            )
            self._info[node] = {"title": self._humanise(name), "status": "pending", "kind": "stage"}
            if name == "autocad":
                self._autocad_node = node

    def set_plan(self, batches: Sequence[dict]) -> None:
        """Create batch and job nodes from the up-front plan."""

        parent = self._autocad_node
        if parent is None:
            return
        for batch in batches:
            label = str(batch.get("label") or "")
            jobs = list(batch.get("jobs") or [])
            if not jobs:
                continue
            node = f"batch:{label}"
            title = _BATCH_TITLES.get(label, label.title())
            if not self.tree.exists(node):
                self.tree.insert(
                    parent, tk.END, iid=node, text=title,
                    values=("· Pending", f"0/{len(jobs)}", ""), tags=("status:pending",),
                )
            self._info[node] = {
                "title": title, "status": "pending", "kind": "batch", "label": label
            }
            self._batch_counts[label] = [0, len(jobs)]
            for job_name in jobs:
                self._ensure_job(job_name, node)
        self.tree.item(parent, open=True)

    def _ensure_job(self, job_name: str, parent: Optional[str] = None) -> str:
        node = f"job:{job_name}"
        if self.tree.exists(node):
            return node
        if parent is None:
            parent = self._batch_for(job_name) or self._autocad_node or ""
        title = job_name.split(":", 1)[-1]
        self.tree.insert(
            parent, tk.END, iid=node, text=title,
            values=("· Pending", "", ""), tags=("status:pending",),
        )
        self._info[node] = {
            "title": title, "status": "pending", "kind": "job", "job": job_name
        }
        self._output.setdefault(node, [])
        return node

    def _batch_for(self, job_name: str) -> Optional[str]:
        prefix = job_name.split(":", 1)[0]
        label = {"view": "views", "sheet": "sheets", "merge": "merge"}.get(prefix)
        node = f"batch:{label}" if label else None
        return node if node and self.tree.exists(node) else None

    # -- status -----------------------------------------------------------
    def set_stage_status(
        self, name: str, status: str, *, duration: Optional[float] = None, detail: str = ""
    ) -> None:
        node = f"stage:{name}"
        if not self.tree.exists(node):
            return
        self._apply_status(node, status, duration=duration, detail=detail)
        if status == "running":
            self.tree.see(node)

    def set_job_status(
        self,
        job_name: str,
        status: str,
        *,
        code: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        node = self._ensure_job(job_name)
        detail = "" if code is None else f"exit {code}"
        self._apply_status(node, status, duration=duration, detail=detail)
        if status in {"completed", "failed"}:
            self._bump_batch(job_name)
        if status == "running":
            self.tree.see(node)
        if node == self._selected:
            self._refresh_detail_header(node)

    def _bump_batch(self, job_name: str) -> None:
        label = {"view": "views", "sheet": "sheets", "merge": "merge"}.get(
            job_name.split(":", 1)[0]
        )
        counts = self._batch_counts.get(label or "")
        if not counts:
            return
        counts[0] += 1
        node = f"batch:{label}"
        if not self.tree.exists(node):
            return
        done, total = counts
        status = "completed" if done >= total else "running"
        values = list(self.tree.item(node, "values"))
        values[1] = f"{done}/{total}"
        self.tree.item(node, values=values)
        self._apply_status(node, status, detail=f"{done}/{total}")

    def _apply_status(
        self, node: str, status: str, *, duration: Optional[float] = None, detail: str = ""
    ) -> None:
        status = status.lower()
        info = self._info.setdefault(node, {"title": node, "kind": "node"})
        info["status"] = status
        if duration is not None:
            info["duration"] = duration
        if detail:
            info["detail"] = detail
        values = list(self.tree.item(node, "values")) or ["", "", ""]
        values[0] = f"{_GLYPHS.get(status, '·')} {status.capitalize()}"
        if detail:
            values[1] = detail
        values[2] = self._format_duration(info.get("duration"))
        self.tree.item(node, values=values, tags=(f"status:{status}",))
        self._apply_filter_to(node)

    def mark_remaining_skipped(self) -> None:
        """A halted pipeline leaves later stages showing Pending forever."""

        for node in self.tree.get_children(""):
            if self._info.get(node, {}).get("status") == "pending":
                self._apply_status(node, "skipped")

    @staticmethod
    def _format_duration(seconds: Optional[float]) -> str:
        if not seconds:
            return ""
        if seconds >= 60:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        return f"{seconds:.1f}s"

    @staticmethod
    def _humanise(name: str) -> str:
        return name.replace("_", " ").title()

    # -- output -----------------------------------------------------------
    def append_output(self, job_name: str, line: str) -> None:
        node = self._ensure_job(job_name)
        lines = self._output.setdefault(node, [])
        lines.append(line)
        if len(lines) > _MAX_OUTPUT_LINES:
            del lines[: len(lines) - _MAX_OUTPUT_LINES]
        if node == self._selected:
            self.output.configure(state="normal")
            self.output.insert(tk.END, line + "\n")
            self.output.see(tk.END)
            self.output.configure(state="disabled")

    # -- filtering --------------------------------------------------------
    def set_filter(self, key: str) -> None:
        self._filter = key
        self._refresh_chips()
        for node in list(self._info):
            self._apply_filter_to(node)

    def _refresh_chips(self) -> None:
        for key, chip in self._filter_chips.items():
            chip.restyle(active=key == self._filter)

    def _matches_filter(self, node: str) -> bool:
        if self._filter == "all":
            return True
        info = self._info.get(node, {})
        if info.get("kind") != "job":
            return True
        return info.get("status") == self._filter

    def _apply_filter_to(self, node: str) -> None:
        if not self.tree.exists(node) and node not in self._hidden:
            return
        visible = self._matches_filter(node)
        if visible and node in self._hidden:
            parent, index = self._hidden.pop(node)
            try:
                self.tree.reattach(node, parent, index)
            except tk.TclError:  # pragma: no cover
                pass
        elif not visible and node not in self._hidden:
            try:
                parent = self.tree.parent(node)
                index = self.tree.index(node)
                self._hidden[node] = (parent, index)
                self.tree.detach(node)
            except tk.TclError:  # pragma: no cover
                self._hidden.pop(node, None)

    # -- selection --------------------------------------------------------
    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self._selected = selection[0]
        self._show_output(self._selected)

    def _refresh_detail_header(self, node: str) -> None:
        info = self._info.get(node, {})
        self.detail_title.configure(text=info.get("title", "—"))
        status = str(info.get("status", "")).capitalize()
        duration = self._format_duration(info.get("duration"))
        detail = info.get("detail", "")
        parts = [part for part in (status, detail, duration) if part]
        self.detail_status.configure(
            text="  ·  ".join(parts),
            text_color=theme.status_color(info.get("status", "pending")),
        )
        failed = info.get("status") == "failed"
        is_job = info.get("kind") == "job"
        self.open_log_button.pack_forget()
        self.copy_button.pack_forget()
        if is_job:
            if failed:
                self.open_log_button.pack(side="left", padx=(0, 6), pady=(0, 4))
            if self._output.get(node):
                self.copy_button.pack(side="left", pady=(0, 4))

    def _show_output(self, node: Optional[str]) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        if node is not None:
            self._refresh_detail_header(node)
            for line in self._output.get(node, []):
                self.output.insert(tk.END, line + "\n")
            self.output.see(tk.END)
        else:
            self.detail_title.configure(text="—")
            self.detail_status.configure(text="")
            self.open_log_button.pack_forget()
            self.copy_button.pack_forget()
        self.output.configure(state="disabled")

    def _open_job_log(self) -> None:
        info = self._info.get(self._selected or "", {})
        job = info.get("job")
        if not job:
            return
        path = self._job_log_resolver(job)
        if path is not None and path.exists():
            open_path(path)

    def _copy_output(self) -> None:
        lines = self._output.get(self._selected or "", [])
        if lines:
            copy_to_clipboard(self, "\n".join(lines))


__all__ = ["WorkView"]
