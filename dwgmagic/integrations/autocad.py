"""AutoCAD integration layer with structured execution and progress reporting."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

from dwgmagic.errors import AutoCadNotFoundError
from dwgmagic.settings import DEFAULT_AUTOCAD_CANDIDATES, Settings

#: accoreconsole frequently exits 0 despite failing; these markers in its
#: output indicate a failed run regardless of the exit code.
#:
#: Note: a bare "Unknown command" is NOT fatal — the historic scripts feed
#: XREF more tokens than it consumes when a drawing has no xrefs, and the
#: spilled token triggers a harmless "Unknown command "R"". Only the
#: tectonica plugin commands being unknown (NETLOAD failed) is fatal.
FAILURE_MARKERS: Tuple[str, ...] = (
    'Unknown command "TEC',
    "Unable to load",
    "eLoadFailed",
    "FATAL ERROR",
    "Unhandled exception",
    "Unable to open drawing",
)

#: Output produced when a script cancels out of a command; harmless.
_POLL_INTERVAL = 0.25


def registry_autocad_candidates() -> Tuple[Path, ...]:
    """Discover accoreconsole.exe locations from the AutoCAD registry keys."""

    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return ()

    candidates: List[Path] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            root = winreg.OpenKey(hive, r"SOFTWARE\Autodesk\AutoCAD")
        except OSError:
            continue
        with root:
            try:
                release_count = winreg.QueryInfoKey(root)[0]
                for i in range(release_count):
                    release = winreg.EnumKey(root, i)
                    try:
                        release_key = winreg.OpenKey(root, release)
                    except OSError:
                        continue
                    with release_key:
                        product_count = winreg.QueryInfoKey(release_key)[0]
                        for j in range(product_count):
                            product = winreg.EnumKey(release_key, j)
                            try:
                                product_key = winreg.OpenKey(release_key, product)
                            except OSError:
                                continue
                            with product_key:
                                try:
                                    location, _ = winreg.QueryValueEx(
                                        product_key, "AcadLocation"
                                    )
                                except OSError:
                                    continue
                            exe = Path(location) / "accoreconsole.exe"
                            if exe not in candidates:
                                candidates.append(exe)
            except OSError:  # pragma: no cover - registry quirks
                pass
    return tuple(candidates)


def discover_autocad(
    explicit: Optional[Path] = None,
    candidates: Sequence[Path] = DEFAULT_AUTOCAD_CANDIDATES,
) -> Path:
    """Locate accoreconsole.exe, raising :class:`AutoCadNotFoundError` if absent.

    Resolution order: explicit path, registry-registered installs (newest
    first is not guaranteed), then the static candidate paths.
    """

    searched: List[Path] = []
    if explicit:
        if explicit.exists():
            return explicit
        searched.append(explicit)

    for candidate in registry_autocad_candidates():
        if candidate.exists():
            return candidate
        searched.append(candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate
        searched.append(candidate)

    raise AutoCadNotFoundError(searched)


@dataclass(slots=True)
class AutoCadResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    command: Tuple[str, ...]
    duration: float = 0.0
    #: Populated when the job failed for a reason other than the exit code
    #: (timeout, cancellation, failure marker found in output).
    failure_reason: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.failure_reason is None

    def output_tail(self, lines: int = 12) -> str:
        combined = "\n".join(part for part in (self.stdout, self.stderr) if part).strip()
        if not combined:
            return ""
        return "\n".join(combined.splitlines()[-lines:])


@dataclass(slots=True)
class AutoCadJob:
    name: str
    script_path: Path
    input_path: Optional[Path]
    #: Files this job is expected to produce; validated after execution.
    expected_outputs: Tuple[Path, ...] = field(default_factory=tuple)


def _scan_for_failure(output: str) -> Optional[str]:
    for line in output.splitlines():
        for marker in FAILURE_MARKERS:
            if marker in line:
                return line.strip()
    return None


class AutoCadRunner:
    """Executes AutoCAD console commands with streaming output and timeouts."""

    def __init__(self, settings: Settings, *, timeout: Optional[float] = None) -> None:
        self.settings = settings
        self.timeout = timeout if timeout is not None else settings.job_timeout

    def discover(self) -> Path:
        return discover_autocad(
            self.settings.autocad_executable, self.settings.autocad_candidates
        )

    def run_script(
        self,
        *,
        script_path: Path,
        logger,
        input_path: Optional[Path] = None,
        output_callback: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> AutoCadResult:
        executable = self.discover()
        command = [str(executable)]
        if input_path is not None:
            command.extend(["/i", str(input_path)])
        command.extend(["/s", str(script_path)])
        logger.info("Starting AutoCAD job for %s", script_path.name)
        logger.debug("Executing %s", command)

        started = time.monotonic()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-16-le",
            errors="replace",
            cwd=str(self.settings.project_root),
            # Never flash a console window per job (output is piped anyway).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        def _pump(stream, sink: List[str], forward: bool) -> None:
            for line in iter(stream.readline, ""):
                stripped = line.rstrip("\r\n")
                sink.append(stripped)
                if forward and output_callback is not None:
                    try:
                        output_callback(stripped)
                    except Exception:  # noqa: BLE001 - listeners must not kill the job
                        pass
            stream.close()

        readers = [
            threading.Thread(
                target=_pump, args=(process.stdout, stdout_lines, True), daemon=True
            ),
            threading.Thread(
                target=_pump, args=(process.stderr, stderr_lines, False), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()

        failure_reason: Optional[str] = None
        deadline = started + self.timeout if self.timeout else None
        while True:
            if process.poll() is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                failure_reason = "cancelled"
                logger.warning("Cancelling AutoCAD job %s", script_path.stem)
                process.kill()
                process.wait()
                break
            if deadline is not None and time.monotonic() > deadline:
                failure_reason = f"timed out after {self.timeout:.0f}s"
                logger.error(
                    "AutoCAD job %s exceeded %.0fs; killing process",
                    script_path.stem,
                    self.timeout,
                )
                process.kill()
                process.wait()
                break
            time.sleep(_POLL_INTERVAL)

        for reader in readers:
            reader.join(timeout=5)

        duration = time.monotonic() - started
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)

        if failure_reason is None and process.returncode == 0:
            failure_reason = _scan_for_failure(stdout) or _scan_for_failure(stderr)
            if failure_reason:
                failure_reason = f"failure marker in output: {failure_reason}"

        result = AutoCadResult(
            name=script_path.stem,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            command=tuple(command),
            duration=duration,
            failure_reason=failure_reason,
        )
        self._dump_job_output(result, logger)
        if not result.succeeded:
            logger.error(
                "AutoCAD job %s failed (code %s%s)",
                script_path.stem,
                result.returncode,
                f", {failure_reason}" if failure_reason else "",
            )
        else:
            logger.debug(
                "AutoCAD job %s completed in %.1fs", script_path.stem, duration
            )
        return result

    def _dump_job_output(self, result: AutoCadResult, logger) -> None:
        """Persist raw console output for post-mortem debugging."""

        try:
            jobs_dir = self.settings.project_root / self.settings.log_dir / "jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            dump_path = jobs_dir / f"{result.name}.out.txt"
            body = result.stdout
            if result.stderr:
                body = f"{body}\n--- stderr ---\n{result.stderr}"
            dump_path.write_text(body, encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - disk issues
            logger.debug("Could not write job output dump: %s", exc)


class AutoCadCoordinator:
    """Coordinates execution of AutoCAD jobs using a bounded thread pool."""

    def __init__(self, runner: AutoCadRunner, *, max_workers: Optional[int] = None) -> None:
        self.runner = runner
        self.max_workers = max_workers

    def _resolve_workers(self) -> int:
        if self.max_workers:
            return self.max_workers
        settings = getattr(self.runner, "settings", None)
        configured = getattr(settings, "max_workers", None)
        return max(1, int(configured or os.cpu_count() or 4))

    def execute(
        self,
        jobs: Sequence[AutoCadJob],
        logger,
        *,
        listener: Optional["AutoCadProgressListener"] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Sequence[AutoCadResult]:
        results: List[AutoCadResult] = []
        for job in jobs:
            _notify(listener, "on_job_queued", job)

        with ThreadPoolExecutor(max_workers=self._resolve_workers()) as executor:
            future_map = {
                executor.submit(self._execute_job, job, logger, listener, cancel_event): job
                for job in jobs
            }
            for future in as_completed(future_map):
                job = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - job crash becomes a failed result
                    logger.error("Job %s crashed: %s", job.name, exc)
                    _notify(listener, "on_job_failed", job, exc)
                    result = AutoCadResult(
                        name=job.name,
                        returncode=-1,
                        stdout="",
                        stderr=str(exc),
                        command=(),
                        failure_reason=f"crashed: {exc}",
                    )
                    results.append(result)
                else:
                    logger.info(
                        "Job %s completed with code %s in %.1fs",
                        job.name,
                        result.returncode,
                        result.duration,
                    )
                    _notify(listener, "on_job_completed", result)
                    results.append(result)
        return results

    def _execute_job(
        self,
        job: AutoCadJob,
        logger,
        listener: Optional["AutoCadProgressListener"],
        cancel_event: Optional[threading.Event] = None,
    ) -> AutoCadResult:
        if cancel_event is not None and cancel_event.is_set():
            return AutoCadResult(
                name=job.name,
                returncode=-1,
                stdout="",
                stderr="",
                command=(),
                failure_reason="cancelled",
            )
        _notify(listener, "on_job_started", job)

        def _forward_output(line: str) -> None:
            _notify(listener, "on_job_output", job.name, line)

        result = self.runner.run_script(
            script_path=job.script_path,
            input_path=job.input_path,
            logger=logger,
            output_callback=_forward_output if listener is not None else None,
            cancel_event=cancel_event,
        )
        return replace(result, name=job.name)


class AutoCadProgressListener(Protocol):
    """Observer for AutoCAD job execution."""

    def on_job_queued(self, job: AutoCadJob) -> None:  # pragma: no cover - interface
        ...

    def on_job_started(self, job: AutoCadJob) -> None:  # pragma: no cover - interface
        ...

    def on_job_output(self, job_name: str, line: str) -> None:  # pragma: no cover - interface
        ...

    def on_job_completed(self, result: AutoCadResult) -> None:  # pragma: no cover - interface
        ...

    def on_job_failed(self, job: AutoCadJob, error: Exception) -> None:  # pragma: no cover - interface
        ...


def _notify(listener: Optional[AutoCadProgressListener], method: str, *args) -> None:
    if listener is None:
        return
    callback = getattr(listener, method, None)
    if callable(callback):
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - listeners must never break execution
            pass


__all__ = [
    "AutoCadResult",
    "AutoCadJob",
    "AutoCadRunner",
    "AutoCadCoordinator",
    "AutoCadProgressListener",
    "discover_autocad",
    "registry_autocad_candidates",
    "FAILURE_MARKERS",
]
