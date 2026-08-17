"""Trusted folder checking utilities and the registry-based fix."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from dwgmagic.errors import TrustedFolderError
from dwgmagic.settings import Settings


def merge_trusted_paths(current: Optional[str], new_path: str) -> Optional[str]:
    """Append ``new_path`` to a TRUSTEDPATHS value; None if already present."""

    def _norm(entry: str) -> str:
        return os.path.normcase(os.path.normpath(entry.strip().rstrip("\\/")))

    entries = [entry for entry in (current or "").split(";") if entry.strip()]
    if any(_norm(entry) == _norm(new_path) for entry in entries):
        return None
    entries.append(str(new_path))
    return ";".join(entries)


def add_trusted_path(path: Path) -> List[str]:
    """Append ``path`` to TRUSTEDPATHS of every AutoCAD profile in HKCU.

    accoreconsole reads the per-user profile variables, so no elevation is
    needed. Returns the profiles that were modified (empty when every profile
    already trusted the path); raises :class:`TrustedFolderError` when no
    AutoCAD profiles exist at all.
    """

    import winreg

    modified: List[str] = []
    found_any_profile = False
    try:
        acad_root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Autodesk\AutoCAD")
    except OSError as exc:
        raise TrustedFolderError(
            "No AutoCAD registry profiles found for the current user",
            hint="Start AutoCAD once so it creates its profile, then retry.",
        ) from exc

    with acad_root:
        for i in range(winreg.QueryInfoKey(acad_root)[0]):
            release = winreg.EnumKey(acad_root, i)
            try:
                release_key = winreg.OpenKey(acad_root, release)
            except OSError:
                continue
            with release_key:
                for j in range(winreg.QueryInfoKey(release_key)[0]):
                    product = winreg.EnumKey(release_key, j)
                    profiles_path = f"{release}\\{product}\\Profiles"
                    try:
                        profiles_key = winreg.OpenKey(acad_root, profiles_path)
                    except OSError:
                        continue
                    with profiles_key:
                        for k in range(winreg.QueryInfoKey(profiles_key)[0]):
                            profile = winreg.EnumKey(profiles_key, k)
                            variables_path = f"{profiles_path}\\{profile}\\Variables"
                            try:
                                variables_key = winreg.OpenKey(
                                    acad_root, variables_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
                                )
                            except OSError:
                                continue
                            with variables_key:
                                found_any_profile = True
                                try:
                                    current, _ = winreg.QueryValueEx(variables_key, "TRUSTEDPATHS")
                                except OSError:
                                    current = ""
                                merged = merge_trusted_paths(str(current), str(path))
                                if merged is None:
                                    continue
                                winreg.SetValueEx(
                                    variables_key, "TRUSTEDPATHS", 0, winreg.REG_SZ, merged
                                )
                                modified.append(f"{release}\\{product} [{profile}]")

    if not found_any_profile:
        raise TrustedFolderError(
            "No AutoCAD profiles with variables found in the registry",
            hint="Start AutoCAD once so it creates its profile, then retry.",
        )
    return modified


class TrustedFolderChecker:
    """Validates that AutoCAD can NETLOAD tectonica.dll from the app folder.

    The check script is generated at runtime so it always points at the
    configured ``tectonica_path`` instead of a hardcoded location.
    """

    def __init__(self, runner: "AutoCadRunnerProtocol") -> None:
        self._runner = runner

    def check(self, settings: Settings, logger) -> None:
        dll_path = settings.tectonica_path / "tectonica.dll"
        if not dll_path.exists():
            raise TrustedFolderError(
                f"tectonica.dll not found at {dll_path}",
                hint="Reinstall DWGMAGIC — releases ship tectonica.dll alongside the executable.",
            )

        script_path = self._resolve_script(settings)
        result = self._runner.run_script(script_path=script_path, logger=logger)
        if not result.succeeded:
            reason = getattr(result, "failure_reason", None) or f"exit code {result.returncode}"
            raise TrustedFolderError(
                f"Trusted folder validation failed ({reason})",
                hint=(
                    f"Add {settings.tectonica_path} to AutoCAD's trusted locations "
                    "(OPTIONS > Files > Trusted Locations, or the TRUSTEDPATHS variable)."
                ),
            )

    def _resolve_script(self, settings: Settings) -> Path:
        """Use a pre-existing check script if present, else generate one."""

        static_script = settings.tectonica_path / settings.trusted_folder_script
        if static_script.exists():
            return static_script

        dll = (settings.tectonica_path / "tectonica.dll").as_posix()
        handle = tempfile.NamedTemporaryFile(
            "w",
            suffix=".scr",
            prefix="dwgmagic_trusted_",
            delete=False,
            encoding=settings.script_encoding,
            errors="replace",
        )
        with handle as fh:
            fh.write(f'netload "{dll}"\n')
        return Path(handle.name)


class AutoCadRunnerProtocol:
    """Protocol subset used for type-checking."""

    def run_script(self, script_path: Path, logger, input_path: Optional[Path] = None) -> "AutoCadResult":  # pragma: no cover - interface
        raise NotImplementedError


__all__ = ["TrustedFolderChecker"]
