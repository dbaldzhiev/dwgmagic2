"""Per-run manifest: a machine-readable record of what a pipeline run did.

Written to ``logs/run_<timestamp>.json`` inside the project so failures can be
diagnosed after the fact, and used to build the human-readable run summary
shown by the CLI and GUI.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import dwgmagic
from dwgmagic.core.context import ProjectContext, StageResult
from dwgmagic.integrations.autocad import AutoCadResult


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _settings_snapshot(context: ProjectContext) -> Dict[str, Any]:
    settings = context.settings
    return {
        "project_root": str(settings.project_root),
        "tectonica_path": str(settings.tectonica_path),
        "autocad_executable": str(settings.autocad_executable) if settings.autocad_executable else None,
        "xref_xplode_toggle": settings.xref_xplode_toggle,
        "max_workers": settings.max_workers,
        "job_timeout": settings.job_timeout,
        "continue_on_error": settings.continue_on_error,
        "log_level": settings.log_level,
    }


def _job_entries(results: Sequence[AutoCadResult]) -> List[Dict[str, Any]]:
    entries = []
    for result in results:
        entries.append(
            {
                "name": result.name,
                "returncode": result.returncode,
                "succeeded": result.succeeded,
                "duration_s": round(result.duration, 2),
                "failure_reason": result.failure_reason,
                "command": list(result.command),
            }
        )
    return entries


def expected_deliverables(context: ProjectContext) -> List[Path]:
    project_root = context.project_root
    name = project_root.name
    return [
        project_root / f"{name}_MXR.dwg",
        project_root / f"{name}_MM.dwg",
    ]


def build_manifest(
    context: ProjectContext, results: Sequence[StageResult]
) -> Dict[str, Any]:
    autocad_results: Sequence[AutoCadResult] = context.get("autocad_results", [])
    deliverables = []
    for path in expected_deliverables(context):
        deliverables.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
            }
        )
    succeeded = bool(results) and all(result.succeeded for result in results)
    return {
        "app": "dwgmagic",
        "version": dwgmagic.__version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": context.project_root.name,
        "succeeded": succeeded,
        "settings": _settings_snapshot(context),
        "dwg_files": list(context.get("dwg_files", [])),
        "stages": [
            {
                "name": result.name,
                "succeeded": result.succeeded,
                "details": result.details,
                "started_at": result.started_at,
                "duration_s": round(result.duration, 2),
            }
            for result in results
        ],
        "jobs": _job_entries(autocad_results),
        "deliverables": deliverables,
    }


def write_manifest(
    context: ProjectContext, results: Sequence[StageResult], logger=None
) -> Optional[Path]:
    """Persist the run manifest; returns the path or None when unwritable."""

    manifest = build_manifest(context, results)
    log_dir = context.project_root / context.settings.log_dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"run_{timestamp}.json"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_jsonable(manifest), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        if logger is not None:
            logger.warning("Could not write run manifest: %s", exc)
        return None
    if logger is not None:
        logger.info("Run manifest written to %s", path)
    return path


def _format_size(size: Optional[int]) -> str:
    if size is None:
        return "missing"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def build_summary_lines(
    context: ProjectContext, results: Sequence[StageResult]
) -> List[str]:
    """Human-readable end-of-run summary shared by CLI and GUI."""

    lines: List[str] = []
    succeeded = bool(results) and all(result.succeeded for result in results)
    total_duration = sum(result.duration for result in results)
    lines.append(
        f"Run {'succeeded' if succeeded else 'FAILED'} in {total_duration:.0f}s"
    )

    autocad_results: Sequence[AutoCadResult] = context.get("autocad_results", [])
    if autocad_results:
        failed_jobs = [r for r in autocad_results if not r.succeeded]
        lines.append(
            f"AutoCAD jobs: {len(autocad_results) - len(failed_jobs)} succeeded, "
            f"{len(failed_jobs)} failed"
        )
        for result in failed_jobs:
            reason = result.failure_reason or f"exit code {result.returncode}"
            lines.append(f"  ✗ {result.name}: {reason}")

    for result in results:
        if not result.succeeded:
            detail = f": {result.details}" if result.details else ""
            lines.append(f"Stage '{result.name}' failed{detail}")

    for path in expected_deliverables(context):
        size = path.stat().st_size if path.exists() else None
        marker = "✓" if path.exists() else "✗"
        lines.append(f"  {marker} {path.name} ({_format_size(size)})")

    return lines


__all__ = [
    "build_manifest",
    "write_manifest",
    "build_summary_lines",
    "expected_deliverables",
]
