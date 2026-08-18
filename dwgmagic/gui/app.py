"""CustomTkinter-based GUI for the DWGMAGIC pipeline.

Features: drag & drop project loading, preflight checks, cancellable runs,
live AutoCAD job output, light/dark theming, recent projects, run summaries,
and update notifications from GitHub releases.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable, Mapping, Sequence

import customtkinter as ctk

try:  # Drag & drop is optional; the GUI must still work without the tkdnd binary.
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _DND_AVAILABLE = True
except Exception:  # pragma: no cover - depends on native library presence
    _DND_AVAILABLE = False

from jinja2 import Environment

import dwgmagic
from dwgmagic.core.context import ProjectConfig, ProjectContext
from dwgmagic.core.pipeline import CANCEL_EVENT_KEY, PipelineRunner, PipelineStage
from dwgmagic.core.stages import build_default_stages
from dwgmagic.gui.state import GuiState
from dwgmagic.integrations.autocad import AutoCadCoordinator, AutoCadRunner, discover_autocad
from dwgmagic.logger import LoggerFactory
from dwgmagic.manifest import build_summary_lines, write_manifest
from dwgmagic.miscutil import inspect_project
from dwgmagic.settings import APP_ROOT, GITHUB_REPO, Settings
from dwgmagic.trusted_folder import TrustedFolderChecker, add_trusted_path
from dwgmagic.ui.progress import ProgressEvent, QueueProgressListener
from dwgmagic.update import check_for_update, launch_updater

# Configure CustomTkinter
ctk.set_default_color_theme("blue")

_MAX_TASK_LOG_LINES = 1000


class QueueLogHandler(logging.Handler):
    """Logging handler that forwards formatted records to the GUI queue."""

    def __init__(self, event_queue: "queue.Queue[ProgressEvent]") -> None:
        super().__init__()
        self.event_queue = event_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.event_queue.put(
            ProgressEvent(
                "log",
                {
                    "name": record.name,
                    "level": record.levelname,
                    "message": message,
                },
            )
        )


if _DND_AVAILABLE:

    class _RootWindow(ctk.CTk, TkinterDnD.DnDWrapper):
        """CustomTkinter root window with Drag & Drop support."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)

else:  # pragma: no cover - depends on native library presence

    class _RootWindow(ctk.CTk):
        """CustomTkinter root window (drag & drop unavailable)."""


class GuiApplication(_RootWindow):
    """Encapsulates the CustomTkinter user interface and pipeline orchestration."""

    def __init__(
        self,
        *,
        settings_loader: Callable[[Path], Settings],
        environment_builder: Callable[[Settings], Environment],
        runner_factory: Callable[[Settings], AutoCadRunner],
        coordinator_factory: Callable[[AutoCadRunner], AutoCadCoordinator],
        logger_factory_builder: Callable[[Settings], LoggerFactory],
        initial_project: Path | None = None,
        autorun: bool = False,
        enable_update_check: bool = True,
    ) -> None:
        super().__init__()

        self.settings_loader = settings_loader
        self.environment_builder = environment_builder
        self.runner_factory = runner_factory
        self.coordinator_factory = coordinator_factory
        self.logger_factory_builder = logger_factory_builder

        self.current_settings: Settings | None = None
        self.environment: Environment | None = None
        self.runner: AutoCadRunner | None = None
        self.coordinator: AutoCadCoordinator | None = None
        self.logger_factory: LoggerFactory | None = None
        self.pipeline: PipelineRunner | None = None
        self.stages: Sequence[PipelineStage] = ()
        self.stage_names: list[str] = []

        self.gui_state = GuiState.load()
        ctk.set_appearance_mode(self.gui_state.appearance)

        self.event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()

        self.log_handler = QueueLogHandler(self.event_queue)
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

        self.listener = QueueProgressListener(self.event_queue)

        # Preflight status
        self.preflight_autocad = tk.StringVar(value="Checking…")
        self.preflight_plugin = tk.StringVar(value="Checking…")
        self.preflight_trusted = tk.StringVar(value="Requires project")

        # UI Setup
        self.title(f"DWGMAGIC v{dwgmagic.__version__}")
        self.geometry(self.gui_state.geometry)
        self.minsize(1000, 700)

        # Configure grid layout (1x2)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # State Variables
        self.status_var = tk.StringVar(value="Ready")
        self.project_var = tk.StringVar(value="No project selected")
        self.completed_stages = 0
        self.job_total = 0
        self.job_completed = 0
        self._job_total_is_final = False
        self.job_states: dict[str, dict[str, str | int | None]] = {}
        self.job_logs: dict[str, list[str]] = {}
        self.task_info: dict[str, dict[str, str | int | float | None]] = {}
        self.task_parents: dict[str, str] = {}
        self._current_task_selection: str | None = None
        self.context: ProjectContext | None = None
        self._running = False
        self._cancel_event: threading.Event | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._shutting_down = False
        self._shutdown_deadline: float | None = None
        self._autorun_pending = autorun and initial_project is not None
        self._update_info = None
        self._run_started: float | None = None
        self._trusted_fix_target: Path = APP_ROOT
        self._trusted_fix_prompted = False
        self.cpu_count = os.cpu_count() or 4
        self.progress_text_var = tk.StringVar(value="")
        self.elapsed_var = tk.StringVar(value="")

        self._build_layout()
        self._apply_tree_style()
        self._reset_stage_table()
        self._reset_task_tree()
        self._update_check_enabled = enable_update_check
        self.after(100, self._process_events)
        self.after(500, self._run_startup_checks)
        self.after(1500, self._run_update_check)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Enable Drop on the window
        if _DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:  # pragma: no cover - depends on tkdnd binary
                pass

        if initial_project:
            try:
                self._load_project(initial_project)
            except Exception as exc:
                self._autorun_pending = False
                messagebox.showerror("Project Load Failed", str(exc))

    def _new_context(self) -> ProjectContext:
        if not self.current_settings or not self.environment:
            raise RuntimeError("Project configuration has not been loaded")
        config = ProjectConfig(settings=self.current_settings, stages=self.stage_names)
        return ProjectContext(config=config, environment=self.environment)

    # UI construction -----------------------------------------------------
    def _build_layout(self) -> None:
        # --- Sidebar ---
        self.sidebar_frame = ctk.CTkFrame(self, width=240, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(11, weight=1)

        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, text="DWGMAGIC", font=ctk.CTkFont(size=24, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 0))
        ctk.CTkLabel(
            self.sidebar_frame,
            text=f"v{dwgmagic.__version__}",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        ).grid(row=1, column=0, padx=20, pady=(0, 10))

        # Project Controls
        ctk.CTkLabel(
            self.sidebar_frame, text="Project Control", anchor="w", font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=2, column=0, padx=20, pady=(10, 0))

        self.open_proj_btn = ctk.CTkButton(
            self.sidebar_frame, text="Open Project…", command=self._choose_project
        )
        self.open_proj_btn.grid(row=3, column=0, padx=20, pady=(10, 4))

        self.recent_menu = ctk.CTkOptionMenu(
            self.sidebar_frame,
            values=self._recent_menu_values(),
            command=self._on_recent_selected,
        )
        self.recent_menu.grid(row=4, column=0, padx=20, pady=(0, 4))
        self.recent_menu.set("Recent projects…")

        hint = "(or drop a folder here)" if _DND_AVAILABLE else ""
        ctk.CTkLabel(
            self.sidebar_frame, text=hint, font=ctk.CTkFont(size=10), text_color="gray"
        ).grid(row=5, column=0)

        self.project_display_label = ctk.CTkLabel(
            self.sidebar_frame,
            textvariable=self.project_var,
            font=ctk.CTkFont(size=12, slant="italic"),
            wraplength=200,
            text_color="gray70",
        )
        self.project_display_label.grid(row=6, column=0, padx=20, pady=(0, 12))

        self.run_button = ctk.CTkButton(
            self.sidebar_frame,
            text="▶   Run Pipeline",
            command=self._start_pipeline,
            font=ctk.CTkFont(size=14, weight="bold"),
            height=36,
            fg_color="#2fa572",
            hover_color="#20744f",
        )
        self.run_button.grid(row=7, column=0, padx=20, pady=(0, 6))
        self.run_button.configure(state="disabled")

        self.cancel_button = ctk.CTkButton(
            self.sidebar_frame,
            text="■   Cancel Run",
            command=self._cancel_pipeline,
            fg_color="#b04a4a",
            hover_color="#7a2f2f",
        )
        self.cancel_button.grid(row=8, column=0, padx=20, pady=(0, 10))
        self.cancel_button.configure(state="disabled")

        # Execution options
        workers_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        workers_frame.grid(row=9, column=0, padx=20, pady=(4, 0), sticky="ew")
        ctk.CTkLabel(
            workers_frame,
            text="Parallel AutoCAD jobs",
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        worker_values = [str(i) for i in range(1, self.cpu_count + 1)]
        self.workers_menu = ctk.CTkOptionMenu(workers_frame, values=worker_values, width=90,
                                              command=self._on_workers_changed)
        self.workers_menu.pack(anchor="w", pady=(4, 0))
        chosen = self.gui_state.max_workers or self.cpu_count
        chosen = max(1, min(chosen, self.cpu_count))
        self.workers_menu.set(str(chosen))
        self.workers_hint = ctk.CTkLabel(
            workers_frame, font=ctk.CTkFont(size=10), text_color="gray60", text=""
        )
        self.workers_hint.pack(anchor="w")
        self._refresh_workers_hint()

        # Preflight Checks Section
        preflight = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        preflight.grid(row=10, column=0, padx=20, pady=(14, 0), sticky="ew")
        ctk.CTkLabel(
            preflight, text="Preflight Checks", anchor="w", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(preflight, text="AutoCAD:", font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", pady=(6, 0)
        )
        self.lbl_acad_status = ctk.CTkLabel(
            preflight, textvariable=self.preflight_autocad, font=ctk.CTkFont(size=11), wraplength=200
        )
        self.lbl_acad_status.pack(anchor="w", padx=(10, 0))

        ctk.CTkLabel(preflight, text="Plugin (tectonica.dll):", font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", pady=(6, 0)
        )
        self.lbl_plugin_status = ctk.CTkLabel(
            preflight, textvariable=self.preflight_plugin, font=ctk.CTkFont(size=11), wraplength=200
        )
        self.lbl_plugin_status.pack(anchor="w", padx=(10, 0))

        ctk.CTkLabel(preflight, text="Trusted Path:", font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", pady=(6, 0)
        )
        self.lbl_trust_status = ctk.CTkLabel(
            preflight, textvariable=self.preflight_trusted, font=ctk.CTkFont(size=11), wraplength=200
        )
        self.lbl_trust_status.pack(anchor="w", padx=(10, 0))

        # Shown only when the trusted-path check fails and is fixable.
        self.fix_trusted_btn = ctk.CTkButton(
            preflight,
            text="Fix trusted path",
            command=self._on_fix_trusted_clicked,
            fg_color="#b8860b",
            hover_color="#8a6508",
            height=26,
            width=140,
        )

        # Spacer
        ctk.CTkLabel(self.sidebar_frame, text="").grid(row=11, column=0)

        # Update notification (hidden until an update is found)
        self.update_button = ctk.CTkButton(
            self.sidebar_frame,
            text="Update available",
            command=self._apply_update,
            fg_color="#b8860b",
            hover_color="#8a6508",
        )
        # not gridded yet — shown by _handle_event("update_available")

        # Appearance Mode
        ctk.CTkLabel(self.sidebar_frame, text="Appearance Mode:", anchor="w").grid(
            row=13, column=0, padx=20, pady=(10, 0)
        )
        self.appearance_mode_optionmenu = ctk.CTkOptionMenu(
            self.sidebar_frame,
            values=["System", "Light", "Dark"],
            command=self._change_appearance_mode_event,
        )
        self.appearance_mode_optionmenu.grid(row=14, column=0, padx=20, pady=(10, 20))
        self.appearance_mode_optionmenu.set(self.gui_state.appearance)

        # --- Main Area ---
        self.main_content = ctk.CTkFrame(self, fg_color="transparent")
        self.main_content.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_content.grid_rowconfigure(3, weight=1)  # Tabview expands
        self.main_content.grid_columnconfigure(0, weight=1)

        # 1. Header & Status
        self.status_frame = ctk.CTkFrame(self.main_content, fg_color="transparent")
        self.status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(
            self.status_frame, text="Status:", font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left", padx=(0, 10))

        self.status_value_label = ctk.CTkLabel(
            self.status_frame, textvariable=self.status_var, font=ctk.CTkFont(size=16)
        )
        self.status_value_label.pack(side="left")

        self.elapsed_label = ctk.CTkLabel(
            self.status_frame,
            textvariable=self.elapsed_var,
            font=ctk.CTkFont(size=13),
            text_color="gray60",
        )
        self.elapsed_label.pack(side="left", padx=(14, 0))

        self.progress_text_label = ctk.CTkLabel(
            self.status_frame,
            textvariable=self.progress_text_var,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.progress_text_label.pack(side="right", padx=(0, 4))
        self.progress_bar = ctk.CTkProgressBar(self.status_frame)
        self.progress_bar.pack(side="right", fill="x", expand=True, padx=20)
        self.progress_bar.set(0)

        # 2. Stages (Always Visible)
        ctk.CTkLabel(
            self.main_content, text="Pipeline Stages", font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=1, column=0, sticky="w", pady=(0, 5))

        self.stages_container = ctk.CTkFrame(self.main_content, height=150)
        self.stages_container.grid(row=2, column=0, sticky="ew", pady=(0, 20))
        self.stages_container.pack_propagate(False)  # Respect height

        self.style = ttk.Style()
        self.style.theme_use("default")

        self.stage_tree = ttk.Treeview(
            self.stages_container,
            columns=("stage", "status", "time"),
            show="headings",
        )
        self.stage_tree.heading("stage", text="Stage Name")
        self.stage_tree.heading("status", text="Result")
        self.stage_tree.heading("time", text="Time")
        self.stage_tree.column("stage", width=380, anchor="w")
        self.stage_tree.column("status", width=180, anchor="w")
        self.stage_tree.column("time", width=90, anchor="e")

        stage_scroll_y = ctk.CTkScrollbar(self.stages_container, command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stage_scroll_y.set)
        stage_scroll_y.pack(side="right", fill="y")
        self.stage_tree.pack(side="left", fill="both", expand=True)

        # 3. Tabview (Tasks & Logs)
        self.tabview = ctk.CTkTabview(self.main_content)
        self.tabview.grid(row=3, column=0, sticky="nsew")
        self.tabview.add("Tasks")
        self.tabview.add("Logs")

        # -- Tasks Tab --
        self.tabview.tab("Tasks").grid_columnconfigure(0, weight=1)
        self.tabview.tab("Tasks").grid_rowconfigure(0, weight=1)

        self.tasks_paned = tk.PanedWindow(
            self.tabview.tab("Tasks"), orient=tk.HORIZONTAL, sashwidth=4, borderwidth=0
        )
        self.tasks_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # Left: Task Tree
        self.tree_frame = ctk.CTkFrame(self.tasks_paned)
        self.tasks_paned.add(self.tree_frame)
        self.tree_frame.pack_propagate(False)

        self.task_tree = ttk.Treeview(
            self.tree_frame,
            columns=("status", "code", "time"),
            show="tree headings",
            selectmode="browse",
        )
        self.task_tree.heading("#0", text="Task")
        self.task_tree.heading("status", text="Status")
        self.task_tree.heading("code", text="Code")
        self.task_tree.heading("time", text="Time")
        self.task_tree.column("#0", minwidth=260, stretch=True)
        self.task_tree.column("status", width=90, anchor="w")
        self.task_tree.column("code", width=60, anchor="center")
        self.task_tree.column("time", width=70, anchor="e")

        self.tree_scroll_y = ctk.CTkScrollbar(self.tree_frame, command=self.task_tree.yview)
        self.tree_scroll_x = ctk.CTkScrollbar(
            self.tree_frame, orientation="horizontal", command=self.task_tree.xview
        )
        self.task_tree.configure(
            yscrollcommand=self.tree_scroll_y.set, xscrollcommand=self.tree_scroll_x.set
        )

        self.tree_scroll_y.pack(side="right", fill="y")
        self.tree_scroll_x.pack(side="bottom", fill="x")
        self.task_tree.pack(fill="both", expand=True)

        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_selected)

        # Right: Task Details
        self.details_frame = ctk.CTkFrame(self.tasks_paned)
        self.tasks_paned.add(self.details_frame)

        self.details_header = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.details_header.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(self.details_header, text="Task:", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.detail_name_var = tk.StringVar(value="—")
        ctk.CTkLabel(self.details_header, textvariable=self.detail_name_var).grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(self.details_header, text="Status:", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=1, column=0, sticky="w", padx=(0, 10)
        )
        self.detail_status_var = tk.StringVar(value="—")
        ctk.CTkLabel(self.details_header, textvariable=self.detail_status_var).grid(row=1, column=1, sticky="w")

        ctk.CTkLabel(self.details_header, text="Exit Code:", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=2, column=0, sticky="w", padx=(0, 10)
        )
        self.detail_code_var = tk.StringVar(value="—")
        ctk.CTkLabel(self.details_header, textvariable=self.detail_code_var).grid(row=2, column=1, sticky="w")

        ctk.CTkLabel(self.details_frame, text="Task Output Log:", anchor="w").pack(fill="x", padx=10)
        self.task_log = ctk.CTkTextbox(self.details_frame, wrap="word", font=("Consolas", 12))
        self.task_log.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.task_log.configure(state="disabled")

        # -- Logs Tab --
        self.tabview.tab("Logs").grid_columnconfigure(0, weight=1)
        self.tabview.tab("Logs").grid_rowconfigure(0, weight=1)
        self.log_widget = ctk.CTkTextbox(self.tabview.tab("Logs"), wrap="word", font=("Consolas", 12))
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)
        try:
            self.log_widget.tag_config("error", foreground="#e05a5a")
            self.log_widget.tag_config("warning", foreground="#d9a13d")
        except Exception:  # pragma: no cover - depends on ctk internals
            pass
        self.log_widget.configure(state="disabled")

        self._configure_status_tags(self.stage_tree)
        self._configure_status_tags(self.task_tree)

    # Theming --------------------------------------------------------------
    def _apply_tree_style(self) -> None:
        """Style the ttk widgets to match the current light/dark appearance."""

        dark = ctk.get_appearance_mode() == "Dark"
        if dark:
            background, foreground = "#333333", "#f0f0f0"
            heading_bg, heading_fg = "#555555", "#f0f0f0"
            selected = "#1f538d"
            paned_bg = "#2b2b2b"
        else:
            background, foreground = "#f4f4f4", "#1a1a1a"
            heading_bg, heading_fg = "#dcdcdc", "#1a1a1a"
            selected = "#3b8ed0"
            paned_bg = "#e6e6e6"

        self.style.configure(
            "Treeview",
            background=background,
            foreground=foreground,
            fieldbackground=background,
            borderwidth=0,
            rowheight=27,
            font=("Segoe UI", 10),
        )
        self.style.map("Treeview", background=[("selected", selected)])
        self.style.configure(
            "Treeview.Heading",
            background=heading_bg,
            foreground=heading_fg,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padding=(6, 4),
        )
        try:
            self.tasks_paned.configure(bg=paned_bg)
        except Exception:  # pragma: no cover
            pass

    def _change_appearance_mode_event(self, new_appearance_mode: str) -> None:
        ctk.set_appearance_mode(new_appearance_mode)
        self.gui_state.appearance = new_appearance_mode
        self.gui_state.save()
        # get_appearance_mode() resolves "System"; restyle after the switch.
        self.after(50, self._apply_tree_style)

    # Persistence ----------------------------------------------------------
    #: How long to wait for AutoCAD jobs to die before closing anyway.
    _SHUTDOWN_GRACE_SECONDS = 30.0

    def _on_close(self) -> None:
        if self._running and not self._shutting_down:
            proceed = messagebox.askyesno(
                "Pipeline Running",
                "A pipeline run is still in progress. Cancel it and exit?",
            )
            if not proceed:
                return
            self._begin_shutdown()
            return
        self._save_state_and_destroy()

    def _begin_shutdown(self) -> None:
        """Cancel the run and wait for AutoCAD to exit before destroying.

        The pipeline thread is a daemon, so destroying the window immediately
        kills it before the runner can reap its children — leaving orphaned
        accoreconsole processes holding locks on the project's DWG files.
        """

        self._shutting_down = True
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._set_status("Stopping AutoCAD jobs…", kind="warning")
        self.cancel_button.configure(state="disabled", text="Stopping…")
        self.run_button.configure(state="disabled")
        self._shutdown_deadline = time.monotonic() + self._SHUTDOWN_GRACE_SECONDS
        self._await_shutdown()

    def _await_shutdown(self) -> None:
        """Poll for the pipeline thread to finish; never block the main loop."""

        thread = self._pipeline_thread
        finished = thread is None or not thread.is_alive()
        expired = time.monotonic() >= (self._shutdown_deadline or 0.0)
        if finished or expired:
            if expired and not finished:
                self._append_log(
                    "AutoCAD jobs did not stop within "
                    f"{self._SHUTDOWN_GRACE_SECONDS:.0f}s; closing anyway.",
                    level="warning",
                )
            self._save_state_and_destroy()
            return
        self.after(200, self._await_shutdown)

    def _save_state_and_destroy(self) -> None:
        try:
            self.gui_state.geometry = self.geometry()
            self.gui_state.save()
        finally:
            self.destroy()

    def _recent_menu_values(self) -> list[str]:
        return self.gui_state.recent_projects or ["(no recent projects)"]

    def _on_recent_selected(self, value: str) -> None:
        self.recent_menu.set("Recent projects…")
        if value.startswith("("):
            return
        if self._running:
            messagebox.showwarning(
                "Pipeline Running", "Wait for the current run to finish before switching projects."
            )
            return
        path = Path(value)
        if not path.exists():
            messagebox.showerror("Not Found", f"{path} no longer exists.")
            self.gui_state.recent_projects = [
                item for item in self.gui_state.recent_projects if item != value
            ]
            self.gui_state.save()
            self.recent_menu.configure(values=self._recent_menu_values())
            return
        try:
            self._load_project(path)
        except Exception as exc:
            messagebox.showerror("Project Load Failed", str(exc))

    # Preflight ------------------------------------------------------------
    def _run_startup_checks(self) -> None:
        """Checks that run on every app start, before any project is loaded."""

        def _check() -> None:
            acad_ok = False
            try:
                exe = discover_autocad(None)
                acad_ok = True
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {"check": "autocad", "status": f"Found: {exe}", "ok": True},
                    )
                )
            except Exception:
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {
                            "check": "autocad",
                            "status": "Not found (install AutoCAD or set --autocad-path)",
                            "ok": False,
                        },
                    )
                )

            dll = APP_ROOT / "tectonica.dll"
            self.event_queue.put(
                ProgressEvent(
                    "preflight",
                    {
                        "check": "plugin",
                        "status": "Present" if dll.exists() else f"Missing: {dll}",
                        "ok": dll.exists(),
                    },
                )
            )

            if acad_ok and dll.exists():
                self._trusted_check_sync(Settings(project_root=APP_ROOT))
            else:
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {"check": "trusted", "status": "Skipped (see above)", "ok": None},
                    )
                )

        threading.Thread(target=_check, daemon=True).start()

    def _trusted_check_sync(self, settings: Settings) -> None:
        """Run the trusted-path check (blocking; call from a worker thread)."""

        self.event_queue.put(
            ProgressEvent("preflight", {"check": "trusted", "status": "Checking…", "ok": None})
        )
        try:
            runner = self.runner_factory(settings)
            TrustedFolderChecker(runner).check(settings, logging.getLogger("Preflight"))
            self.event_queue.put(
                ProgressEvent("preflight", {"check": "trusted", "status": "OK", "ok": True})
            )
        except Exception:
            dll_present = (settings.tectonica_path / "tectonica.dll").exists()
            self.event_queue.put(
                ProgressEvent(
                    "preflight",
                    {
                        "check": "trusted",
                        "status": f"AutoCAD does not trust {settings.tectonica_path}",
                        "ok": False,
                        "fixable": dll_present,
                        "target": str(settings.tectonica_path),
                    },
                )
            )

    def _on_fix_trusted_clicked(self) -> None:
        self._apply_trusted_fix()

    def _propose_trusted_fix(self) -> None:
        """One-time per session: proactively offer to apply the registry fix."""

        if self._trusted_fix_prompted:
            return
        self._trusted_fix_prompted = True
        proceed = messagebox.askyesno(
            "AutoCAD Trusted Path",
            f"AutoCAD does not trust the DWGMAGIC folder:\n\n"
            f"{self._trusted_fix_target}\n\n"
            "Generated scripts load tectonica.dll from there, so pipeline "
            "runs will fail until it is trusted.\n\n"
            "Add it to AutoCAD's trusted locations now? (This updates the "
            "TRUSTEDPATHS setting of your AutoCAD profiles — current user "
            "only, no administrator rights needed.)",
        )
        if proceed:
            self._apply_trusted_fix()

    def _apply_trusted_fix(self) -> None:
        """Append the app folder to AutoCAD's TRUSTEDPATHS and re-check."""

        target = self._trusted_fix_target
        self.fix_trusted_btn.configure(state="disabled", text="Fixing…")

        def _fix() -> None:
            try:
                modified = add_trusted_path(target)
                self.event_queue.put(
                    ProgressEvent(
                        "log",
                        {
                            "name": "Preflight",
                            "level": "INFO",
                            "message": (
                                f"Added {target} to TRUSTEDPATHS of "
                                f"{len(modified)} AutoCAD profile(s)"
                                if modified
                                else f"{target} was already trusted by every profile"
                            ),
                        },
                    )
                )
            except Exception as exc:
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {
                            "check": "trusted",
                            "status": f"Fix failed: {exc}",
                            "ok": False,
                            "fixable": True,
                            "target": str(target),
                        },
                    )
                )
                return
            settings = self.current_settings or Settings(project_root=APP_ROOT)
            self._trusted_check_sync(settings)

        threading.Thread(target=_fix, daemon=True).start()

    def _run_project_checks(self, settings: Settings) -> None:
        """Runs checks that depend on the loaded project settings."""

        def _check() -> None:
            runner = self.runner_factory(settings)
            try:
                exe = runner.discover()
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {"check": "autocad", "status": f"Found: {exe}", "ok": True},
                    )
                )
            except Exception:
                self.event_queue.put(
                    ProgressEvent(
                        "preflight", {"check": "autocad", "status": "Not found", "ok": False}
                    )
                )
                return

            dll = settings.tectonica_path / "tectonica.dll"
            self.event_queue.put(
                ProgressEvent(
                    "preflight",
                    {
                        "check": "plugin",
                        "status": "Present" if dll.exists() else f"Missing: {dll}",
                        "ok": dll.exists(),
                    },
                )
            )
            if not dll.exists():
                self.event_queue.put(
                    ProgressEvent(
                        "preflight",
                        {"check": "trusted", "status": "Skipped (plugin missing)", "ok": False},
                    )
                )
                return

            self._trusted_check_sync(settings)

        threading.Thread(target=_check, daemon=True).start()

    # Updates ----------------------------------------------------------------
    def _update_checks_enabled(self) -> bool:
        """CLI flag / env gate, plus the loaded project's ``check_updates``."""

        if not self._update_check_enabled:
            return False
        settings = self.current_settings
        return True if settings is None else bool(settings.check_updates)

    def _run_update_check(self) -> None:
        if not self._update_checks_enabled():
            return

        def _check() -> None:
            info = check_for_update()
            if info is not None:
                self.event_queue.put(
                    ProgressEvent(
                        "update_available",
                        {
                            "latest": info.latest,
                            "current": info.current,
                            "url": info.url,
                            "package_url": info.package_url,
                        },
                    )
                )

        threading.Thread(target=_check, daemon=True).start()

    def _apply_update(self) -> None:
        if self._running:
            messagebox.showwarning(
                "Pipeline Running", "Finish or cancel the current run before updating."
            )
            return
        info = self._update_info or {}
        package_url = info.get("package_url")
        if package_url and launch_updater(package_url, relaunch_gui=True):
            messagebox.showinfo(
                "Updating",
                "The updater has been started. DWGMAGIC will close now and "
                "reopen once the update finishes.",
            )
            self._on_close()
        else:
            url = info.get("url", f"https://github.com/{GITHUB_REPO}/releases")
            webbrowser.open(url)

    # Drag & drop ------------------------------------------------------------
    def _on_drop(self, event) -> None:
        if self._running:
            messagebox.showwarning(
                "Pipeline Running",
                "Please wait for the current pipeline run to finish before changing projects.",
            )
            return
        paths = self.tk.splitlist(event.data)
        if not paths:
            return
        path = Path(paths[0])
        if path.is_dir():
            try:
                self._load_project(path.resolve())
            except Exception as exc:
                messagebox.showerror("Project Load Failed", str(exc))
        else:
            messagebox.showinfo("Drop Folder", "Please drop a project folder, not a file.")

    # Tables -----------------------------------------------------------------
    def _reset_stage_table(self) -> None:
        for row in self.stage_tree.get_children():
            self.stage_tree.delete(row)
        for name in self.stage_names:
            self.stage_tree.insert(
                "",
                tk.END,
                iid=name,
                values=(self._display_name(name), "Pending", ""),
                tags=(self._status_tag("pending"),),
            )
        self.progress_bar.set(0)
        self.completed_stages = 0

    def _display_name(self, stage_name: str) -> str:
        return stage_name.replace("_", " ").title()

    def _reset_task_tree(self) -> None:
        if hasattr(self, "task_tree"):
            for row in self.task_tree.get_children():
                self.task_tree.delete(row)
        self.task_info.clear()
        self.job_states.clear()
        self.job_logs.clear()
        self.task_parents = {"merge": ""}
        self.job_total = 0
        self.job_completed = 0
        self._job_total_is_final = False

        self.task_tree.insert(
            "",
            tk.END,
            iid="merge",
            text="Merge",
            values=("Pending", "", ""),
            tags=(self._status_tag("pending"),),
        )
        self.task_tree.item("merge", open=True)
        self.task_info["merge"] = {
            "name": "merge",
            "title": "Merge",
            "status": "pending",
            "code": None,
            "script": None,
            "duration": None,
        }
        self.job_logs["merge"] = []
        self.task_tree.selection_set("merge")
        self._current_task_selection = "merge"
        self._refresh_task_details("merge")

    def _ensure_stage_row(self, name: str) -> None:
        if not self.stage_tree.exists(name):
            self.stage_tree.insert(
                "",
                tk.END,
                iid=name,
                values=(self._display_name(name), "Pending", ""),
                tags=(self._status_tag("pending"),),
            )

    def _configure_status_tags(self, widget: ttk.Treeview) -> None:
        for key, color in [
            ("pending", "gray"),
            ("queued", "gray"),
            ("running", "orange"),
            ("completed", "green"),
            ("failed", "red"),
        ]:
            widget.tag_configure(self._status_tag(key), foreground=color)

    def _status_tag(self, status: str) -> str:
        return f"status:{status}"

    _STATUS_GLYPHS = {
        "pending": "Pending",
        "queued": "•  Queued",
        "running": "▶  Running",
        "completed": "✓  Completed",
        "failed": "✗  Failed",
    }

    def _status_display(self, status: str) -> str:
        return self._STATUS_GLYPHS.get(status, status.capitalize())

    _STATUS_COLORS = {
        "info": ("gray10", "#DCE4EE"),
        "running": "#d9a13d",
        "ok": "#2fa572",
        "error": "#e05a5a",
        "warning": "#d9a13d",
    }

    def _set_status(self, text: str, kind: str = "info") -> None:
        self.status_var.set(text)
        self.status_value_label.configure(
            text_color=self._STATUS_COLORS.get(kind, self._STATUS_COLORS["info"])
        )

    # Execution options ---------------------------------------------------
    def _selected_workers(self) -> int:
        try:
            return max(1, min(int(self.workers_menu.get()), self.cpu_count))
        except (ValueError, tk.TclError):
            return self.cpu_count

    def _on_workers_changed(self, value: str) -> None:
        workers = self._selected_workers()
        # Persist only explicit sub-max choices; None means "all CPUs".
        self.gui_state.max_workers = None if workers == self.cpu_count else workers
        self.gui_state.save()
        self._refresh_workers_hint()

    def _refresh_workers_hint(self) -> None:
        selected = self._selected_workers()
        if selected == self.cpu_count:
            self.workers_hint.configure(text=f"all {self.cpu_count} CPUs (default)")
        else:
            self.workers_hint.configure(text=f"limited ({self.cpu_count} CPUs available)")

    def _tick_elapsed(self) -> None:
        if not self._running or self._run_started is None:
            return
        seconds = int(time.monotonic() - self._run_started)
        self.elapsed_var.set(f"⏱ {seconds // 60}:{seconds % 60:02d}")
        self.after(1000, self._tick_elapsed)

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        if seconds is None:
            return ""
        if seconds >= 60:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        return f"{seconds:.1f}s"

    def _set_stage_status(self, name: str, status: str, *, duration: float | None = None) -> None:
        normalised = status.lower()
        display = self._status_display(normalised)
        self.stage_tree.item(
            name,
            values=(self._display_name(name), display, self._format_duration(duration)),
            tags=(self._status_tag(normalised),),
        )
        self.stage_tree.see(name)

    def _ensure_task_node(
        self,
        job_name: str,
        *,
        parent: str | None = None,
        title: str | None = None,
    ) -> None:
        if parent is None:
            parent = self.task_parents.get(job_name, "merge")
        else:
            self.task_parents[job_name] = parent
        if not title:
            existing = self.task_info.get(job_name)
            title = existing.get("title") if existing else job_name
        if not self.task_tree.exists(parent):
            parent = "merge"
        if not self.task_tree.exists(job_name):
            self.task_tree.insert(
                parent,
                tk.END,
                iid=job_name,
                text=title,
                values=("Pending", "", ""),
                tags=(self._status_tag("pending"),),
            )
        info = self.task_info.setdefault(
            job_name,
            {
                "name": job_name,
                "title": title,
                "status": "pending",
                "code": None,
                "script": None,
                "duration": None,
            },
        )
        info["title"] = title
        self.job_logs.setdefault(job_name, [])

    def _set_task_status(
        self,
        job_name: str,
        status: str,
        *,
        code: int | None = None,
        script: str | None = None,
        duration: float | None = None,
    ) -> None:
        if job_name not in self.task_info:
            self._ensure_task_node(job_name, parent="merge", title=job_name)
        info = self.task_info[job_name]
        info["status"] = status.lower()
        if code is not None:
            info["code"] = code
        if script is not None:
            info["script"] = script
        if duration is not None:
            info["duration"] = duration
        display_status = self._status_display(info["status"])
        code_display = "" if info.get("code") is None else str(info.get("code"))
        self.task_tree.item(
            job_name,
            values=(
                display_status,
                code_display,
                self._format_duration(info.get("duration")),
            ),
            tags=(self._status_tag(info["status"]),),
        )
        if job_name == self._current_task_selection:
            self._refresh_task_details(job_name)

    def _append_task_log(self, job_name: str, message: str) -> None:
        logs = self.job_logs.setdefault(job_name, [])
        logs.append(message)
        if len(logs) > _MAX_TASK_LOG_LINES:
            del logs[: len(logs) - _MAX_TASK_LOG_LINES]
        if job_name == self._current_task_selection:
            # Append incrementally instead of re-rendering the whole buffer.
            self.task_log.configure(state="normal")
            self.task_log.insert(tk.END, message + "\n")
            self.task_log.see(tk.END)
            self.task_log.configure(state="disabled")

    def _populate_task_tree(self, sheets: Iterable[Mapping[str, object]]) -> None:
        for sheet in sheets:
            sheet_name = str(sheet.get("sheetName") or sheet.get("name") or "Unnamed Sheet")
            sheet_id = f"sheet:{sheet_name}"
            self._ensure_task_node(sheet_id, parent="merge", title=f"Sheet: {sheet_name}")
            self.task_tree.item(sheet_id, open=True)
            views = sheet.get("viewsOnSheet") or sheet.get("views") or []
            for view in views:
                if isinstance(view, Mapping):
                    view_name = str(view.get("name") or view.get("viewName") or "View")
                else:
                    view_name = str(view)
                view_id = f"view:{view_name}"
                self._ensure_task_node(view_id, parent=sheet_id, title=f"View: {view_name}")
        self.task_tree.item("merge", open=True)

    def _handle_stage_data(self, stage_name: str, data: Mapping[str, object]) -> None:
        if stage_name == "generate_scripts":
            sheets = data.get("sheets") or data.get("structured_sheets")
            if isinstance(sheets, Iterable):
                self._populate_task_tree(sheets)  # type: ignore[arg-type]
        elif stage_name == "preprocess":
            ignored = data.get("ignored") or []
            orphans = data.get("orphan_views") or []
            if ignored:
                self._append_log(
                    f"Ignored files (no matching convention): {', '.join(map(str, ignored))}",
                    level="warning",
                )
            if orphans:
                self._append_log(
                    f"Views without a matching sheet: {', '.join(map(str, orphans))}",
                    level="warning",
                )

    # Project loading ----------------------------------------------------
    def _choose_project(self) -> None:
        if self._running:
            messagebox.showwarning(
                "Pipeline Running",
                "Please wait for the current pipeline run to finish before changing projects.",
            )
            return
        path_str = filedialog.askdirectory(title="Select DWGMAGIC Project")
        if not path_str:
            return
        try:
            self._load_project(Path(path_str).resolve())
        except Exception as exc:  # pragma: no cover - surfaced via GUI
            messagebox.showerror("Project Load Failed", str(exc))

    def _load_project(self, project_root: Path) -> None:
        if not project_root.exists():
            raise FileNotFoundError(project_root)

        settings = self.settings_loader(project_root)
        environment = self.environment_builder(settings)
        logger_factory = self.logger_factory_builder(settings).with_handlers(self.log_handler)
        runner = self.runner_factory(settings)
        coordinator = self.coordinator_factory(runner)

        self.current_settings = settings
        self.environment = environment
        self.logger_factory = logger_factory
        self.runner = runner
        self.coordinator = coordinator
        self.context = None

        self.stages = build_default_stages(environment, logger_factory, runner, coordinator)
        self.stage_names = [stage.name for stage in self.stages]
        self.pipeline = PipelineRunner.from_iterable(self.stages)

        self.project_var.set(str(project_root))
        self._set_status("Ready")
        self.title(f"DWGMAGIC v{dwgmagic.__version__} — {project_root.name}")
        self._reset_stage_table()
        self._reset_task_tree()
        self._append_log(f"Loaded project at {project_root}")

        inspection = inspect_project(project_root)
        if inspection.is_project:
            self._append_log(f"Project source: {inspection.describe()}")
            self.run_button.configure(state="normal")
        else:
            self._append_log(
                "Warning: no DWG files, originals/, or original.zip found — "
                "running will fail until DWGs are added.",
                level="warning",
            )
            self.run_button.configure(state="normal")

        self.gui_state.remember_project(project_root)
        self.gui_state.save()
        self.recent_menu.configure(values=self._recent_menu_values())

        # Trigger project-specific checks
        self.preflight_autocad.set("Checking…")
        self.preflight_trusted.set("Checking…")
        self._run_project_checks(settings)

        if self._autorun_pending:
            self._autorun_pending = False
            self._append_log("Autorun requested — starting pipeline")
            self.after(700, self._start_pipeline)

    # Task details ---------------------------------------------------------
    def _on_task_selected(self, _event) -> None:
        selection = self.task_tree.selection()
        if not selection:
            return
        job_name = selection[0]
        self._current_task_selection = job_name
        self._refresh_task_details(job_name)

    def _refresh_task_details(self, job_name: str) -> None:
        info = self.task_info.get(job_name)
        if not info:
            self.detail_name_var.set("—")
            self.detail_status_var.set("—")
            self.detail_code_var.set("—")
            self._refresh_task_log(job_name)
            return
        self.detail_name_var.set(info.get("title") or job_name)
        status = str(info.get("status", "pending")).capitalize()
        duration = info.get("duration")
        if duration:
            status = f"{status} ({self._format_duration(duration)})"
        self.detail_status_var.set(status)
        code = info.get("code")
        self.detail_code_var.set("—" if code is None else str(code))
        self._refresh_task_log(job_name)

    def _refresh_task_log(self, job_name: str) -> None:
        self.task_log.configure(state="normal")
        self.task_log.delete("1.0", tk.END)
        for line in self.job_logs.get(job_name, []):
            self.task_log.insert(tk.END, line + "\n")
        self.task_log.see(tk.END)
        self.task_log.configure(state="disabled")

    # Pipeline control ------------------------------------------------------
    def _start_pipeline(self) -> None:
        if self._running:
            return
        if not self.pipeline or not self.current_settings or not self.environment:
            messagebox.showinfo(
                "Select Project",
                "Please open a project folder before running the pipeline.",
            )
            return

        project_root = self.current_settings.project_root
        inspection = inspect_project(project_root)
        if not inspection.is_project:
            messagebox.showerror(
                "Not a DWGMAGIC Project",
                f"{project_root}\n\n{inspection.describe()}\n\n"
                "Nothing was modified. Point DWGMAGIC at a folder containing "
                "the exported DWG files.",
            )
            return
        if inspection.first_run:
            # Logged rather than confirmed: pressing Run is the confirmation, and
            # the layout it describes is the documented, expected behaviour.
            self._append_log(f"First run in {project_root} — {inspection.describe()}")

        self._running = True
        self.run_button.configure(state="disabled")
        self.open_proj_btn.configure(state="disabled")
        self.recent_menu.configure(state="disabled")
        self.workers_menu.configure(state="disabled")
        self.cancel_button.configure(state="normal", text="■   Cancel Run")
        self._set_status("Preparing pipeline…", kind="running")
        self._reset_stage_table()
        self._reset_task_tree()
        workers = self._selected_workers()
        if self.coordinator is not None:
            self.coordinator.max_workers = workers
        self._append_log(f"Starting pipeline run ({workers} parallel AutoCAD job(s))...")
        self._run_started = time.monotonic()
        self.elapsed_var.set("⏱ 0:00")
        self.after(1000, self._tick_elapsed)
        self.progress_text_var.set("0%")

        self._cancel_event = threading.Event()
        self.context = self._new_context()
        self.context.set("autocad_listener", self.listener)
        self.context.set(CANCEL_EVENT_KEY, self._cancel_event)

        self.tabview.set("Tasks")

        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline_thread, daemon=True
        )
        self._pipeline_thread.start()

    def _cancel_pipeline(self) -> None:
        if not self._running or self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled", text="Cancelling…")
        self._set_status("Cancelling — waiting for AutoCAD jobs to stop…", kind="warning")
        self._append_log("Cancellation requested", level="warning")

    def _run_pipeline_thread(self) -> None:
        results = None
        try:
            results = self.pipeline.run(self.context, listener=self.listener)
        except Exception as exc:  # pragma: no cover - surfaced via GUI
            self.event_queue.put(ProgressEvent("pipeline_error", {"error": str(exc)}))
        finally:
            if self.logger_factory is not None:
                self.logger_factory.close()
            if results is not None:
                try:
                    write_manifest(self.context, results)
                    lines = build_summary_lines(self.context, results)
                except Exception:  # noqa: BLE001 - summary must not mask the run
                    lines = []
                self.event_queue.put(ProgressEvent("summary", {"lines": lines}))
            self.event_queue.put(ProgressEvent("pipeline_thread_complete", {}))

    # Event handling ------------------------------------------------------
    #: Events drained per tick. A burst from N parallel consoles must not hold
    #: the main loop for the length of the burst.
    _MAX_EVENTS_PER_TICK = 200

    def _process_events(self) -> None:
        """Drain queued events.

        Rearming happens in ``finally``: a handler that raises must never be
        able to stop the pump, or the window stays alive and responsive while
        silently never updating again.
        """

        try:
            for _ in range(self._MAX_EVENTS_PER_TICK):
                try:
                    event = self.event_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception as exc:  # noqa: BLE001 - one bad event, not the pump
                    self._report_event_error(event, exc)
        finally:
            self.after(100, self._process_events)

    def _report_event_error(self, event: ProgressEvent, exc: Exception) -> None:
        """Surface a handler failure instead of losing it."""

        try:
            self._append_log(
                f"Internal error handling {event.kind!r} event: {exc!r}", level="error"
            )
        except Exception:  # noqa: BLE001 - the log widget itself may be the failure
            pass

    def _update_progress(self) -> None:
        total_stages = len(self.stage_names)
        if total_stages == 0:
            return
        fraction = self.completed_stages / total_stages
        # While the AutoCAD stage runs, weight it by its job completion.
        if self.job_total > 0 and self.completed_stages < total_stages:
            fraction += (self.job_completed / self.job_total) / total_stages
        fraction = min(1.0, fraction)
        self.progress_bar.set(fraction)
        text = f"{int(fraction * 100)}%"
        if self.job_total > 0:
            text += f" · {self.job_completed}/{self.job_total} jobs"
        self.progress_text_var.set(text)

    def _handle_event(self, event: ProgressEvent) -> None:
        kind = event.kind
        payload = event.payload
        if kind == "stage_started":
            name = payload["name"]
            self._ensure_stage_row(name)
            self._set_stage_status(name, "running")
            self._set_status(f"Running stage {self._display_name(name)}", kind="running")
        elif kind == "stage_completed":
            name = payload["name"]
            self._ensure_stage_row(name)
            succeeded = payload.get("succeeded")
            status_text = "completed" if succeeded else "failed"
            self.completed_stages += 1
            self._set_stage_status(name, status_text, duration=payload.get("duration"))
            self._update_progress()

            if succeeded and payload.get("data"):
                self._handle_stage_data(name, payload["data"])
            if not succeeded and payload.get("details"):
                self._append_log(f"Stage {name} failed: {payload['details']}", level="error")
        elif kind == "pipeline_completed":
            results = payload.get("results", [])
            succeeded = bool(results) and all(item.get("succeeded", False) for item in results)
            cancelled = self._cancel_event is not None and self._cancel_event.is_set()
            if cancelled and not succeeded:
                self._set_status("Run cancelled", kind="warning")
                self._append_log("Pipeline run cancelled", level="warning")
            elif succeeded:
                self._set_status("Pipeline completed successfully", kind="ok")
                self._append_log("Pipeline completed successfully")
                self.progress_bar.set(1.0)
                self.progress_text_var.set("100%")
            else:
                self._set_status("Pipeline completed with errors", kind="error")
                self._append_log("Pipeline completed with errors", level="error")
        elif kind == "summary":
            lines = payload.get("lines") or []
            if lines:
                self._append_log("── Run summary " + "─" * 30)
                failed = False
                for line in lines:
                    is_bad = line.startswith(("  ✗", "Stage")) or "FAILED" in line
                    failed = failed or is_bad
                    self._append_log(line, level="error" if is_bad else "info")
                cancelled = self._cancel_event is not None and self._cancel_event.is_set()
                if failed and not cancelled:
                    messagebox.showerror("Run Failed", "\n".join(lines))
        elif kind == "pipeline_error":
            self._set_status("Pipeline crashed", kind="error")
            self._append_log(f"Pipeline crashed: {payload['error']}", level="error")
        elif kind == "pipeline_thread_complete":
            self._running = False
            self._cancel_event = None
            self.run_button.configure(state="normal")
            self.open_proj_btn.configure(state="normal")
            self.recent_menu.configure(state="normal")
            self.workers_menu.configure(state="normal")
            self.cancel_button.configure(state="disabled", text="■   Cancel Run")
        elif kind == "jobs_planned":
            # The full job set, known before the first batch starts. Fixing the
            # denominator here is what keeps the progress bar monotonic.
            self.job_total = int(payload.get("total") or 0)
            self._job_total_is_final = self.job_total > 0
            batch_summary = ", ".join(
                f"{len(batch.get('jobs') or [])} {batch.get('label')}"
                for batch in payload.get("batches") or []
                if batch.get("jobs")
            )
            self._append_log(f"Planned {self.job_total} AutoCAD job(s): {batch_summary}")
            self._update_progress()
        elif kind == "job_queued":
            job_name = payload["name"]
            script_path = payload.get("script") or ""
            script_display = Path(script_path).name if script_path else "—"
            self.job_states[job_name] = {"status": "Queued", "code": None}
            if not self._job_total_is_final:
                self.job_total += 1
            self._ensure_task_node(job_name)
            self._set_task_status(job_name, "queued", script=script_display)
            self._append_task_log(job_name, f"Queued (script: {script_display})")
            self._append_log(f"Queued job {job_name} (script: {script_display})")
        elif kind == "job_started":
            job_name = payload["name"]
            self.job_states[job_name] = {"status": "Running", "code": None}
            self._ensure_task_node(job_name)
            self._set_task_status(job_name, "running")
            self._append_task_log(job_name, "Running…")
            self._append_log(f"Running job {job_name}")
        elif kind == "job_output":
            job_name = payload["name"]
            line = payload.get("line", "")
            if line.strip():
                self._ensure_task_node(job_name)
                self._append_task_log(job_name, line)
        elif kind == "job_completed":
            job_name = payload["name"]
            succeeded = payload.get("succeeded")
            status_key = "completed" if succeeded else "failed"
            return_code = payload.get("returncode")
            duration = payload.get("duration")
            failure_reason = payload.get("failure_reason")
            self.job_states[job_name] = {
                "status": "Completed" if succeeded else "Failed",
                "code": return_code,
            }
            self._set_task_status(job_name, status_key, code=return_code, duration=duration)
            self.job_completed = min(self.job_total, self.job_completed + 1)
            self._update_progress()
            message = (
                f"Job {job_name} {'succeeded' if succeeded else 'failed'} "
                f"with code {return_code}"
                + (f" ({failure_reason})" if failure_reason else "")
                + (f" in {self._format_duration(duration)}" if duration else "")
            )
            self._append_task_log(job_name, message)
            self._append_log(message, level="info" if succeeded else "error")
            stderr = (payload.get("stderr") or "").strip()
            if stderr:
                self._append_task_log(job_name, stderr)
                self._append_log(stderr, level="info" if succeeded else "error")
        elif kind == "job_failed":
            job_name = payload["name"]
            error_message = payload.get("error", "")
            self.job_states[job_name] = {"status": "Failed", "code": None}
            self._set_task_status(job_name, "failed")
            self.job_completed = min(self.job_total, self.job_completed + 1)
            self._update_progress()
            self._append_task_log(job_name, f"Failed: {error_message}")
            self._append_log(f"Job {job_name} failed: {error_message}", level="error")
        elif kind == "log":
            level = payload.get("level", "INFO").upper()
            message = payload.get("message", "")
            display_level = (
                "error"
                if level in {"ERROR", "CRITICAL"}
                else "warning"
                if level == "WARNING"
                else "info"
            )
            self._append_log(f"[{level}] {message}", level=display_level)
        elif kind == "preflight":
            check = payload["check"]
            status = payload["status"]
            ok = payload["ok"]
            color = "gray70" if ok is None else ("#2fa572" if ok else "#e05a5a")
            if check == "autocad":
                self.preflight_autocad.set(status)
                self.lbl_acad_status.configure(text_color=color)
            elif check == "plugin":
                self.preflight_plugin.set(status)
                self.lbl_plugin_status.configure(text_color=color)
            elif check == "trusted":
                self.preflight_trusted.set(status)
                self.lbl_trust_status.configure(text_color=color)
                if payload.get("fixable"):
                    self._trusted_fix_target = Path(payload.get("target") or APP_ROOT)
                    self.fix_trusted_btn.configure(state="normal", text="Fix trusted path")
                    self.fix_trusted_btn.pack(anchor="w", padx=(10, 0), pady=(6, 0))
                    self._propose_trusted_fix()
                else:
                    self.fix_trusted_btn.pack_forget()
        elif kind == "update_available":
            self._update_info = payload
            latest = payload.get("latest", "?")
            self.update_button.configure(text=f"Update to v{latest}")
            self.update_button.grid(row=12, column=0, padx=20, pady=(0, 10))
            self._append_log(
                f"Update available: v{payload.get('current')} → v{latest} "
                f"({payload.get('url')})"
            )

    def _append_log(self, message: str, *, level: str = "info", error: bool = False) -> None:
        if error:
            level = "error"
        self.log_widget.configure(state="normal")
        tags = () if level == "info" else (level,)
        try:
            self.log_widget.insert(tk.END, message + "\n", tags)
        except Exception:  # pragma: no cover - tag support differences
            self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state="disabled")

    def run(self) -> None:
        self.mainloop()


def run_gui(
    *,
    settings_loader: Callable[[Path], Settings],
    environment_builder: Callable[[Settings], Environment],
    runner_factory: Callable[[Settings], AutoCadRunner],
    coordinator_factory: Callable[[AutoCadRunner], AutoCadCoordinator],
    logger_factory_builder: Callable[[Settings], LoggerFactory],
    initial_project: Path | None = None,
    autorun: bool = False,
    enable_update_check: bool = True,
) -> None:
    """Entry point used by the CLI to launch the GUI."""

    app = GuiApplication(
        settings_loader=settings_loader,
        environment_builder=environment_builder,
        runner_factory=runner_factory,
        coordinator_factory=coordinator_factory,
        logger_factory_builder=logger_factory_builder,
        initial_project=initial_project,
        autorun=autorun,
        enable_update_check=enable_update_check,
    )
    app.run()


__all__ = ["run_gui", "GuiApplication"]
