"""Concrete pipeline stages for DWGMAGIC."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from dwgmagic.classify import classify_dwg_files
from dwgmagic.core.pipeline import CANCEL_EVENT_KEY, PipelineStage
from dwgmagic.errors import DwgmagicError, PipelineCancelledError
from dwgmagic.integrations.autocad import AutoCadCoordinator, AutoCadJob, AutoCadResult, AutoCadRunner
from dwgmagic.logger import LoggerFactory
from dwgmagic.miscutil import Preprocessor
from dwgmagic.script_generator import ScriptGenerator
from dwgmagic.trusted_folder import TrustedFolderChecker
from jinja2 import Environment

from .context import ProjectContext, StageResult


def _error_details(exc: Exception) -> str:
    if isinstance(exc, DwgmagicError):
        return exc.user_message()
    return str(exc)


@dataclass(slots=True)
class TrustedFolderCheckStage(PipelineStage):
    """Ensures the AutoCAD trusted folder is configured."""

    checker: TrustedFolderChecker
    logger_factory: LoggerFactory

    name: str = "trusted_folder_check"

    def run(self, context: ProjectContext) -> StageResult:
        logger = self.logger_factory.create("CHECKS")
        try:
            self.checker.check(context.settings, logger)
            return StageResult(self.name, True)
        except Exception as exc:
            logger.error("Trusted folder check failed: %s", exc)
            return StageResult(self.name, False, _error_details(exc))


@dataclass(slots=True)
class PreprocessorStage(PipelineStage):
    """Cleans and prepares the project workspace."""

    preprocessor: Preprocessor
    logger_factory: LoggerFactory

    name: str = "preprocess"

    def run(self, context: ProjectContext) -> StageResult:
        logger = self.logger_factory.create("PREPROCESS")
        try:
            dwg_files = self.preprocessor.preprocess(context, logger)
            context.set("dwg_files", dwg_files)
            classified = classify_dwg_files(dwg_files)
            if classified.ignored:
                logger.warning(
                    "Ignoring %d file(s) that match no convention: %s",
                    len(classified.ignored),
                    ", ".join(classified.ignored),
                )
            if classified.orphan_views:
                logger.warning(
                    "%d view file(s) match no sheet and will not be merged: %s",
                    len(classified.orphan_views),
                    ", ".join(classified.orphan_views),
                )
            logger.info("Classified project files: %s", classified.summary())
            return StageResult(
                self.name,
                True,
                data={
                    "dwg_files": dwg_files,
                    "classification": classified.summary(),
                    "ignored": classified.ignored,
                    "orphan_views": classified.orphan_views,
                },
            )
        except Exception as exc:
            logger.error("Preprocessing failed: %s", exc)
            return StageResult(self.name, False, _error_details(exc))


@dataclass(slots=True)
class ScriptGenerationStage(PipelineStage):
    """Generates AutoCAD scripts and batch files."""

    generator: ScriptGenerator
    logger_factory: LoggerFactory

    name: str = "generate_scripts"

    def run(self, context: ProjectContext) -> StageResult:
        logger = self.logger_factory.create("SCRIPT_GENERATOR")
        try:
            artifacts = self.generator.generate_all(context, logger)
            context.set("scripts", artifacts)
            structured_sheets = context.get("structured_sheets", [])
            sheet_views_lookup = context.get("sheet_views_lookup", {})
            return StageResult(
                self.name,
                True,
                data={
                    "artifacts": artifacts,
                    "sheets": structured_sheets,
                    "sheet_views_lookup": sheet_views_lookup,
                },
            )
        except Exception as exc:
            logger.error("Script generation failed: %s", exc)
            return StageResult(self.name, False, _error_details(exc))


@dataclass(slots=True)
class AutoCadStage(PipelineStage):
    """Runs AutoCAD console jobs for views, sheets, and merge."""

    coordinator: AutoCadCoordinator
    logger_factory: LoggerFactory

    name: str = "autocad"

    def run(self, context: ProjectContext) -> StageResult:
        logger = self.logger_factory.create("AUTOCAD")
        settings = context.settings
        continue_on_error = settings.continue_on_error
        results: List[AutoCadResult] = []
        # Publish incrementally so manifests/summaries see the jobs that ran
        # even when the stage aborts partway through.
        context.set("autocad_results", results)
        try:
            state = self._build_jobs(context)
            listener = context.get("autocad_listener")
            cancel_event: Optional[threading.Event] = context.get(CANCEL_EVENT_KEY)

            def _check_cancelled() -> None:
                if cancel_event is not None and cancel_event.is_set():
                    raise PipelineCancelledError()

            def _run_batch(batch: Sequence[AutoCadJob], label: str) -> List[AutoCadResult]:
                if not batch:
                    return []
                _check_cancelled()
                logger.info("Running %s batch (%d job(s))", label, len(batch))
                batch_results = list(
                    self.coordinator.execute(
                        batch, logger, listener=listener, cancel_event=cancel_event
                    )
                )
                results.extend(batch_results)
                _check_cancelled()

                failed = [r for r in batch_results if not r.succeeded]
                if failed:
                    summary = self._failure_summary(failed)
                    if continue_on_error:
                        logger.warning(
                            "%s batch had failures (continuing): %s", label, summary
                        )
                    else:
                        raise DwgmagicError(
                            f"{label} batch failed: {summary}",
                            hint=(
                                "See logs/jobs/*.out.txt for the AutoCAD console "
                                "output of each failed job."
                            ),
                        )

                missing = self._missing_outputs(batch)
                if missing:
                    missing_list = ", ".join(str(path) for path in missing)
                    if continue_on_error:
                        logger.warning(
                            "%s batch did not produce expected outputs (continuing): %s",
                            label,
                            missing_list,
                        )
                    else:
                        raise DwgmagicError(
                            f"{label} batch did not produce expected outputs: {missing_list}",
                            hint="The corresponding AutoCAD jobs likely failed silently; check logs/jobs/.",
                        )
                return batch_results

            _run_batch(state.view_jobs, "view")
            _run_batch(state.sheet_jobs, "sheet")
            _run_batch(state.merge_jobs, "merge")

            context.set("autocad_results", results)
            failed_total = [r for r in results if not r.succeeded]
            details = None
            if failed_total:
                details = f"Completed with {len(failed_total)} failed job(s) (continue-on-error)"
            return StageResult(
                self.name,
                True,
                details,
                data={
                    "results": results,
                    "failed_jobs": [r.name for r in failed_total],
                },
            )
        except Exception as exc:
            if not isinstance(exc, PipelineCancelledError):
                logger.error("AutoCAD execution failed: %s", exc)
            else:
                logger.warning("AutoCAD execution cancelled")
            return StageResult(self.name, False, _error_details(exc))

    @staticmethod
    def _failure_summary(failed: Sequence[AutoCadResult]) -> str:
        parts = []
        for result in failed:
            reason = result.failure_reason or f"exit code {result.returncode}"
            parts.append(f"{result.name} ({reason})")
        return ", ".join(parts)

    @staticmethod
    def _missing_outputs(batch: Sequence[AutoCadJob]) -> List[Path]:
        missing: List[Path] = []
        for job in batch:
            for output in job.expected_outputs:
                if not output.exists():
                    missing.append(output)
        return missing

    @dataclass(slots=True)
    class StageJobs:
        view_jobs: Tuple[AutoCadJob, ...]
        sheet_jobs: Tuple[AutoCadJob, ...]
        merge_jobs: Tuple[AutoCadJob, ...]

    def _build_jobs(self, context: ProjectContext) -> "AutoCadStage.StageJobs":
        project_root = context.project_root
        derevitized = project_root / "derevitized"
        scripts_dir = project_root / "scripts"

        classified = classify_dwg_files(context.get("dwg_files", []))
        view_files = classified.views
        sheet_files = classified.sheets
        context.set("view_files", view_files)
        context.set("sheet_files", sheet_files)

        view_jobs = tuple(
            AutoCadJob(
                name=f"view:{Path(view).stem}",
                script_path=scripts_dir / f"{Path(view).stem.upper()}.scr",
                input_path=derevitized / view,
            )
            for view in view_files
        )

        sheet_jobs = tuple(
            AutoCadJob(
                name=f"sheet:{Path(sheet).stem}",
                script_path=scripts_dir / f"{Path(sheet).stem.upper()}_SHEET.scr",
                input_path=derevitized / sheet,
                expected_outputs=(derevitized / f"{Path(sheet).stem}_xrefed.dwg",),
            )
            for sheet in sheet_files
        )

        project_name = project_root.name
        merge_jobs = (
            AutoCadJob(
                name="merge",
                script_path=scripts_dir / "DWGMAGIC.scr",
                input_path=None,
                expected_outputs=(
                    project_root / f"{project_name}_MXR.dwg",
                    project_root / f"{project_name}_MM.dwg",
                ),
            ),
        )

        return AutoCadStage.StageJobs(view_jobs, sheet_jobs, merge_jobs)


def build_default_stages(
    environment: Environment,
    logger_factory: LoggerFactory,
    runner: AutoCadRunner,
    coordinator: AutoCadCoordinator,
) -> Tuple[PipelineStage, ...]:
    """Construct the default pipeline stages used by both CLI and GUI."""

    return (
        TrustedFolderCheckStage(TrustedFolderChecker(runner), logger_factory),
        PreprocessorStage(Preprocessor(), logger_factory),
        ScriptGenerationStage(ScriptGenerator(environment), logger_factory),
        AutoCadStage(coordinator, logger_factory),
    )
