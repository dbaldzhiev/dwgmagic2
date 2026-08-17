"""Typed error taxonomy for DWGMAGIC.

Stages and integrations raise these instead of bare exceptions so that the
CLI and GUI can present actionable messages and map failures to hints.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence


class DwgmagicError(Exception):
    """Base class for all DWGMAGIC errors."""

    #: Short, user-facing hint on how to resolve the problem (optional).
    hint: Optional[str] = None

    def __init__(self, message: str, *, hint: Optional[str] = None) -> None:
        super().__init__(message)
        if hint is not None:
            self.hint = hint

    def user_message(self) -> str:
        message = str(self)
        if self.hint:
            return f"{message}\nHint: {self.hint}"
        return message


class AutoCadNotFoundError(DwgmagicError):
    """Raised when accoreconsole.exe cannot be located."""

    def __init__(self, searched: Sequence[Path] = ()) -> None:
        detail = ""
        if searched:
            locations = "\n".join(f"  - {path}" for path in searched)
            detail = f" Searched:\n{locations}"
        super().__init__(
            f"Unable to locate accoreconsole.exe.{detail}",
            hint=(
                "Install AutoCAD, or point DWGMAGIC at it with --autocad-path, "
                "the DWGMAGIC_AUTOCAD_PATH environment variable, or the "
                "autocad_executable config key."
            ),
        )
        self.searched = tuple(searched)


class TrustedFolderError(DwgmagicError):
    """Raised when the trusted-folder validation fails."""


class NotAProjectError(DwgmagicError):
    """Raised when a folder does not look like a DWGMAGIC project.

    Guards the destructive preprocessing steps from running against an
    arbitrary directory (e.g. a folder dropped onto the GUI by mistake).
    """


class ScriptGenerationError(DwgmagicError):
    """Raised when AutoCAD script generation fails."""


class JobFailedError(DwgmagicError):
    """Raised (or reported) when an AutoCAD console job fails."""

    def __init__(
        self,
        job_name: str,
        returncode: Optional[int],
        *,
        reason: Optional[str] = None,
        output_tail: str = "",
    ) -> None:
        parts = [f"AutoCAD job '{job_name}' failed"]
        if returncode is not None:
            parts.append(f"with exit code {returncode}")
        if reason:
            parts.append(f"({reason})")
        message = " ".join(parts)
        if output_tail:
            message = f"{message}\n--- output tail ---\n{output_tail}"
        super().__init__(message)
        self.job_name = job_name
        self.returncode = returncode
        self.reason = reason
        self.output_tail = output_tail


class JobTimeoutError(JobFailedError):
    """Raised when an AutoCAD console job exceeds its allotted time."""

    def __init__(self, job_name: str, timeout: float) -> None:
        super().__init__(job_name, None, reason=f"timed out after {timeout:.0f}s")
        self.timeout = timeout


class PipelineCancelledError(DwgmagicError):
    """Raised when the user cancels a run."""

    def __init__(self) -> None:
        super().__init__("Pipeline run cancelled by user")


__all__ = [
    "DwgmagicError",
    "AutoCadNotFoundError",
    "TrustedFolderError",
    "NotAProjectError",
    "ScriptGenerationError",
    "JobFailedError",
    "JobTimeoutError",
    "PipelineCancelledError",
]
