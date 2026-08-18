"""Zone B — the run plan before the click, the live header during the run.

Pressing Run is destructive: on a rerun it removes everything in the project
root that is not ``originals/``, ``original.zip`` or a config file. That was
previously a zero-information trigger — the confirmation dialog that stood in
for it was removed, and the data was only ever logged *after* the run started.
Here it is on screen, before the click.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Sequence

import customtkinter as ctk

from dwgmagic.gui import theme
from dwgmagic.miscutil import RunPlan

_MODE_LABELS = {
    "fresh": "First run — DWGs in the folder",
    "rerun": "Re-run — restoring from originals/",
    "archive": "Re-run — restoring from original.zip",
    "invalid": "Not a DWGMAGIC project",
}


class RunPanel(ctk.CTkFrame):
    def __init__(
        self,
        master,
        *,
        on_run: Callable[[], None],
        on_cancel: Callable[[], None],
        on_options: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, fg_color=theme.color("surface.raised"), **kwargs)
        self._on_run = on_run
        self._started: Optional[float] = None
        self._durations: list[float] = []
        self._total_jobs = 0
        self._done_jobs = 0

        self.grid_columnconfigure(0, weight=1)

        # Header line: phase/status on the left, actions on the right.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        header.grid_columnconfigure(1, weight=1)

        self.phase_label = ctk.CTkLabel(
            header, text="Ready", font=ctk.CTkFont(size=15, weight="bold"), anchor="w"
        )
        self.phase_label.grid(row=0, column=0, sticky="w")

        self.timing_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=theme.color("text.muted"),
            anchor="w",
        )
        self.timing_label.grid(row=0, column=1, sticky="w", padx=(12, 0))

        buttons = ctk.CTkFrame(header, fg_color="transparent")
        buttons.grid(row=0, column=2, sticky="e")
        self.options_button = ctk.CTkButton(
            buttons,
            text="Options",
            width=80,
            height=32,
            fg_color="transparent",
            border_width=1,
            border_color=theme.color("border"),
            text_color=theme.color("text"),
            hover_color=theme.color("surface.sunken"),
            command=on_options,
        )
        self.options_button.pack(side="left", padx=(0, 8))
        self.run_button = ctk.CTkButton(
            buttons,
            text="▶  Run",
            width=120,
            height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=theme.color("success"),
            hover_color=theme.color("success.hover"),
            command=self._on_run,
        )
        self.run_button.pack(side="left")
        self.cancel_button = ctk.CTkButton(
            buttons,
            text="■  Cancel",
            width=110,
            height=36,
            fg_color=theme.color("danger"),
            hover_color=theme.color("danger.hover"),
            command=on_cancel,
        )

        # Progress bar, shown only while running.
        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)

        # The plan / reason surface.
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)

        self.produces_label = ctk.CTkLabel(
            self.body, text="", font=ctk.CTkFont(size=11), anchor="nw", justify="left"
        )
        self.produces_label.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        self.deletes_label = ctk.CTkLabel(
            self.body, text="", font=ctk.CTkFont(size=11), anchor="nw", justify="left"
        )
        self.deletes_label.grid(row=0, column=1, sticky="nw")

        self.set_enabled(False, reason="Open a project to begin.")

    # -- idle state -------------------------------------------------------
    def show_plan(self, plan: RunPlan) -> None:
        """Render what a run would produce and destroy."""

        self.phase_label.configure(text=_MODE_LABELS.get(plan.mode, plan.mode))
        if not plan.dwg_count:
            self.timing_label.configure(text="")
        else:
            self.timing_label.configure(text=f"{plan.dwg_count} DWG file(s)")

        produces = "\n".join(f"    {path.name}" for path in plan.produces[:8])
        self.produces_label.configure(
            text=f"Will create / overwrite:\n{produces}" if produces else "",
            text_color=theme.color("text.muted"),
        )

        if plan.deletes:
            shown = [path.name for path in plan.deletes[:6]]
            extra = len(plan.deletes) - len(shown)
            if extra > 0:
                shown.append(f"…and {extra} more")
            listing = "\n".join(f"    {name}" for name in shown)
            self.deletes_label.configure(
                text=f"Will DELETE first:\n{listing}", text_color=theme.color("danger")
            )
        else:
            self.deletes_label.configure(
                text="Will delete: nothing", text_color=theme.color("text.muted")
            )

    def set_enabled(self, enabled: bool, *, reason: str = "") -> None:
        self.run_button.configure(state="normal" if enabled else "disabled")
        if not enabled and reason:
            self.phase_label.configure(text=reason)
            self.produces_label.configure(text="")
            self.deletes_label.configure(text="")

    # -- running state ----------------------------------------------------
    def begin_run(self) -> None:
        self._started = time.monotonic()
        self._durations = []
        self._total_jobs = 0
        self._done_jobs = 0
        self.run_button.pack_forget()
        self.cancel_button.configure(state="normal", text="■  Cancel")
        self.cancel_button.pack(side="left")
        self.options_button.configure(state="disabled")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.produces_label.configure(text="")
        self.deletes_label.configure(text="")
        self.set_phase("Starting…")

    def end_run(self) -> None:
        self._started = None
        self.cancel_button.pack_forget()
        self.run_button.pack(side="left")
        self.options_button.configure(state="normal")
        self.progress.grid_forget()

    def set_cancelling(self, text: str = "Stopping AutoCAD jobs…") -> None:
        self.cancel_button.configure(state="disabled", text="Stopping…")
        self.set_phase(text)

    def set_phase(self, text: str) -> None:
        self.phase_label.configure(text=text)

    def set_plan_total(self, total: int) -> None:
        self._total_jobs = total

    def record_job(self, duration: Optional[float]) -> None:
        """Feed a completed job's duration so the ETA can be estimated."""

        self._done_jobs += 1
        if duration:
            self._durations.append(duration)

    def set_progress(self, fraction: float, detail: str = "") -> None:
        self.progress.set(max(0.0, min(1.0, fraction)))
        self._refresh_timing(detail)

    def tick(self) -> None:
        self._refresh_timing()

    def _refresh_timing(self, detail: str = "") -> None:
        parts = []
        if self._started is not None:
            elapsed = int(time.monotonic() - self._started)
            parts.append(f"⏱ {elapsed // 60}:{elapsed % 60:02d}")
        eta = self._estimate_remaining()
        if eta is not None:
            parts.append(f"ETA ~{int(eta) // 60}:{int(eta) % 60:02d}")
        if detail:
            parts.append(detail)
        if parts:
            self.timing_label.configure(text="   ".join(parts))

    def _estimate_remaining(self) -> Optional[float]:
        """Mean completed-job duration projected over what is left.

        Only meaningful once the total is fixed and some jobs have finished,
        which is exactly what the up-front job plan provides.
        """

        if not self._durations or self._total_jobs <= 0:
            return None
        remaining = self._total_jobs - self._done_jobs
        if remaining <= 0:
            return None
        mean = sum(self._durations) / len(self._durations)
        return mean * remaining


__all__ = ["RunPanel"]
