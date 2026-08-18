"""Tests for the startup crash reporter.

This is the safety net the whole dwgmagic2 rewrite exists to provide: in the
windowed build a startup failure has nowhere else to surface, so a regression
here reintroduces the original "nothing happens at all" bug.
"""
import sys

import dwgmagic
from dwgmagic import crashlog


def test_crash_log_path_lives_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = crashlog.crash_log_path()
    assert path == tmp_path / "dwgmagic2" / "logs" / "crash.log"


def test_report_writes_traceback_and_context(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    boxes = []
    monkeypatch.setattr(crashlog, "_show_message_box", boxes.append)

    try:
        raise RuntimeError("tkinter is missing")
    except RuntimeError:
        crashlog.report(*sys.exc_info())

    body = crashlog.crash_log_path().read_text(encoding="utf-8")
    assert "RuntimeError: tkinter is missing" in body
    assert "Traceback" in body
    # Version and interpreter identify which build failed.
    assert dwgmagic.__version__ in body


def test_report_appends_rather_than_truncating(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(crashlog, "_show_message_box", lambda message: None)

    for message in ("first failure", "second failure"):
        try:
            raise ValueError(message)
        except ValueError:
            crashlog.report(*sys.exc_info())

    body = crashlog.crash_log_path().read_text(encoding="utf-8")
    assert "first failure" in body
    assert "second failure" in body


def test_report_shows_message_box_when_stderr_is_absent(monkeypatch, tmp_path):
    """The windowed build discards stderr; a message box is the only channel."""

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    boxes = []
    monkeypatch.setattr(crashlog, "_show_message_box", boxes.append)
    monkeypatch.setattr(sys, "stderr", None)

    try:
        raise RuntimeError("no console here")
    except RuntimeError:
        crashlog.report(*sys.exc_info())

    assert len(boxes) == 1
    assert "no console here" in boxes[0]
    assert str(crashlog.crash_log_path()) in boxes[0]


def test_report_survives_an_unwritable_log_location(monkeypatch):
    """A failed crash log must still produce a visible message."""

    monkeypatch.setattr(crashlog, "_write_crash_log", lambda text: None)
    boxes = []
    monkeypatch.setattr(crashlog, "_show_message_box", boxes.append)
    monkeypatch.setattr(sys, "stderr", None)

    try:
        raise OSError("disk full")
    except OSError:
        crashlog.report(*sys.exc_info())

    assert len(boxes) == 1
    assert "could not be written" in boxes[0]


def test_install_routes_unhandled_exceptions(monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    crashlog.install()
    assert sys.excepthook is crashlog.report
