"""Tests for view logic that does not need a Tk window.

Like the event-pump tests, the real methods are bound to lightweight
stand-ins exposing only the state they touch.
"""
from dwgmagic.gui.views import logs_view
from dwgmagic.gui.views.logs_view import LogsView
from dwgmagic.gui.views.run_panel import RunPanel


class _FakeLogs:
    append = LogsView.append
    _passes = LogsView._passes

    def __init__(self):
        self._entries = []
        self._level = "all"
        self._query = ""
        self.rerenders = 0
        self.writes = 0

    def _rerender(self):
        self.rerenders += 1

    def _write(self, level, message):
        self.writes += 1


def test_full_log_pane_does_not_rerender_on_every_line():
    """Regression: once the pane held MAX_LINES, trimming one line per append
    re-rendered the whole pane for every new log line, freezing the window."""

    view = _FakeLogs()
    for i in range(logs_view.MAX_LINES + 2000):
        view.append(f"line {i}")

    assert len(view._entries) <= logs_view.MAX_LINES
    # One re-render per TRIM_CHUNK lines, not one per line.
    assert view.rerenders <= 2000 // logs_view.TRIM_CHUNK + 1
    assert view._entries[-1] == ("info", f"line {logs_view.MAX_LINES + 1999}")


class _FakePanel:
    _estimate_remaining = RunPanel._estimate_remaining

    def __init__(self, *, workers, total, done, durations):
        self._workers = workers
        self._total_jobs = total
        self._done_jobs = done
        self._durations = durations


def test_eta_accounts_for_parallel_jobs():
    """Regression: the ETA projected every remaining job back to back, so with
    8 parallel jobs it overstated the time left eightfold."""

    serial = _FakePanel(workers=1, total=20, done=4, durations=[60.0] * 4)
    parallel = _FakePanel(workers=8, total=20, done=4, durations=[60.0] * 4)

    assert serial._estimate_remaining() == 16 * 60.0
    assert parallel._estimate_remaining() == 2 * 60.0  # ceil(16 / 8) rounds


def test_eta_is_unknown_before_any_job_finishes():
    panel = _FakePanel(workers=4, total=10, done=0, durations=[])
    assert panel._estimate_remaining() is None
