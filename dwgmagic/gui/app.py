"""The DWGMAGIC window: layout, wiring, and pipeline event dispatch.

Organised around the run lifecycle rather than by widget type — a project bar,
a run panel, one work hierarchy, and a result panel — with each zone owning its
own module under :mod:`dwgmagic.gui.views`. This file keeps only the window,
the wiring between those views and the pipeline, and the event pump.
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
from tkinter import filedialog, messagebox
from typing import Callable, Iterable, Mapping, Optional, Sequence

import customtkinter as ctk

try:  # Drag & drop is optional; the GUI must still work without the tkdnd binary.
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _DND_AVAILABLE = True
except Exception:  # pragma: no cover - depends on native library presence
    _DND_AVAILABLE = False

from jinja2 import Environment

import dwgmagic
from dwgmagic.classify import classify_dwg_files
from dwgmagic.core.context import ProjectConfig, ProjectContext
from dwgmagic.core.pipeline import CANCEL_EVENT_KEY, PipelineRunner, PipelineStage
from dwgmagic.core.stages import build_default_stages
from dwgmagic.gui import theme
from dwgmagic.gui.state import GuiState
from dwgmagic.gui.views.empty_state import EmptyState
from dwgmagic.gui.views.logs_view import LogsView
from dwgmagic.gui.views.project_bar import ProjectBar
from dwgmagic.gui.views.result_panel import ResultPanel
from dwgmagic.gui.views.run_panel import RunPanel
from dwgmagic.gui.views.work_view import WorkView
from dwgmagic.integrations.autocad import AutoCadCoordinator, AutoCadRunner
from dwgmagic.logger import LoggerFactory
from dwgmagic.manifest import build_manifest, build_summary_lines, write_manifest
from dwgmagic.miscutil import inspect_project, plan_run
from dwgmagic.settings import APP_ROOT, Settings
from dwgmagic.trusted_folder import TrustedFolderChecker, add_trusted_path
from dwgmagic.ui.progress import ProgressEvent, QueueProgressListener
from dwgmagic.update import check_for_update, launch_updater

ctk.set_default_color_theme("blue")

#: Share of the progress bar owned by the AutoCAD stage. It is ~95% of a real
#: run's wall clock; weighting it as one stage of four made the bar meaningless.
_AUTOCAD_WEIGHT = 0.85


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
                {"name": record.name, "level": record.levelname, "message": message},
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
    """The application window."""

    #: Events drained per tick. A burst from N parallel consoles must not hold
    #: the main loop for the length of the burst.
    _MAX_EVENTS_PER_TICK = 200

    #: How long to wait for AutoCAD jobs to die before closing anyway.
    _SHUTDOWN_GRACE_SECONDS = 30.0

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
        self.project_root: Path | None = None

        self.gui_state = GuiState.load()
        ctk.set_appearance_mode(self.gui_state.appearance)

        self.event_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self.log_handler = QueueLogHandler(self.event_queue)
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        self.listener = QueueProgressListener(self.event_queue)

        # Run state
        self.context: ProjectContext | None = None
        self._running = False
        self._cancel_event: threading.Event | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._shutting_down = False
        self._shutdown_deadline: float | None = None
        self._autorun_pending = autorun and initial_project is not None
        self._run_started: float | None = None
        self._update_info = None
        self._update_check_enabled = enable_update_check
        self._completed_stages = 0
        self._job_total = 0
        self._job_completed = 0
        self._job_total_is_final = False

        # Preflight
        self._preflight_cache: dict[tuple, dict] = {}
        self._trusted_fix_target: Path = APP_ROOT
        self.cpu_count = os.cpu_count() or 4

        self.title(f"DWGMAGIC v{dwgmagic.__version__}")
        self.geometry(self.gui_state.geometry)
        self.minsize(1040, 720)

        # Layout first: the menu's recent list refreshes the views it feeds.
        self._build_layout()
        self._build_menu()
        self._bind_shortcuts()

        self.after(100, self._process_events)
        self.after(400, self._run_startup_checks)
        self.after(1500, self._run_update_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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

    # -- construction ------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open Project…", accelerator="Ctrl+O", command=self._choose_project)
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Recent Projects", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Open Project Folder", command=self._open_project_folder)
        file_menu.add_command(label="Open Logs Folder", command=self._open_logs_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        run_menu = tk.Menu(menubar, tearoff=0)
        run_menu.add_command(label="Run", accelerator="F5", command=self._start_pipeline)
        run_menu.add_command(label="Cancel", accelerator="Esc", command=self._cancel_pipeline)
        run_menu.add_separator()
        run_menu.add_command(label="Re-check environment", command=self._recheck_preflight)
        menubar.add_cascade(label="Run", menu=run_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        help_menu.add_command(label="Open crash log", command=self._open_crash_log)
        help_menu.add_command(
            label="Project on GitHub",
            command=lambda: webbrowser.open("https://github.com/dbaldzhiev/dwgmagic2"),
        )
        menubar.add_cascade(label="Help", menu=help_menu)

        try:
            self.configure(menu=menubar)
        except tk.TclError:  # pragma: no cover
            pass
        self._refresh_recent_menu()

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.project_bar = ProjectBar(
            self,
            on_open=self._choose_project,
            on_recent=self._open_recent,
            on_recheck=self._recheck_preflight,
            on_fix_trusted=self._apply_trusted_fix,
            on_reveal=self._open_project_folder,
        )
        self.project_bar.grid(row=0, column=0, sticky="ew")

        self.run_panel = RunPanel(
            self,
            on_run=self._start_pipeline,
            on_cancel=self._cancel_pipeline,
            on_options=self._show_options,
        )

        self.result_panel = ResultPanel(self, on_show_job=self._focus_job)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.add("Work")
        self.tabview.add("Logs")
        for tab in ("Work", "Logs"):
            self.tabview.tab(tab).grid_columnconfigure(0, weight=1)
            self.tabview.tab(tab).grid_rowconfigure(0, weight=1)

        self.work_view = WorkView(
            self.tabview.tab("Work"), job_log_resolver=self._job_log_path
        )
        self.work_view.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.logs_view = LogsView(
            self.tabview.tab("Logs"), run_log_resolver=self._run_log_path
        )
        self.logs_view.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.empty_state = EmptyState(
            self,
            on_open=self._choose_project,
            on_recent=self._open_recent,
            dnd_available=_DND_AVAILABLE,
        )
        self.empty_state.set_recent(self.gui_state.recent_projects)

        # Appearance control lives in the bottom strip now that the sidebar is gone.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        footer.grid_columnconfigure(0, weight=1)
        self.update_button = ctk.CTkButton(
            footer,
            text="Update available",
            width=150,
            height=28,
            fg_color=theme.color("warning"),
            hover_color=theme.color("warning.hover"),
            command=self._apply_update,
        )
        self.appearance_menu = ctk.CTkOptionMenu(
            footer, values=["System", "Light", "Dark"], width=110,
            command=self._change_appearance_mode,
        )
        self.appearance_menu.set(self.gui_state.appearance)
        self.appearance_menu.grid(row=0, column=1, sticky="e")

        self._show_empty_state(True)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _e: self._choose_project())
        self.bind("<F5>", lambda _e: self._start_pipeline())
        self.bind("<Escape>", lambda _e: self._cancel_pipeline())

    def _show_empty_state(self, empty: bool) -> None:
        if empty:
            self.run_panel.grid_forget()
            self.result_panel.grid_forget()
            self.tabview.grid_forget()
            self.empty_state.grid(row=3, column=0, sticky="nsew", padx=14, pady=10)
        else:
            self.empty_state.grid_forget()
            self.run_panel.grid(row=1, column=0, sticky="ew", padx=14, pady=(10, 8))
            self.tabview.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 8))

    # -- appearance --------------------------------------------------------
    def _change_appearance_mode(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self.gui_state.appearance = mode
        self.gui_state.save()
        # get_appearance_mode() resolves "System"; restyle after the switch.
        self.after(50, self.work_view.apply_theme)

    # -- project loading ---------------------------------------------------
    def _choose_project(self) -> None:
        if self._guard_running():
            return
        path = filedialog.askdirectory(title="Select DWGMAGIC Project")
        if not path:
            return
        try:
            self._load_project(Path(path).resolve())
        except Exception as exc:  # pragma: no cover - surfaced via GUI
            messagebox.showerror("Project Load Failed", str(exc))

    def _open_recent(self, value: str) -> None:
        if self._guard_running():
            return
        path = Path(value)
        if not path.exists():
            messagebox.showerror("Not Found", f"{path} no longer exists.")
            self.gui_state.recent_projects = [
                item for item in self.gui_state.recent_projects if item != value
            ]
            self.gui_state.save()
            self._refresh_recent_menu()
            return
        try:
            self._load_project(path)
        except Exception as exc:
            messagebox.showerror("Project Load Failed", str(exc))

    def _guard_running(self) -> bool:
        if self._running:
            messagebox.showwarning(
                "Pipeline Running",
                "Wait for the current run to finish before switching projects.",
            )
            return True
        return False

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
        self.project_root = project_root

        self.stages = build_default_stages(environment, logger_factory, runner, coordinator)
        self.stage_names = [stage.name for stage in self.stages]
        self.pipeline = PipelineRunner.from_iterable(self.stages)

        self.title(f"DWGMAGIC v{dwgmagic.__version__} — {project_root.name}")
        self._show_empty_state(False)
        self.result_panel.grid_forget()
        self.project_bar.set_project(project_root)
        self.work_view.reset(self.stage_names)
        self._append_log(f"Loaded project at {project_root}")

        self._refresh_plan()
        self.gui_state.remember_project(project_root)
        self.gui_state.save()
        self._refresh_recent_menu()
        self._run_project_checks(settings)

        if self._autorun_pending:
            self._autorun_pending = False
            self._append_log("Autorun requested — starting pipeline")
            self.after(700, self._start_pipeline)

    def _refresh_plan(self) -> None:
        """Show what a run would create and destroy, before the click."""

        if self.project_root is None:
            return
        plan = plan_run(self.project_root)
        inspection = inspect_project(self.project_root)
        classified = classify_dwg_files(inspection.dwg_names)
        orphans = [
            view
            for view in classified.views
            if not any(
                view in views for views in classified.sheet_views_lookup.values()
            )
        ]
        self.project_bar.set_content(
            dwg=len(inspection.dwg_names),
            sheets=len(classified.sheets),
            views=len(classified.views),
            ignored=classified.ignored,
            orphans=orphans,
            mode=inspection.mode,
        )
        self.run_panel.show_plan(plan)
        if not inspection.is_project:
            self.run_panel.set_enabled(
                False,
                reason=f"Not a DWGMAGIC project — {inspection.describe()}",
            )
        else:
            self.run_panel.set_enabled(True)

    def _refresh_recent_menu(self) -> None:
        self.project_bar.set_recent(self.gui_state.recent_projects)
        self.empty_state.set_recent(self.gui_state.recent_projects)
        try:
            self.recent_menu.delete(0, tk.END)
            if not self.gui_state.recent_projects:
                self.recent_menu.add_command(label="(none)", state="disabled")
            for entry in self.gui_state.recent_projects:
                self.recent_menu.add_command(
                    label=entry, command=lambda p=entry: self._open_recent(p)
                )
        except tk.TclError:  # pragma: no cover
            pass

    # -- preflight ---------------------------------------------------------
    def _settings_fingerprint(self, settings: Settings) -> tuple:
        return (str(settings.autocad_executable), str(settings.tectonica_path))

    def _run_startup_checks(self) -> None:
        """Preflight before any project is open.

        Routed through ``settings_loader`` so environment and config overrides
        (``DWGMAGIC_AUTOCAD_PATH``, ``DWGMAGIC_TECTONICA_PATH``) are honoured —
        constructing a bare Settings here made preflight disagree with the run.
        """

        try:
            settings = self.settings_loader(APP_ROOT)
        except Exception:  # noqa: BLE001 - fall back rather than block startup
            settings = Settings(project_root=APP_ROOT)
        self._run_preflight(settings)

    def _run_project_checks(self, settings: Settings) -> None:
        self._run_preflight(settings)

    def _recheck_preflight(self) -> None:
        self._preflight_cache.clear()
        settings = self.current_settings
        if settings is None:
            self._run_startup_checks()
        else:
            self._run_preflight(settings)

    def _run_preflight(self, settings: Settings) -> None:
        """Check the environment, reusing a cached result for the same config.

        Each of startup, project load and stage 1 used to launch accoreconsole
        — a multi-second cold start — to prove the same fact.
        """

        fingerprint = self._settings_fingerprint(settings)
        cached = self._preflight_cache.get(fingerprint)
        if cached is not None:
            for check, payload in cached.items():
                self.project_bar.set_check(check, **payload)
            return

        for check in ("autocad", "plugin", "trusted"):
            self.project_bar.set_check(check, "Checking…", None)

        def _check() -> None:
            results: dict[str, dict] = {}

            runner = self.runner_factory(settings)
            try:
                exe = runner.discover()
                results["autocad"] = {"status": f"Found: {exe}", "ok": True}
            except Exception:
                results["autocad"] = {
                    "status": "Not found (install AutoCAD or set --autocad-path)",
                    "ok": False,
                }

            dll = settings.tectonica_path / "tectonica.dll"
            results["plugin"] = {
                "status": "Present" if dll.exists() else f"Missing: {dll}",
                "ok": dll.exists(),
            }

            if results["autocad"]["ok"] and dll.exists():
                try:
                    TrustedFolderChecker(runner).check(settings, logging.getLogger("Preflight"))
                    results["trusted"] = {
                        "status": f"OK — {settings.tectonica_path}", "ok": True
                    }
                except Exception:
                    results["trusted"] = {
                        "status": f"AutoCAD does not trust {settings.tectonica_path}",
                        "ok": False,
                        "fixable": True,
                        "target": str(settings.tectonica_path),
                    }
            else:
                results["trusted"] = {"status": "Skipped (see above)", "ok": None}

            self.event_queue.put(
                ProgressEvent("preflight_done", {"fingerprint": fingerprint, "results": results})
            )

        threading.Thread(target=_check, daemon=True).start()

    def _apply_trusted_fix(self) -> None:
        target = self._trusted_fix_target
        self.project_bar.fix_button.configure(state="disabled", text="Fixing…")

        def _fix() -> None:
            try:
                modified = add_trusted_path(target)
                message = (
                    f"Added {target} to TRUSTEDPATHS of {len(modified)} AutoCAD profile(s)"
                    if modified
                    else f"{target} was already trusted by every profile"
                )
                self.event_queue.put(
                    ProgressEvent("log", {"name": "Preflight", "level": "INFO", "message": message})
                )
            except Exception as exc:
                self.event_queue.put(
                    ProgressEvent(
                        "log",
                        {"name": "Preflight", "level": "ERROR", "message": f"Fix failed: {exc}"},
                    )
                )
            self.event_queue.put(ProgressEvent("trusted_fix_done", {}))

        threading.Thread(target=_fix, daemon=True).start()

    # -- run control -------------------------------------------------------
    def _start_pipeline(self) -> None:
        if self._running or self.pipeline is None or self.current_settings is None:
            return
        project_root = self.current_settings.project_root
        inspection = inspect_project(project_root)
        if not inspection.is_project:
            self._append_log(f"Cannot run: {inspection.describe()}", level="error")
            return
        if inspection.first_run:
            self._append_log(f"First run in {project_root} — {inspection.describe()}")

        self._running = True
        self._completed_stages = 0
        self._job_total = 0
        self._job_completed = 0
        self._job_total_is_final = False
        self._run_started = time.monotonic()

        self.result_panel.grid_forget()
        self.work_view.reset(self.stage_names)
        self.work_view.set_filter("all")
        self.run_panel.begin_run()
        self.project_bar.open_button.configure(state="disabled")
        self.tabview.set("Work")

        workers = self.gui_state.max_workers or self.cpu_count
        workers = max(1, min(workers, self.cpu_count))
        if self.coordinator is not None:
            self.coordinator.max_workers = workers
        self._append_log(f"Starting pipeline run ({workers} parallel AutoCAD job(s))…")

        self._cancel_event = threading.Event()
        self.context = self._new_context()
        self.context.set("autocad_listener", self.listener)
        self.context.set(CANCEL_EVENT_KEY, self._cancel_event)

        self._pipeline_thread = threading.Thread(target=self._run_pipeline_thread, daemon=True)
        self._pipeline_thread.start()
        self.after(1000, self._tick)

    def _new_context(self) -> ProjectContext:
        if not self.current_settings or not self.environment:
            raise RuntimeError("Project configuration has not been loaded")
        config = ProjectConfig(settings=self.current_settings, stages=self.stage_names)
        return ProjectContext(config=config, environment=self.environment)

    def _cancel_pipeline(self) -> None:
        if not self._running or self._cancel_event is None:
            return
        self._cancel_event.set()
        self.run_panel.set_cancelling("Cancelling — waiting for AutoCAD jobs to stop…")
        self._append_log("Cancellation requested", level="warning")

    def _run_pipeline_thread(self) -> None:
        results = None
        manifest_path = None
        try:
            results = self.pipeline.run(self.context, listener=self.listener)
        except Exception as exc:  # pragma: no cover - surfaced via GUI
            self.event_queue.put(ProgressEvent("pipeline_error", {"error": str(exc)}))
        finally:
            if self.logger_factory is not None:
                self.logger_factory.close()
            manifest = None
            if results is not None:
                try:
                    manifest_path = write_manifest(self.context, results)
                    manifest = build_manifest(self.context, results)
                except Exception:  # noqa: BLE001 - a bad summary must not mask the run
                    manifest = None
                for line in self._safe_summary(results):
                    self.event_queue.put(
                        ProgressEvent("log", {"level": "INFO", "message": line, "name": "SUMMARY"})
                    )
            self.event_queue.put(
                ProgressEvent(
                    "pipeline_thread_complete",
                    {
                        "manifest": manifest,
                        "manifest_path": str(manifest_path) if manifest_path else None,
                    },
                )
            )

    def _safe_summary(self, results) -> list[str]:
        try:
            return build_summary_lines(self.context, results)
        except Exception:  # noqa: BLE001
            return []

    def _tick(self) -> None:
        if not self._running:
            return
        self.run_panel.tick()
        self.after(1000, self._tick)

    # -- event pump --------------------------------------------------------
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

    def _handle_event(self, event: ProgressEvent) -> None:
        kind = event.kind
        payload = event.payload

        if kind == "stage_started":
            self.work_view.set_stage_status(payload["name"], "running")
            self.run_panel.set_phase(f"Running {payload['name'].replace('_', ' ')}")
        elif kind == "stage_completed":
            name = payload["name"]
            succeeded = bool(payload.get("succeeded"))
            self._completed_stages += 1
            self.work_view.set_stage_status(
                name,
                "completed" if succeeded else "failed",
                duration=payload.get("duration"),
                detail="" if succeeded else str(payload.get("details") or "")[:60],
            )
            if succeeded and payload.get("data"):
                self._handle_stage_data(name, payload["data"])
            if not succeeded and payload.get("details"):
                self._append_log(f"Stage {name} failed: {payload['details']}", level="error")
            self._update_progress()
        elif kind == "jobs_planned":
            self._job_total = int(payload.get("total") or 0)
            self._job_total_is_final = self._job_total > 0
            self.work_view.set_plan(payload.get("batches") or [])
            self.run_panel.set_plan_total(self._job_total)
            summary = ", ".join(
                f"{len(b.get('jobs') or [])} {b.get('label')}"
                for b in payload.get("batches") or []
                if b.get("jobs")
            )
            self._append_log(f"Planned {self._job_total} AutoCAD job(s): {summary}")
            self._update_progress()
        elif kind == "job_queued":
            if not self._job_total_is_final:
                self._job_total += 1
            self.work_view.set_job_status(payload["name"], "queued")
        elif kind == "job_started":
            self.work_view.set_job_status(payload["name"], "running")
        elif kind == "job_output":
            line = payload.get("line", "")
            if line.strip():
                self.work_view.append_output(payload["name"], line)
        elif kind == "job_completed":
            name = payload["name"]
            succeeded = bool(payload.get("succeeded"))
            duration = payload.get("duration")
            self.work_view.set_job_status(
                name,
                "completed" if succeeded else "failed",
                code=payload.get("returncode"),
                duration=duration,
            )
            self._job_completed = min(self._job_total or self._job_completed + 1, self._job_completed + 1)
            self.run_panel.record_job(duration)
            reason = payload.get("failure_reason")
            message = (
                f"Job {name} {'succeeded' if succeeded else 'failed'} "
                f"with code {payload.get('returncode')}"
                + (f" ({reason})" if reason else "")
            )
            self._append_log(message, level="info" if succeeded else "error")
            stderr = (payload.get("stderr") or "").strip()
            if stderr:
                self.work_view.append_output(name, stderr)
            self._update_progress()
        elif kind == "job_failed":
            self.work_view.set_job_status(payload["name"], "failed")
            self._job_completed += 1
            self._append_log(
                f"Job {payload['name']} failed: {payload.get('error', '')}", level="error"
            )
            self._update_progress()
        elif kind == "pipeline_completed":
            pass  # the result panel is driven by the manifest on thread completion
        elif kind == "pipeline_error":
            self._append_log(f"Pipeline crashed: {payload['error']}", level="error")
        elif kind == "pipeline_thread_complete":
            self._finish_run(payload)
        elif kind == "log":
            level = str(payload.get("level", "INFO")).upper()
            display = (
                "error" if level in {"ERROR", "CRITICAL"}
                else "warning" if level == "WARNING"
                else "info"
            )
            self._append_log(payload.get("message", ""), level=display)
        elif kind == "preflight_done":
            self._preflight_cache[tuple(payload["fingerprint"])] = payload["results"]
            for check, result in payload["results"].items():
                self.project_bar.set_check(check, **result)
            trusted = payload["results"].get("trusted", {})
            if trusted.get("fixable"):
                self._trusted_fix_target = Path(trusted.get("target") or APP_ROOT)
                # An inline banner in the expanded detail, not a modal fired
                # over whatever the user is doing seconds after launch.
                self.project_bar.expand_detail()
        elif kind == "trusted_fix_done":
            self.project_bar.fix_button.configure(state="normal", text="Fix trusted path")
            self._recheck_preflight()
        elif kind == "update_available":
            self._update_info = payload
            self.update_button.configure(text=f"Update to v{payload.get('latest', '?')}")
            self.update_button.grid(row=0, column=0, sticky="w")
            self._append_log(
                f"Update available: v{payload.get('current')} → v{payload.get('latest')}"
            )

    def _handle_stage_data(self, stage_name: str, data: Mapping[str, object]) -> None:
        if stage_name == "preprocess":
            for key, label in (
                ("ignored", "Ignored files (no matching convention)"),
                ("orphan_views", "Views without a matching sheet"),
            ):
                values = data.get(key) or []
                if values and isinstance(values, Iterable):
                    self._append_log(
                        f"{label}: {', '.join(map(str, values))}", level="warning"
                    )

    def _finish_run(self, payload: Mapping[str, object]) -> None:
        self._running = False
        cancelled = self._cancel_event is not None and self._cancel_event.is_set()
        self._cancel_event = None
        self.run_panel.end_run()
        self.project_bar.open_button.configure(state="normal")
        self.work_view.mark_remaining_skipped()

        elapsed = time.monotonic() - self._run_started if self._run_started else None
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            manifest_path = payload.get("manifest_path")
            self.result_panel.show(
                manifest,
                elapsed=elapsed,
                cancelled=cancelled,
                manifest_path=Path(str(manifest_path)) if manifest_path else None,
                logs_dir=self._logs_dir(),
            )
            self.result_panel.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
            succeeded = bool(manifest.get("succeeded")) and not cancelled
        else:
            succeeded = False

        if cancelled:
            self.run_panel.set_phase("Run cancelled")
        elif succeeded:
            self.run_panel.set_phase("Run completed")
        else:
            self.run_panel.set_phase("Run failed")

        if self._shutting_down:
            self._save_state_and_destroy()

    def _update_progress(self) -> None:
        """Weight the AutoCAD stage by its real share of the run."""

        stages = len(self.stage_names) or 1
        other_stages = max(1, stages - 1)
        non_autocad_share = (1.0 - _AUTOCAD_WEIGHT) / other_stages
        completed_non_autocad = min(self._completed_stages, other_stages)
        fraction = completed_non_autocad * non_autocad_share
        if self._job_total:
            fraction += _AUTOCAD_WEIGHT * (self._job_completed / self._job_total)
        detail = f"{self._job_completed}/{self._job_total} jobs" if self._job_total else ""
        self.run_panel.set_progress(min(1.0, fraction), detail)

    # -- helpers -----------------------------------------------------------
    def _logs_dir(self) -> Optional[Path]:
        if self.current_settings is None:
            return None
        return self.current_settings.project_root / self.current_settings.log_dir

    def _job_log_path(self, job_name: str) -> Optional[Path]:
        logs = self._logs_dir()
        if logs is None:
            return None
        # Dumps are named by script stem, which drops the job's "view:"/"sheet:" prefix.
        stem = job_name.split(":", 1)[-1]
        for candidate in (stem, stem.upper(), f"{stem.upper()}_SHEET", f"{stem.upper()}-VIEW"):
            path = logs / "jobs" / f"{candidate}.out.txt"
            if path.exists():
                return path
        matches = sorted((logs / "jobs").glob(f"*{stem}*.out.txt")) if (logs / "jobs").exists() else []
        return matches[0] if matches else None

    def _run_log_path(self) -> Optional[Path]:
        logs = self._logs_dir()
        if logs is None or not logs.exists():
            return None
        candidates = sorted(logs.glob("run_*.log"), reverse=True)
        return candidates[0] if candidates else None

    def _open_project_folder(self) -> None:
        from dwgmagic.gui.widgets import open_path

        if self.project_root is not None:
            open_path(self.project_root)

    def _open_logs_folder(self) -> None:
        from dwgmagic.gui.widgets import open_path

        logs = self._logs_dir()
        if logs is not None and logs.exists():
            open_path(logs)

    def _open_crash_log(self) -> None:
        from dwgmagic.crashlog import crash_log_path
        from dwgmagic.gui.widgets import open_path

        path = crash_log_path()
        if path.exists():
            open_path(path)
        else:
            messagebox.showinfo("Crash log", f"No crash log yet.\n\nIt would be written to:\n{path}")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About DWGMAGIC",
            f"DWGMAGIC v{dwgmagic.__version__}\n"
            "TECTONICA — Dimitar Baldzhiev\n\n"
            f"Application folder:\n{APP_ROOT}",
        )

    def _show_options(self) -> None:
        """Per-run options. Persisted per machine in GuiState."""

        window = ctk.CTkToplevel(self)
        window.title("Options")
        window.geometry("340x200")
        window.transient(self)

        ctk.CTkLabel(
            window, text="Parallel AutoCAD jobs", font=ctk.CTkFont(size=12, weight="bold")
        ).pack(anchor="w", padx=16, pady=(16, 4))
        menu = ctk.CTkOptionMenu(
            window, values=[str(i) for i in range(1, self.cpu_count + 1)], width=100
        )
        chosen = self.gui_state.max_workers or self.cpu_count
        menu.set(str(max(1, min(chosen, self.cpu_count))))
        menu.pack(anchor="w", padx=16)
        ctk.CTkLabel(
            window,
            text=f"{self.cpu_count} CPUs available",
            font=ctk.CTkFont(size=10),
            text_color=theme.color("text.muted"),
        ).pack(anchor="w", padx=16, pady=(2, 12))

        def _save() -> None:
            try:
                workers = max(1, min(int(menu.get()), self.cpu_count))
            except ValueError:
                workers = self.cpu_count
            self.gui_state.max_workers = None if workers == self.cpu_count else workers
            self.gui_state.save()
            window.destroy()

        ctk.CTkButton(window, text="Save", command=_save, width=100).pack(anchor="e", padx=16)

    def _focus_job(self, job_name: str) -> None:
        self.tabview.set("Work")
        node = f"job:{job_name}"
        try:
            if self.work_view.tree.exists(node):
                self.work_view.tree.selection_set(node)
                self.work_view.tree.see(node)
        except tk.TclError:  # pragma: no cover
            pass

    def _append_log(self, message: str, *, level: str = "info", error: bool = False) -> None:
        if error:
            level = "error"
        self.logs_view.append(message, level)

    # -- drag & drop -------------------------------------------------------
    def _on_drop(self, event) -> None:
        if self._guard_running():
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

    # -- updates -----------------------------------------------------------
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
                        {"latest": info.latest, "current": info.current, "url": info.url},
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
        if launch_updater(relaunch_gui=True):
            messagebox.showinfo(
                "Updating",
                "The updater has been started. DWGMAGIC will close now and "
                "reopen once the update finishes.",
            )
            self._on_close()
        else:
            webbrowser.open(
                info.get("url", "https://github.com/dbaldzhiev/dwgmagic2/releases")
            )

    # -- shutdown ----------------------------------------------------------
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
        self.run_panel.set_cancelling()
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
