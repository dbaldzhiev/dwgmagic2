"""Pipeline orchestration for DWGMAGIC."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Protocol, Sequence

from .context import ProjectContext, StageResult

#: Context key holding the optional :class:`threading.Event` used to cancel a run.
CANCEL_EVENT_KEY = "cancel_event"


class PipelineStage:
    """Interface for pipeline stages."""

    name: str

    def run(self, context: ProjectContext) -> StageResult:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(slots=True)
class PipelineRunner:
    """Sequentially executes pipeline stages, surfacing structured results."""

    stages: Sequence[PipelineStage]

    def run(
        self,
        context: ProjectContext,
        *,
        listener: Optional["PipelineListener"] = None,
    ) -> List[StageResult]:
        results: List[StageResult] = []
        cancel_event: Optional[threading.Event] = context.get(CANCEL_EVENT_KEY)
        for stage in self.stages:
            if cancel_event is not None and cancel_event.is_set():
                results.append(
                    StageResult(stage.name, False, "Cancelled before stage started")
                )
                break
            if listener:
                listener.on_stage_started(stage.name, context)
            started_at = datetime.now().isoformat(timespec="seconds")
            started = time.monotonic()
            result = stage.run(context)
            result.started_at = started_at
            result.duration = time.monotonic() - started
            results.append(result)
            if listener:
                listener.on_stage_completed(result, context)
            if not result.succeeded:
                break
        if listener:
            listener.on_pipeline_completed(tuple(results), context)
        return results

    @classmethod
    def from_iterable(cls, stages: Iterable[PipelineStage]) -> "PipelineRunner":
        return cls(tuple(stages))


class PipelineListener(Protocol):
    """Observer for pipeline stage progress."""

    def on_stage_started(self, stage_name: str, context: ProjectContext) -> None:  # pragma: no cover - interface
        ...

    def on_stage_completed(self, result: StageResult, context: ProjectContext) -> None:  # pragma: no cover - interface
        ...

    def on_pipeline_completed(
        self, results: Sequence[StageResult], context: ProjectContext
    ) -> None:  # pragma: no cover - interface
        ...
