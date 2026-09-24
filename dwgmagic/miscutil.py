"""Preprocessing utilities operating on explicit project context.

Destructive cleanup only happens after the folder has been positively
identified as a DWGMAGIC project (DWGs at top level, a populated
``originals/`` folder, or an ``original.zip`` archive). Anything else raises
:class:`~dwgmagic.errors.NotAProjectError` without touching the folder.

Source precedence, most recent first:

1. DWGs at the top level — a (new) Revit export. DWGMAGIC's own deliverables
   (``<project>_MXR.dwg`` and friends) are never mistaken for sources.
2. ``original.zip`` — the archive a previous run took.
3. ``originals/`` — the fallback when the archive is gone.

A new export over an already-processed project replaces the previous sources,
which are kept in ``original.previous.zip`` rather than thrown away.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from dwgmagic.core.context import ProjectContext
from dwgmagic.errors import ArchiveError, NotAProjectError

#: Configuration files that survive every cleanup pass.
PRESERVED_SUFFIXES = {".toml", ".tml", ".yaml", ".yml", ".json"}
ARCHIVE_NAME = "original.zip"
#: Where the sources a new export replaces are kept.
PREVIOUS_ARCHIVE_NAME = "original.previous.zip"
PRESERVED_NAMES = {"originals", ARCHIVE_NAME, PREVIOUS_ARCHIVE_NAME}
DEFAULT_LOG_DIR = Path("logs")
#: Directories every run regenerates from scratch.
GENERATED_DIRS = ("scripts", "derevitized")
MANUAL_MERGE_BAT = "MANUALMERGE.bat"


def deliverable_names(project_name: str) -> Tuple[str, ...]:
    """DWGs DWGMAGIC itself writes into the project root."""

    return (
        f"{project_name}_MXR.dwg",
        f"{project_name}_MM.dwg",
        # Written by MANUALMERGE.bat, not by the pipeline.
        f"{project_name}_MMM.dwg",
    )


def _is_deliverable(entry: Path, root: Path) -> bool:
    return entry.name.lower() in {name.lower() for name in deliverable_names(root.name)}


def source_dwgs(directory: Path, root: Path) -> List[Path]:
    """DWG sources in ``directory``, excluding DWGMAGIC's own deliverables.

    Counting the deliverables as sources made a project whose archive had gone
    missing feed ``<project>_MXR.dwg`` back in as a "sheet" — and wipe the real
    ``originals/`` to make room for it.
    """

    if not directory.is_dir():
        return []
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_file()
        and entry.suffix.lower() == ".dwg"
        and not _is_deliverable(entry, root)
    )


def _log_dir_path(root: Path, log_dir: Path) -> Path:
    return log_dir if log_dir.is_absolute() else root / log_dir


def _is_log_dir(entry: Path, root: Path, log_dir: Path) -> bool:
    return entry == _log_dir_path(root, log_dir)


@dataclass(slots=True)
class ProjectInspection:
    """Non-destructive snapshot of what a run would operate on."""

    root: Path
    #: ``archive`` | ``fresh`` | ``rerun`` | ``invalid``
    mode: str
    dwg_names: List[str] = field(default_factory=list)
    #: True when the folder has never been processed by DWGMAGIC before.
    first_run: bool = False
    #: True when a fresh export replaces sources a previous run archived.
    replaces_previous: bool = False

    @property
    def is_project(self) -> bool:
        return self.mode != "invalid"

    def describe(self) -> str:
        if not self.is_project:
            return "No DWG files, originals/ folder, or original.zip archive found."
        source = {
            "archive": "original.zip archive",
            "fresh": (
                "a new export (replaces the previous originals)"
                if self.replaces_previous
                else "DWG files in the folder"
            ),
            "rerun": "originals/ folder (rerun)",
        }[self.mode]
        return f"{len(self.dwg_names)} DWG file(s) from {source}"


def _archived_dwgs(archive: Path) -> List[str]:
    try:
        with zipfile.ZipFile(archive, "r") as zip_file:
            return sorted(
                name
                for name in zip_file.namelist()
                if name.lower().endswith(".dwg") and "/" not in name.strip("/")
            )
    except (zipfile.BadZipFile, OSError):
        return []


def inspect_project(root: Path) -> ProjectInspection:
    """Classify a folder without modifying it."""

    archive = root / ARCHIVE_NAME
    originals = root / "originals"

    root_files = [entry.name for entry in source_dwgs(root, root)]
    if root_files:
        # DWGs at the top level are always the newest thing in the folder: a
        # re-export into a processed project must not be silently discarded in
        # favour of the archive of the previous export.
        has_previous = archive.exists() or bool(source_dwgs(originals, root))
        return ProjectInspection(
            root=root,
            mode="fresh",
            dwg_names=root_files,
            first_run=not originals.exists() and not archive.exists(),
            replaces_previous=has_previous,
        )

    if archive.exists():
        archived = _archived_dwgs(archive)
        if archived:
            return ProjectInspection(root=root, mode="archive", dwg_names=archived)

    original_files = [entry.name for entry in source_dwgs(originals, root)]
    if original_files:
        return ProjectInspection(root=root, mode="rerun", dwg_names=original_files)

    return ProjectInspection(root=root, mode="invalid")


@dataclass(slots=True)
class RunPlan:
    """What a run would create and destroy, computed without touching disk.

    Pressing Run is destructive — on a rerun it removes everything in the
    project root that is not ``originals/``, the archives, ``logs/`` or a
    config file. This makes that consequence showable *before* the click.
    """

    root: Path
    mode: str
    dwg_count: int
    #: Paths this run would delete, most significant first.
    deletes: List[Path] = field(default_factory=list)
    #: Paths this run is expected to produce.
    produces: List[Path] = field(default_factory=list)
    #: True when a new export replaces previously archived sources.
    replaces_previous: bool = False

    @property
    def is_destructive(self) -> bool:
        return bool(self.deletes)

    @property
    def unexpected_deletes(self) -> List[Path]:
        """Deletions of things DWGMAGIC did not create (user files).

        Regenerated artifacts are routine; these are what an unattended run
        (``--autorun`` from the context menu) must not remove unasked.
        """

        generated = {name.lower() for name in (*GENERATED_DIRS, "originals", MANUAL_MERGE_BAT)}
        generated |= {name.lower() for name in deliverable_names(self.root.name)}
        return [path for path in self.deletes if path.name.lower() not in generated]


def plan_run(root: Path, log_dir: Path = DEFAULT_LOG_DIR) -> RunPlan:
    """Describe the effect of running the pipeline on ``root``."""

    inspection = inspect_project(root)
    plan = RunPlan(
        root=root,
        mode=inspection.mode,
        dwg_count=len(inspection.dwg_names),
        replaces_previous=inspection.replaces_previous,
    )
    if not inspection.is_project:
        return plan

    if inspection.mode in {"rerun", "archive"}:
        # Both paths wipe the root down to the preserved set.
        keep_originals = inspection.mode == "rerun"
        for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if _is_preserved(entry, root, log_dir):
                if entry.name != "originals" or keep_originals:
                    continue
            plan.deletes.append(entry)
    else:
        # A fresh run clears the generated artifacts and stale deliverables.
        for name in (*GENERATED_DIRS, MANUAL_MERGE_BAT, *deliverable_names(root.name)):
            target = root / name
            if target.exists():
                plan.deletes.append(target)
        originals = root / "originals"
        if source_dwgs(originals, root):
            # Replaced by the new export; its contents go to the backup first.
            plan.deletes.append(originals)

    name = root.name
    produced = [
        root / "originals",
        root / "derevitized",
        root / "scripts",
        root / "logs",
        root / MANUAL_MERGE_BAT,
        root / f"{name}_MXR.dwg",
        root / f"{name}_MM.dwg",
    ]
    if inspection.mode == "fresh":
        produced.insert(0, root / ARCHIVE_NAME)
        if inspection.replaces_previous:
            produced.insert(1, root / PREVIOUS_ARCHIVE_NAME)
    plan.produces = produced
    return plan


def _is_preserved(entry: Path, root: Path, log_dir: Path = DEFAULT_LOG_DIR) -> bool:
    if entry.name in PRESERVED_NAMES:
        return True
    if _is_log_dir(entry, root, log_dir):
        # Run history (logs and manifests) outlives the run that wrote it.
        return True
    return entry.is_file() and entry.suffix.lower() in PRESERVED_SUFFIXES


@dataclass(slots=True)
class Preprocessor:
    """Prepares the project directory for processing."""

    def preprocess(self, context: ProjectContext, logger) -> List[str]:
        project_root = context.project_root
        log_dir = context.settings.log_dir
        inspection = inspect_project(project_root)
        if not inspection.is_project:
            raise NotAProjectError(
                f"{project_root} does not look like a DWGMAGIC project: "
                f"{inspection.describe()}",
                hint="Point DWGMAGIC at a folder containing the exported DWG files.",
            )
        logger.info("Project source: %s", inspection.describe())

        if inspection.mode == "archive":
            self._restore_from_archive(project_root, log_dir, logger)

        dwg_files, from_originals = self._collect_dwg_files(project_root)
        if not dwg_files:
            raise NotAProjectError("No DWG files found in project root")

        if inspection.mode == "fresh" and inspection.replaces_previous:
            # Before anything is overwritten: the sources this export replaces.
            self._backup_previous_sources(project_root, logger)

        self._cleanup_previous_run(project_root, from_originals, log_dir, logger)
        if from_originals:
            dwg_files = self._restore_project_root(project_root, dwg_files, logger)
        else:
            dwg_files = source_dwgs(project_root, project_root)
            if inspection.mode != "archive":
                # Archive mode just extracted these from original.zip, which
                # stays the source of truth; rewriting it would only risk it.
                self._create_archive_backup(project_root, dwg_files, logger)

        self._cleanup_logs(_log_dir_path(project_root, log_dir), logger)
        self._ensure_directories(project_root, ("scripts", "originals", "derevitized"))
        self._populate_working_directories(project_root, dwg_files, from_originals, logger)
        return [path.name for path in dwg_files]

    def _collect_dwg_files(self, root: Path) -> Tuple[List[Path], bool]:
        root_files = source_dwgs(root, root)
        if root_files:
            return root_files, False

        original_files = source_dwgs(root / "originals", root)
        if original_files:
            return original_files, True

        return [], False

    def _backup_previous_sources(self, root: Path, logger) -> None:
        """Keep the sources a new export replaces in ``original.previous.zip``."""

        archive = root / ARCHIVE_NAME
        previous = root / PREVIOUS_ARCHIVE_NAME
        if archive.exists():
            try:
                os.replace(archive, previous)
            except OSError as exc:
                raise ArchiveError(
                    f"Could not keep the previous {ARCHIVE_NAME} as {PREVIOUS_ARCHIVE_NAME}: {exc}",
                    hint="Close any program holding the archive open and retry.",
                ) from exc
            logger.info("New export detected; previous sources kept in %s", previous)
            return

        originals = source_dwgs(root / "originals", root)
        if originals:
            self._write_verified_archive(previous, originals)
            logger.info(
                "New export detected; %d previous source(s) from originals/ kept in %s",
                len(originals),
                previous,
            )

    def _cleanup_previous_run(self, root: Path, rerun: bool, log_dir: Path, logger) -> None:
        originals = root / "originals"
        if rerun and originals.exists():
            logger.info("Detected previous run; restoring project from originals")
            for entry in root.iterdir():
                if _is_preserved(entry, root, log_dir):
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            logger.info("Previous preprocessing artifacts removed")
        else:
            # Remove generated artifacts and stale deliverables without touching
            # DWG sources. A leftover deliverable would otherwise satisfy the
            # merge's output check even if this run's merge produced nothing.
            for name in (*GENERATED_DIRS, MANUAL_MERGE_BAT, *deliverable_names(root.name)):
                target = root / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    try:
                        target.unlink()
                    except OSError as exc:
                        raise ArchiveError(
                            f"Could not remove the previous {target.name}: {exc}",
                            hint="Close the drawing in AutoCAD and retry.",
                        ) from exc

    def _restore_from_archive(self, root: Path, log_dir: Path, logger) -> None:
        archive = root / ARCHIVE_NAME
        logger.info("Restoring project from archive %s", archive)
        for entry in root.iterdir():
            if entry.name in {ARCHIVE_NAME, PREVIOUS_ARCHIVE_NAME}:
                continue
            if _is_log_dir(entry, root, log_dir):
                continue
            if entry.is_file() and entry.suffix.lower() in PRESERVED_SUFFIXES:
                continue
            # Everything else (including originals/) is superseded by the archive.
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

        with zipfile.ZipFile(archive, "r") as zip_file:
            zip_file.extractall(root)

    @staticmethod
    def _write_verified_archive(archive: Path, files: Sequence[Path]) -> None:
        """Write ``files`` to ``archive`` atomically, or raise :class:`ArchiveError`.

        The archive is the only backup of the sources once they have been
        moved out of the project root, so a failure here must stop the run
        rather than be logged and ignored.
        """

        partial = archive.with_name(archive.name + ".partial")
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                for path in files:
                    zip_file.write(path, arcname=path.name)
            with zipfile.ZipFile(partial, "r") as zip_file:
                bad = zip_file.testzip()
                names = sorted(zip_file.namelist())
            if bad is not None:
                raise ArchiveError(f"{archive.name} is corrupt after writing ({bad})")
            if names != sorted(path.name for path in files):
                raise ArchiveError(f"{archive.name} does not contain every source DWG")
            os.replace(partial, archive)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ArchiveError(
                f"Could not write {archive.name}: {exc}",
                hint="Check free disk space and that nothing holds the file open, then retry.",
            ) from exc
        finally:
            partial.unlink(missing_ok=True)

    def _create_archive_backup(
        self, root: Path, files: Sequence[Path], logger
    ) -> None:
        if not files:
            return

        archive = root / ARCHIVE_NAME
        self._write_verified_archive(archive, files)
        logger.info("Created archive backup %s with %d files", archive, len(files))

    def _restore_project_root(
        self, root: Path, originals: Sequence[Path], logger
    ) -> List[Path]:
        restored: List[Path] = []
        for source in originals:
            destination = root / source.name
            shutil.copy(source, destination)
            restored.append(destination)
        logger.info(
            "Copied %d DWG files from originals back into project root", len(restored)
        )
        return sorted(restored)

    def _ensure_directories(self, root: Path, directories: Iterable[str]) -> None:
        for directory in directories:
            (root / directory).mkdir(exist_ok=True)

    def _populate_working_directories(
        self, root: Path, files: Sequence[Path], from_originals: bool, logger
    ) -> None:
        originals = root / "originals"
        derevitized = root / "derevitized"

        count = 0
        if from_originals:
            for source in files:
                shutil.copy(source, derevitized / source.name)
                count += 1
                source.unlink(missing_ok=True)
            logger.info("Restored %d DWG files from originals", count)
        else:
            self._clear_directory(originals)
            for source in files:
                destination_original = originals / source.name
                shutil.copy(source, destination_original)
                shutil.copy(source, derevitized / source.name)
                source.unlink()
                count += 1
            logger.info("Copied %d DWG files", count)

    def _clear_directory(self, directory: Path) -> None:
        if not directory.exists():
            return
        for entry in directory.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def _cleanup_logs(self, log_dir: Path, logger) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)

        # Run logs and manifests are named per run (run_<timestamp>.log/.json)
        # and are kept as history. Only the per-job console dumps are cleared,
        # since those are regenerated wholesale by the run about to start.
        jobs = log_dir / "jobs"
        if jobs.is_dir():
            shutil.rmtree(jobs, ignore_errors=True)
        self._prune_old_run_logs(log_dir, logger)

    #: Runs (log + manifest) kept per project before the oldest are discarded.
    _MAX_RUN_LOGS = 20

    def _prune_old_run_logs(self, log_dir: Path, logger) -> None:
        """Keep the most recent runs so the folder cannot grow forever."""

        for pattern in ("run_*.log", "run_*.json"):
            history = sorted(log_dir.glob(pattern), key=lambda path: path.name, reverse=True)
            for stale in history[self._MAX_RUN_LOGS:]:
                try:
                    stale.unlink()
                except OSError as exc:  # pragma: no cover - locked by a viewer
                    logger.debug("Could not remove old run file %s: %s", stale, exc)


__all__ = [
    "Preprocessor",
    "ProjectInspection",
    "RunPlan",
    "deliverable_names",
    "inspect_project",
    "plan_run",
    "source_dwgs",
]
