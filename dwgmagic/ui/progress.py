"""Progress listeners used by CLI and GUI front-ends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence

from rich.console import Console

from dwgmagic.core.context import ProjectContext, StageResult
from dwgmagic.core.pipeline import PipelineListener
from dwgmagic.integrations.autocad import AutoCadJob, AutoCadProgressListener, AutoCadResult


@dataclass(slots=True)
class ProgressEvent:
    """Serializable event emitted for GUI consumption."""

    kind: str
    payload: Dict[str, Any]


class ConsoleProgressListener(PipelineListener, AutoCadProgressListener):
    """Rich-based listener that streams status updates to the CLI."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def on_stage_started(self, stage_name: str, context: ProjectContext) -> None:
        self.console.print(f"[bold cyan]→ Starting stage[/] [white]{stage_name}[/]")

    def on_stage_completed(self, result: StageResult, context: ProjectContext) -> None:
        status = "✅" if result.succeeded else "❌"
        message = f"{status} {result.name}"
        if result.details and not result.succeeded:
            message = f"{message}: {result.details}"
        self.console.print(message)

    def on_pipeline_completed(self, results: Sequence[StageResult], context: ProjectContext) -> None:
        if results and all(result.succeeded for result in results):
            self.console.print("[green]Pipeline completed successfully[/green]")
        elif results:
            failed = next((result for result in results if not result.succeeded), results[-1])
            self.console.print(
                f"[red]Pipeline halted during stage[/red] [bold]{failed.name}[/bold]"
            )
        else:
            self.console.print("[yellow]Pipeline produced no results[/yellow]")

    def on_job_queued(self, job: AutoCadJob) -> None:
        self.console.log(
            f"Queued AutoCAD job [bold]{job.name}[/bold] → {job.script_path.name}"
        )

    def on_job_started(self, job: AutoCadJob) -> None:
        self.console.log(f"[yellow]Running AutoCAD job[/yellow] {job.name}")

    def on_job_output(self, job_name: str, line: str) -> None:
        # Raw console chatter is only interesting in the job dump files;
        # keep the CLI readable by not echoing it here.
        pass

    def on_job_completed(self, result: AutoCadResult) -> None:
        symbol = "✅" if result.succeeded else "❌"
        self.console.log(
            f"{symbol} AutoCAD job {result.name} exited with code {result.returncode}"
        )
        if result.stdout.strip():
            self.console.log(f"[dim]{result.stdout.strip()}[/dim]")
        if result.stderr.strip():
            self.console.log(f"[red]{result.stderr.strip()}[/red]")

    def on_job_failed(self, job: AutoCadJob, error: Exception) -> None:
        self.console.log(f"[red]AutoCAD job {job.name} failed: {error}")


class QueueProgressListener(PipelineListener, AutoCadProgressListener):
    """Listener that forwards updates to a queue for the GUI thread."""

    def __init__(self, queue) -> None:  # queue.Queue-like interface
        self.queue = queue

    def on_stage_started(self, stage_name: str, context: ProjectContext) -> None:
        self.queue.put(ProgressEvent("stage_started", {"name": stage_name}))

    def on_stage_completed(self, result: StageResult, context: ProjectContext) -> None:
        self.queue.put(
            ProgressEvent(
                "stage_completed",
                {
                    "name": result.name,
                    "succeeded": result.succeeded,
                    "details": result.details,
                    "data": result.data,
                    "duration": result.duration,
                },
            )
        )

    def on_pipeline_completed(self, results: Sequence[StageResult], context: ProjectContext) -> None:
        summary = [
            {"name": item.name, "succeeded": item.succeeded, "details": item.details}
            for item in results
        ]
        self.queue.put(ProgressEvent("pipeline_completed", {"results": summary}))

    def on_job_queued(self, job: AutoCadJob) -> None:
        self.queue.put(
            ProgressEvent(
                "job_queued",
                {
                    "name": job.name,
                    "script": str(job.script_path),
                    "input": str(job.input_path) if job.input_path else None,
                },
            )
        )

    def on_job_started(self, job: AutoCadJob) -> None:
        self.queue.put(ProgressEvent("job_started", {"name": job.name}))

    def on_job_output(self, job_name: str, line: str) -> None:
        self.queue.put(ProgressEvent("job_output", {"name": job_name, "line": line}))

    def on_job_completed(self, result: AutoCadResult) -> None:
        self.queue.put(
            ProgressEvent(
                "job_completed",
                {
                    "name": result.name,
                    "succeeded": result.succeeded,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "command": list(result.command),
                    "duration": result.duration,
                    "failure_reason": result.failure_reason,
                },
            )
        )

    def on_job_failed(self, job: AutoCadJob, error: Exception) -> None:
        self.queue.put(
            ProgressEvent(
                "job_failed",
                {
                    "name": job.name,
                    "error": str(error),
                },
            )
        )


__all__ = [
    "ConsoleProgressListener",
    "QueueProgressListener",
    "ProgressEvent",
]
