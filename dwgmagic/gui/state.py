"""Persisted GUI state (window geometry, appearance, recent projects)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_MAX_RECENT = 8


def _state_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "dwgmagic2" / "gui.json"


@dataclass
class GuiState:
    geometry: str = "1400x900"
    appearance: str = "System"
    recent_projects: List[str] = field(default_factory=list)
    #: None means "use all CPUs" (the default); an int is an explicit choice.
    max_workers: int | None = None

    def remember_project(self, project: Path) -> None:
        entry = str(project)
        if entry in self.recent_projects:
            self.recent_projects.remove(entry)
        self.recent_projects.insert(0, entry)
        del self.recent_projects[_MAX_RECENT:]

    @classmethod
    def load(cls) -> "GuiState":
        path = _state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        state = cls()
        if isinstance(data.get("geometry"), str):
            state.geometry = data["geometry"]
        if data.get("appearance") in {"System", "Light", "Dark"}:
            state.appearance = data["appearance"]
        recents = data.get("recent_projects")
        if isinstance(recents, list):
            state.recent_projects = [str(item) for item in recents][:_MAX_RECENT]
        workers = data.get("max_workers")
        if isinstance(workers, int) and workers >= 1:
            state.max_workers = workers
        return state

    def save(self) -> None:
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "geometry": self.geometry,
                        "appearance": self.appearance,
                        "recent_projects": self.recent_projects,
                        "max_workers": self.max_workers,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # UI state is best-effort; never break the app over it


__all__ = ["GuiState"]
