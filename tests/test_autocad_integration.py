import io
import threading
from types import SimpleNamespace

import pytest

from dwgmagic.errors import AutoCadNotFoundError
from dwgmagic.integrations.autocad import (
    AutoCadCoordinator,
    AutoCadJob,
    AutoCadResult,
    AutoCadRunner,
)
from dwgmagic.settings import Settings


def _quiet_logger():
    return SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )


class FakePopen:
    """Immediately-completing stand-in for subprocess.Popen."""

    last_instance = None

    def __init__(
        self,
        command,
        stdout=None,
        stderr=None,
        text=None,
        encoding=None,
        errors=None,
        cwd=None,
        **kwargs,
    ):
        FakePopen.last_instance = self
        self.command = command
        self.cwd = cwd
        self.stdout = io.StringIO(getattr(FakePopen, "stdout_body", "console line\n"))
        self.stderr = io.StringIO(getattr(FakePopen, "stderr_body", ""))
        self.returncode = getattr(FakePopen, "exit_code", 0)
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class HangingPopen(FakePopen):
    """Popen fake that never finishes until killed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.fixture(autouse=True)
def _reset_fake_popen():
    FakePopen.exit_code = 0
    FakePopen.stdout_body = "console line\n"
    FakePopen.stderr_body = ""
    yield


def test_runner_builds_command_with_input(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings)

    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", FakePopen)

    script = tmp_path / "script.scr"
    script.write_text("script")
    input_dwg = tmp_path / "input.dwg"
    input_dwg.write_text("dwg")

    result = runner.run_script(
        script_path=script, input_path=input_dwg, logger=_quiet_logger()
    )
    assert result.returncode == 0
    assert result.succeeded is True
    assert FakePopen.last_instance.command == [
        str(executable),
        "/i",
        str(input_dwg),
        "/s",
        str(script),
    ]
    assert result.command == tuple(FakePopen.last_instance.command)
    assert FakePopen.last_instance.cwd == str(tmp_path)
    # Raw console output is dumped for post-mortem debugging.
    dump = tmp_path / "logs" / "jobs" / "script.out.txt"
    assert dump.exists()
    assert "console line" in dump.read_text(encoding="utf-8")


def test_runner_detects_failure_marker_despite_zero_exit(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings)

    FakePopen.stdout_body = "Regenerating model.\nUnknown command \"TECARXREF\".\n"
    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", FakePopen)

    script = tmp_path / "script.scr"
    script.write_text("script")
    result = runner.run_script(script_path=script, logger=_quiet_logger())

    assert result.returncode == 0
    assert result.succeeded is False
    assert "Unknown command" in (result.failure_reason or "")


def test_runner_ignores_benign_unknown_command_spill(tmp_path, monkeypatch):
    """XREF token spill in no-xref drawings must not fail the job (see markers doc)."""

    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings)

    FakePopen.stdout_body = (
        "No matching xref names found.\n"
        'Command: r\n'
        'Unknown command "R".  Press F1 for help.\n'
    )
    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", FakePopen)

    script = tmp_path / "script.scr"
    script.write_text("script")
    result = runner.run_script(script_path=script, logger=_quiet_logger())

    assert result.succeeded is True
    assert result.failure_reason is None


def test_runner_streams_output_to_callback(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings)

    FakePopen.stdout_body = "first\nsecond\n"
    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", FakePopen)

    script = tmp_path / "script.scr"
    script.write_text("script")
    seen: list[str] = []
    runner.run_script(
        script_path=script, logger=_quiet_logger(), output_callback=seen.append
    )
    assert seen == ["first", "second"]


def test_runner_kills_job_on_timeout(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings, timeout=0.3)

    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", HangingPopen)

    script = tmp_path / "script.scr"
    script.write_text("script")
    result = runner.run_script(script_path=script, logger=_quiet_logger())

    assert result.succeeded is False
    assert "timed out" in (result.failure_reason or "")
    assert FakePopen.last_instance.killed is True


def test_runner_kills_job_on_cancel(tmp_path, monkeypatch):
    executable = tmp_path / "accoreconsole.exe"
    executable.write_text("stub")
    settings = Settings(project_root=tmp_path, autocad_executable=executable)
    runner = AutoCadRunner(settings)

    monkeypatch.setattr("dwgmagic.integrations.autocad.subprocess.Popen", HangingPopen)

    cancel = threading.Event()
    cancel.set()
    script = tmp_path / "script.scr"
    script.write_text("script")
    result = runner.run_script(
        script_path=script, logger=_quiet_logger(), cancel_event=cancel
    )

    assert result.succeeded is False
    assert result.failure_reason == "cancelled"


def test_runner_discover_raises_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "dwgmagic.integrations.autocad.registry_autocad_candidates", lambda: ()
    )
    settings = Settings(project_root=tmp_path, autocad_executable=None, autocad_candidates=())
    runner = AutoCadRunner(settings)
    with pytest.raises(AutoCadNotFoundError):
        runner.discover()


def test_coordinator_executes_jobs(tmp_path):
    class RecordingRunner:
        def __init__(self):
            self.calls = []
            self.settings = SimpleNamespace(max_workers=2)

        def run_script(self, *, script_path, logger, input_path=None, **kwargs):
            self.calls.append((script_path, input_path))
            return AutoCadResult(
                name=script_path.stem,
                returncode=0,
                stdout="",
                stderr="",
                command=(str(script_path),),
            )

    runner = RecordingRunner()
    coordinator = AutoCadCoordinator(runner, max_workers=2)
    jobs = [
        AutoCadJob(name="a", script_path=tmp_path / "a.scr", input_path=None),
        AutoCadJob(name="b", script_path=tmp_path / "b.scr", input_path=tmp_path / "b.dwg"),
    ]
    results = list(coordinator.execute(jobs, _quiet_logger()))
    assert len(results) == 2
    assert sorted(result.name for result in results) == ["a", "b"]
    assert set(runner.calls) == {
        (tmp_path / "a.scr", None),
        (tmp_path / "b.scr", tmp_path / "b.dwg"),
    }


def test_coordinator_collects_crashed_jobs_as_failures(tmp_path):
    class ExplodingRunner:
        settings = SimpleNamespace(max_workers=1)

        def run_script(self, *, script_path, logger, input_path=None, **kwargs):
            raise RuntimeError("boom")

    coordinator = AutoCadCoordinator(ExplodingRunner(), max_workers=1)
    jobs = [
        AutoCadJob(name="a", script_path=tmp_path / "a.scr", input_path=None),
        AutoCadJob(name="b", script_path=tmp_path / "b.scr", input_path=None),
    ]
    results = list(coordinator.execute(jobs, _quiet_logger()))
    # Both jobs are reported instead of aborting the batch on the first crash.
    assert len(results) == 2
    assert all(not result.succeeded for result in results)
    assert all("boom" in (result.failure_reason or "") for result in results)


def test_coordinator_skips_jobs_after_cancel(tmp_path):
    class RecordingRunner:
        settings = SimpleNamespace(max_workers=1)

        def run_script(self, *, script_path, logger, input_path=None, **kwargs):
            return AutoCadResult(
                name=script_path.stem, returncode=0, stdout="", stderr="", command=()
            )

    cancel = threading.Event()
    cancel.set()
    coordinator = AutoCadCoordinator(RecordingRunner(), max_workers=1)
    jobs = [AutoCadJob(name="a", script_path=tmp_path / "a.scr", input_path=None)]
    results = list(coordinator.execute(jobs, _quiet_logger(), cancel_event=cancel))
    assert results[0].failure_reason == "cancelled"


def test_coordinator_notifies_listener(tmp_path):
    events = []

    class RecordingRunner:
        settings = SimpleNamespace(max_workers=1)

        def run_script(self, *, script_path, logger, input_path=None, output_callback=None, **kwargs):
            if output_callback:
                output_callback("live line")
            return AutoCadResult(
                name=script_path.stem,
                returncode=0,
                stdout="stdout",
                stderr="stderr",
                command=(str(script_path),),
            )

    class Listener:
        def on_job_queued(self, job):
            events.append(("queued", job.name))

        def on_job_started(self, job):
            events.append(("started", job.name))

        def on_job_output(self, job_name, line):
            events.append(("output", job_name, line))

        def on_job_completed(self, result):
            events.append(("completed", result.name, result.stdout, result.stderr))

        def on_job_failed(self, job, error):  # pragma: no cover - not exercised here
            events.append(("failed", job.name, str(error)))

    coordinator = AutoCadCoordinator(RecordingRunner(), max_workers=1)
    job = AutoCadJob(name="demo", script_path=tmp_path / "demo.scr", input_path=None)

    list(coordinator.execute([job], _quiet_logger(), listener=Listener()))

    assert ("queued", "demo") in events
    assert ("started", "demo") in events
    assert ("output", "demo", "live line") in events
    completed_events = [event for event in events if event[0] == "completed"]
    assert completed_events and completed_events[0][1:] == ("demo", "stdout", "stderr")
