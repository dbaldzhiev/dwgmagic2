"""Pipeline context and configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from jinja2 import Environment

from dwgmagic.settings import Settings


@dataclass(slots=True)
class ProjectConfig:
    """Static configuration provided to the pipeline."""

    settings: Settings
    stages: Iterable[str] = field(default_factory=tuple)


@dataclass(slots=True)
class ProjectContext:
    """Mutable runtime state that stages exchange explicitly."""

    config: ProjectConfig
    environment: Environment
    state: Dict[str, Any] = field(default_factory=dict)

    @property
    def settings(self) -> Settings:
        return self.config.settings

    @property
    def project_root(self) -> Path:
        return self.settings.project_root

    def resolve(self, *parts: str | Path) -> Path:
        """Resolve a path relative to the project root without touching cwd."""

        path = self.project_root
        for part in parts:
            path = path / Path(part)
        return path

    def set(self, key: str, value: Any) -> None:
        self.state[key] = value

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        return self.state.get(key, default)


@dataclass(slots=True)
class StageResult:
    """Outcome of a pipeline stage execution."""

    name: str
    succeeded: bool
    details: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    #: ISO timestamp of when the stage started (set by the pipeline runner).
    started_at: Optional[str] = None
    #: Wall-clock seconds the stage took (set by the pipeline runner).
    duration: float = 0.0

