"""Last-resort crash reporting for the windowed (console-less) build.

The GUI executable is built with ``console=False``, so anything written to
stderr is discarded by Windows. The project-scoped :class:`LoggerFactory` only
starts writing once a project is opened, which leaves startup failures with
nowhere to surface. Without this module an import error at launch shows the
user nothing at all: no window, no message, no log.
"""
from __future__ import annotations

import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import dwgmagic

_MB_ICONERROR = 0x10


def crash_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "dwgmagic2" / "logs" / "crash.log"


def _write_crash_log(text: str) -> Path | None:
    path = crash_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"\n{'=' * 70}\n{datetime.now().isoformat(timespec='seconds')}  ")
            fh.write(f"dwgmagic {dwgmagic.__version__}  python {sys.version}\n")
            fh.write(f"executable: {sys.executable}\n{'=' * 70}\n{text}\n")
        return path
    except OSError:
        return None


def _show_message_box(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "DWGMAGIC — startup failed", _MB_ICONERROR)
    except Exception:  # noqa: BLE001 - a failed message box must not mask the crash
        pass


def report(exc_type, exc_value, exc_traceback) -> None:
    """Persist a traceback and tell the user where to find it."""

    text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    log_path = _write_crash_log(text)

    summary = f"{exc_type.__name__}: {exc_value}"
    if log_path is not None:
        message = f"DWGMAGIC could not start.\n\n{summary}\n\nFull details:\n{log_path}"
    else:
        message = f"DWGMAGIC could not start.\n\n{summary}\n\n(The crash log could not be written.)"

    if sys.stderr is not None:
        try:
            sys.stderr.write(text)
        except Exception:  # noqa: BLE001 - stderr is absent in the windowed build
            pass
    else:
        _show_message_box(message)
        return

    # A frozen windowed build still has a stderr object, but nothing reads it.
    if getattr(sys, "frozen", False) and not _has_console():
        _show_message_box(message)


def _has_console() -> bool:
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:  # noqa: BLE001
        return False


def install() -> None:
    """Route otherwise-unhandled exceptions through :func:`report`."""

    sys.excepthook = report


__all__ = ["install", "report", "crash_log_path"]
