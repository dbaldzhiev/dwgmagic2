"""Preprocessing utilities operating on explicit project context.

Destructive cleanup only happens after the folder has been positively
identified as a DWGMAGIC project (DWGs at top level, a populated
``originals/`` folder, or an ``original.zip`` archive). Anything else raises
:class:`~dwgmagic.errors.NotAProjectError` without touching the folder.
"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from dwgmagic.core.context import ProjectContext
from dwgmagic.errors import NotAProjectError

#: Configuration files that survive every cleanup pass.
PRESERVED_SUFFIXES = {".toml", ".tml", ".yaml", ".yml", ".json"}
PRESERVED_NAMES = {"originals", "original.zip"}


@dataclass(slots=True)
class ProjectInspection:
    """Non-destructive snapshot of what a run would operate on."""

    root: Path
    #: ``archive`` | ``fresh`` | ``rerun`` | ``invalid``
    mode: str
    dwg_names: List[str] = field(default_factory=list)
    #: True when the folder has never been processed by DWGMAGIC before.
    first_run: bool = False

    @property
    def is_project(self) -> bool:
        return self.mode != "invalid"

    def describe(self) -> str:
        if not self.is_project:
            return "No DWG files, originals/ folder, or original.zip archive found."
        source = {
            "archive": "original.zip archive",
            "fresh": "DWG files in the folder",
            "rerun": "originals/ folder (rerun)",
        }[self.mode]
        return f"{len(self.dwg_names)} DWG file(s) from {source}"


def inspect_project(root: Path) -> ProjectInspection:
    """Classify a folder without modifying it."""

    archive = root / "original.zip"
    if archive.exists():
        try:
            with zipfile.ZipFile(archive, "r") as zip_file:
                archived = [
                    name
                    for name in zip_file.namelist()
                    if name.lower().endswith(".dwg") and "/" not in name.strip("/")
                ]
        except (zipfile.BadZipFile, OSError):
            archived = []
        if archived:
            return ProjectInspection(root=root, mode="archive", dwg_names=sorted(archived))

    root_files = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".dwg"
    )
    if root_files:
        first_run = not (root / "originals").exists() and not archive.exists()
        return ProjectInspection(
            root=root, mode="fresh", dwg_names=root_files, first_run=first_run
        )

    originals = root / "originals"
    if originals.exists():
        original_files = sorted(
            entry.name
            for entry in originals.iterdir()
            if entry.is_file() and entry.suffix.lower() == ".dwg"
        )
        if original_files:
            return ProjectInspection(root=root, mode="rerun", dwg_names=original_files)

    return ProjectInspection(root=root, mode="invalid")


def _is_preserved(entry: Path) -> bool:
    if entry.name in PRESERVED_NAMES:
        return True
    return entry.is_file() and entry.suffix.lower() in PRESERVED_SUFFIXES


@dataclass(slots=True)
class Preprocessor:
    """Prepares the project directory for processing."""

    def preprocess(self, context: ProjectContext, logger) -> List[str]:
        project_root = context.project_root
        inspection = inspect_project(project_root)
        if not inspection.is_project:
            raise NotAProjectError(
                f"{project_root} does not look like a DWGMAGIC project: "
                f"{inspection.describe()}",
                hint="Point DWGMAGIC at a folder containing the exported DWG files.",
            )
        logger.info("Project source: %s", inspection.describe())

        if inspection.mode == "archive":
            self._restore_from_archive(project_root, logger)

        dwg_files, from_originals = self._collect_dwg_files(project_root)
        if not dwg_files:
            raise NotAProjectError("No DWG files found in project root")

        self._cleanup_previous_run(project_root, from_originals, logger)
        if from_originals:
            dwg_files = self._restore_project_root(project_root, dwg_files, logger)
        else:
            dwg_files = sorted(
                entry
                for entry in project_root.iterdir()
                if entry.suffix.lower() == ".dwg" and entry.is_file()
            )
            self._create_archive_backup(project_root, dwg_files, logger)

        self._cleanup_logs(project_root / context.settings.log_dir, logger)
        self._ensure_directories(project_root, ("scripts", "originals", "derevitized"))
        self._populate_working_directories(project_root, dwg_files, from_originals, logger)
        return [path.name for path in dwg_files]

    def _collect_dwg_files(self, root: Path) -> Tuple[List[Path], bool]:
        root_files = sorted(
            entry for entry in root.iterdir() if entry.suffix.lower() == ".dwg" and entry.is_file()
        )
        if root_files:
            return root_files, False

        originals = root / "originals"
        if originals.exists():
            original_files = sorted(
                entry for entry in originals.iterdir() if entry.suffix.lower() == ".dwg" and entry.is_file()
            )
            if original_files:
                return original_files, True

        return [], False

    def _cleanup_previous_run(self, root: Path, rerun: bool, logger) -> None:
        originals = root / "originals"
        if rerun and originals.exists():
            logger.info("Detected previous run; restoring project from originals")
            for entry in root.iterdir():
                if _is_preserved(entry):
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            logger.info("Previous preprocessing artifacts removed")
        else:
            # Remove known artifact directories if present without touching DWG sources.
            for directory in ("scripts", "derevitized", "logs"):
                target = root / directory
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)

    def _restore_from_archive(self, root: Path, logger) -> None:
        archive = root / "original.zip"
        logger.info("Restoring project from archive %s", archive)
        for entry in root.iterdir():
            if entry == archive:
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

    def _create_archive_backup(
        self, root: Path, files: Sequence[Path], logger
    ) -> None:
        if not files:
            return

        archive = root / "original.zip"
        try:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                for path in files:
                    zip_file.write(path, arcname=path.name)
        except OSError as exc:  # pragma: no cover - disk issues are environment-specific
            logger.warning("Unable to create archive %s: %s", archive, exc)
        else:
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
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = log_dir.with_name(f"{log_dir.name}_backup_{timestamp}")
        try:
            log_dir.rename(backup_dir)
        except OSError as exc:
            # Expected whenever the active run.log lives inside it already.
            logger.info("Reusing existing log directory %s", log_dir)
            logger.debug("Log directory rename skipped: %s", exc)
            for entry in log_dir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    try:
                        entry.unlink()
                    except PermissionError as remove_exc:
                        logger.debug("Skipping locked log file %s: %s", entry, remove_exc)
                    except OSError as remove_exc:
                        logger.warning("Unable to remove %s: %s", entry, remove_exc)
        finally:
            log_dir.mkdir(parents=True, exist_ok=True)


__all__ = ["Preprocessor", "ProjectInspection", "inspect_project"]
