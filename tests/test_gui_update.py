"""Tests for the in-app update flow.

The defect being pinned: ``_apply_update`` called ``launch_updater()`` with no
``package_url`` argument, which is required and has no default. Clicking the
"Update" button in the GUI raised a ``TypeError`` inside a Tk callback, which
Tk's default callback-exception handling swallows (prints to stderr, which a
windowed build has nowhere to show) - so the button appeared to do nothing.
Separately, ``_run_update_check`` never put ``package_url`` on the event it
queued, so even a fixed ``_apply_update`` would have had nothing to launch
with.
"""
import queue

from dwgmagic.gui.app import GuiApplication
from dwgmagic.update import UpdateInfo


class _FakeAppForApply:
    """Stand-in exposing only what ``_apply_update`` touches."""

    _apply_update = GuiApplication._apply_update

    def __init__(self, update_info):
        self._running = False
        self._update_info = update_info
        self.closed = False

    def _on_close(self):
        self.closed = True


def test_apply_update_launches_updater_with_package_url(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dwgmagic.gui.app.launch_updater",
        lambda package_url, *, relaunch_gui=True, version=None, sha256=None: calls.append(
            (package_url, relaunch_gui, version, sha256)
        )
        or True,
    )
    monkeypatch.setattr("dwgmagic.gui.app.messagebox.showinfo", lambda *a, **k: None)

    app = _FakeAppForApply(
        {
            "package_url": "https://example.test/bundle.zip",
            "package_sha256": "ab" * 32,
            "latest": "9.9.9",
            "url": "https://example.test/rel",
        }
    )
    app._apply_update()

    assert calls == [("https://example.test/bundle.zip", True, "9.9.9", "ab" * 32)]
    assert app.closed is True


def test_apply_update_falls_back_to_browser_without_package_url(monkeypatch):
    def _explode(*a, **k):
        raise AssertionError("launch_updater must not be called without a package_url")

    monkeypatch.setattr("dwgmagic.gui.app.launch_updater", _explode)
    opened = []
    monkeypatch.setattr("dwgmagic.gui.app.webbrowser.open", lambda url: opened.append(url))

    app = _FakeAppForApply({"url": "https://example.test/rel"})
    app._apply_update()

    assert opened == ["https://example.test/rel"]
    assert app.closed is False


class _FakeAppForCheck:
    _update_checks_enabled = GuiApplication._update_checks_enabled
    _run_update_check = GuiApplication._run_update_check

    def __init__(self):
        self._update_check_enabled = True
        self.current_settings = None
        self.event_queue: "queue.Queue" = queue.Queue()


class _ImmediateThread:
    """Runs the target synchronously instead of on a real thread."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_run_update_check_event_carries_package_url(monkeypatch):
    monkeypatch.setattr("dwgmagic.gui.app.threading.Thread", _ImmediateThread)
    monkeypatch.setattr(
        "dwgmagic.gui.app.check_for_update",
        lambda: UpdateInfo(
            current="1.0.0",
            latest="2.0.0",
            url="https://example.test/rel",
            notes="",
            package_url="https://example.test/bundle.zip",
        ),
    )

    app = _FakeAppForCheck()
    app._run_update_check()

    event = app.event_queue.get_nowait()
    assert event.kind == "update_available"
    assert event.payload["package_url"] == "https://example.test/bundle.zip"
