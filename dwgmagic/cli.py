"""Command-line entrypoint for the DWGMAGIC pipeline.

By default DWGMAGIC opens the GUI (optionally preloaded with a project path);
pass ``--cli`` for the headless console pipeline, which returns a non-zero
exit code when the run fails.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader
from rich.console import Console


def _make_console() -> Console:
    """Console that never crashes on legacy (non-UTF) Windows encodings."""

    try:
        encoding = getattr(sys.stdout, "encoding", None) or ""
        if "utf" not in encoding.lower() and hasattr(sys.stdout, "buffer"):
            wrapped = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding=encoding or "ascii",
                errors="replace",
                line_buffering=True,
            )
            return Console(file=wrapped)
    except Exception:  # noqa: BLE001 - fall back to the default console
        pass
    return Console()

import dwgmagic
from dwgmagic.core.context import ProjectConfig, ProjectContext
from dwgmagic.core.pipeline import CANCEL_EVENT_KEY, PipelineRunner
from dwgmagic.core.stages import build_default_stages
from dwgmagic.integrations.autocad import AutoCadCoordinator, AutoCadRunner
from dwgmagic.logger import LoggerFactory
from dwgmagic.manifest import build_summary_lines, write_manifest
from dwgmagic.miscutil import PREVIOUS_ARCHIVE_NAME, plan_run
from dwgmagic.settings import Settings, load_settings
from dwgmagic.ui.progress import ConsoleProgressListener

console = _make_console()


def build_environment(settings: Settings) -> Environment:
    """Construct a Jinja environment with resilient template discovery."""

    seen = set()
    search_paths = []

    def _add_path(path: Path) -> None:
        path_str = str(path)
        if path_str not in seen:
            seen.add(path_str)
            search_paths.append(path_str)

    for root in settings.resolve_template_roots():
        _add_path(root)
        _add_path(root / "templates")

    # Always include the templates bundled with the package as a fallback.
    bundled_templates = Path(dwgmagic.__file__).resolve().parent / "templates"
    _add_path(bundled_templates.parent)
    _add_path(bundled_templates)

    loaders = []
    if search_paths:
        loaders.append(FileSystemLoader(search_paths))

    try:  # pragma: no cover - exercised indirectly when package data is available
        loaders.append(PackageLoader("dwgmagic", "templates"))
    except Exception:
        pass

    if not loaders:
        raise RuntimeError("No template loaders could be configured")

    loader = loaders[0] if len(loaders) == 1 else ChoiceLoader(loaders)
    return Environment(loader=loader, trim_blocks=True, lstrip_blocks=True)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dwgmagic", description="DWGMAGIC Toolset"
    )
    parser.add_argument("path", nargs="?", help="Path to the project directory")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--config", type=Path, help="Optional configuration file", default=None)
    parser.add_argument(
        "--template-root",
        action="append",
        type=Path,
        dest="template_roots",
        help="Additional template search path (can be specified multiple times)",
    )
    parser.add_argument("--autocad-path", type=Path, help="Explicit path to accoreconsole.exe")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run the pipeline headless in the console (requires a project path)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help=argparse.SUPPRESS,  # legacy flag; the GUI is now the default
    )
    parser.add_argument(
        "--autorun",
        action="store_true",
        help="Start the pipeline automatically after the GUI loads the project",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip the GitHub release update check on GUI startup",
    )
    parser.add_argument(
        "--version", action="version", version=f"dwgmagic {dwgmagic.__version__}"
    )
    return parser.parse_args(argv)


def display_title_bar() -> None:
    version = f"v{dwgmagic.__version__}"
    console.print("╭──────────────────────────────────────────────╮", style="cyan")
    console.print(f"│              [bold magenta]DWGMAGIC[/bold magenta] [dim]{version:<8}[/dim]              │")
    console.print("├──────────────────────────────────────────────┤", style="cyan")
    console.print("│        [italic]TECTONICA - Dimitar Baldzhiev[/italic]         │")
    console.print("╰──────────────────────────────────────────────╯", style="cyan")


def _make_settings_loader(args: argparse.Namespace):
    def _load(root: Path) -> Settings:
        return load_settings(
            root,
            verbose=args.verbose,
            config_file=args.config,
            template_roots=args.template_roots,
            autocad_path=args.autocad_path,
        )

    return _load


def run_console(args: argparse.Namespace, project_root: Path) -> int:
    """Run the pipeline headless; returns the process exit code."""

    try:
        settings = _make_settings_loader(args)(project_root)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        return 2
    environment = build_environment(settings)

    logger_factory = LoggerFactory(settings)
    runner = AutoCadRunner(settings)
    coordinator = AutoCadCoordinator(runner)

    display_title_bar()

    if settings.verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, settings.log_level))
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger_factory = logger_factory.with_handlers(console_handler)

    stages = build_default_stages(environment, logger_factory, runner, coordinator)

    config = ProjectConfig(settings=settings, stages=[stage.name for stage in stages])
    context = ProjectContext(config=config, environment=environment)

    listener = ConsoleProgressListener(console)
    context.set("autocad_listener", listener)
    cancel_event = threading.Event()
    context.set(CANCEL_EVENT_KEY, cancel_event)

    _print_plan_warnings(project_root, settings)

    pipeline = PipelineRunner.from_iterable(stages)
    try:
        results, cancelled = _run_cancellable(pipeline, context, listener, cancel_event)
    finally:
        logger_factory.close()

    write_manifest(context, results, run_id=logger_factory.run_id)

    console.print()
    console.rule("[bold]Run summary")
    for line in build_summary_lines(context, results):
        console.print(line)

    if cancelled:
        console.print("[yellow]Run cancelled by user[/yellow]")
        return 130
    succeeded = bool(results) and all(result.succeeded for result in results)
    return 0 if succeeded else 1


def _print_plan_warnings(project_root: Path, settings: Settings) -> None:
    """Say what the run is about to delete; the CLI has no plan panel."""

    plan = plan_run(project_root, settings.log_dir)
    if plan.replaces_previous:
        console.print(
            "[yellow]New export detected:[/yellow] it replaces the previous sources, "
            f"which are kept in {PREVIOUS_ARCHIVE_NAME}."
        )
    if plan.unexpected_deletes:
        names = ", ".join(path.name for path in plan.unexpected_deletes)
        console.print(f"[yellow]This run removes files DWGMAGIC did not create:[/yellow] {names}")


def _run_cancellable(pipeline, context, listener, cancel_event):
    """Run the pipeline on a worker thread so Ctrl+C can actually cancel it.

    On the main thread, KeyboardInterrupt is raised inside the job pool's
    wait; the pool's ``__exit__`` then ran every queued job to completion
    before the cancel flag was ever set. Here Ctrl+C sets the flag, the runner
    kills the AutoCAD process trees and skips queued jobs, and the partial
    results still reach the manifest. Returns ``(results, cancelled)``.
    """

    outcome: dict = {}
    done = threading.Event()

    def _work() -> None:
        try:
            outcome["results"] = pipeline.run(context, listener=listener)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            outcome["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_work, name="dwgmagic-pipeline", daemon=True)
    worker.start()
    cancelled = False
    # Poll with sleep rather than Thread.join: a KeyboardInterrupt landing
    # inside join() can leave the thread looking finished while it still runs.
    while not done.is_set():
        try:
            time.sleep(0.1)
        except KeyboardInterrupt:
            if not cancelled:
                cancelled = True
                cancel_event.set()
                console.print(
                    "[yellow]Cancelling — stopping AutoCAD jobs and waiting "
                    "for them to exit…[/yellow]"
                )
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("results", []), cancelled


def run(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.path).resolve() if args.path else None

    if args.cli:
        if project_root is None:
            console.print("[red]Error:[/red] --cli requires a project directory path.")
            return 2
        return run_console(args, project_root)

    # GUI is the default front-end.
    from dwgmagic.gui.app import run_gui

    enable_update_check = not args.no_update_check and os.environ.get(
        "DWGMAGIC_CHECK_UPDATES", "1"
    ).lower() not in {"0", "false", "no", "off"}

    run_gui(
        settings_loader=_make_settings_loader(args),
        environment_builder=build_environment,
        runner_factory=lambda settings: AutoCadRunner(settings),
        coordinator_factory=lambda runner: AutoCadCoordinator(runner),
        logger_factory_builder=lambda settings: LoggerFactory(settings),
        initial_project=project_root,
        autorun=args.autorun,
        enable_update_check=enable_update_check,
    )
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    raise SystemExit(run(argv))


__all__ = ["main", "run", "run_console", "build_environment", "parse_args"]
