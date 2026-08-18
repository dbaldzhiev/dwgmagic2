"""Tests for the GUI event pump.

Exercised without a Tk window: the real ``GuiApplication`` methods are bound to
a lightweight stand-in that supplies only what the pump touches. Constructing
the window would need a display, and the behaviour under test is the pump's
control flow, not any widget.

The defect being pinned: ``_process_events`` used to re-arm itself *after* the
drain loop, so a handler raising anything other than ``queue.Empty`` escaped
and the re-arm never ran. The window stayed alive and responsive, the pipeline
kept executing AutoCAD jobs, and the UI silently never updated again.
"""
import queue

import pytest

from dwgmagic.gui.app import GuiApplication
from dwgmagic.ui.progress import ProgressEvent


class FakePump:
    """Stand-in exposing only what ``_process_events`` uses."""

    _MAX_EVENTS_PER_TICK = GuiApplication._MAX_EVENTS_PER_TICK
    _process_events = GuiApplication._process_events
    _report_event_error = GuiApplication._report_event_error

    def __init__(self, handler=None):
        self.event_queue: queue.Queue[ProgressEvent] = queue.Queue()
        self.rearms = 0
        self.handled = []
        self.logged = []
        self._handler = handler

    def after(self, _delay, _callback):
        self.rearms += 1

    def _handle_event(self, event):
        self.handled.append(event)
        if self._handler is not None:
            self._handler(event)

    def _append_log(self, message, *, level="info", error=False):
        self.logged.append((level, message))


def test_pump_rearms_after_a_raising_handler():
    def explode(event):
        if event.kind == "bad":
            raise KeyError("name")

    pump = FakePump(explode)
    pump.event_queue.put(ProgressEvent("bad", {}))
    pump.event_queue.put(ProgressEvent("good", {}))

    pump._process_events()

    assert pump.rearms == 1, "the pump must re-arm even when a handler raises"
    # The bad event must not swallow the ones behind it.
    assert [event.kind for event in pump.handled] == ["bad", "good"]


def test_handler_failure_is_surfaced_not_swallowed():
    pump = FakePump(lambda event: 1 / 0)
    pump.event_queue.put(ProgressEvent("job_completed", {}))

    pump._process_events()

    assert pump.logged, "a handler failure must reach the Logs pane"
    level, message = pump.logged[-1]
    assert level == "error"
    assert "job_completed" in message
    assert "ZeroDivisionError" in message


def test_pump_rearms_when_the_queue_is_empty():
    pump = FakePump()
    pump._process_events()
    assert pump.rearms == 1
    assert pump.handled == []


def test_pump_caps_events_per_tick():
    """A burst from N parallel consoles must not hold the main loop."""

    pump = FakePump()
    total = FakePump._MAX_EVENTS_PER_TICK + 50
    for index in range(total):
        pump.event_queue.put(ProgressEvent("job_output", {"line": str(index)}))

    pump._process_events()

    assert len(pump.handled) == FakePump._MAX_EVENTS_PER_TICK
    assert pump.event_queue.qsize() == 50
    assert pump.rearms == 1


def test_logging_failure_cannot_break_the_pump():
    """The log widget may itself be the thing that is broken."""

    pump = FakePump(lambda event: 1 / 0)

    def _broken_log(*args, **kwargs):
        raise RuntimeError("log widget is gone")

    pump._append_log = _broken_log
    pump.event_queue.put(ProgressEvent("log", {}))

    pump._process_events()  # must not raise

    assert pump.rearms == 1


@pytest.mark.parametrize("failures", [1, 5, 25])
def test_pump_survives_repeated_failures(failures):
    pump = FakePump(lambda event: 1 / 0)
    for _ in range(failures):
        pump.event_queue.put(ProgressEvent("stage_started", {}))

    pump._process_events()

    assert len(pump.handled) == failures
    assert len(pump.logged) == failures
    assert pump.rearms == 1
