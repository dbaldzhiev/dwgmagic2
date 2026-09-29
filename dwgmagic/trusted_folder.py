"""Trusted folder checking utilities and the registry-based fix."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Set, Tuple

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


#: Printed by tectonica's ``tecTest`` command. Seeing it proves the plugin
#: actually loaded; the mere absence of a NETLOAD error message does not.
PLUGIN_LOADED_MARKER = "HELLO! TEST! DEBUG TECTONICA!"

#: Name of the generated check script, and so of its ``logs/jobs`` dump.
_CHECK_SCRIPT_NAME = "trusted_folder_check.scr"

#: Checks that passed in this process, keyed by what they depend on. A pass
#: stays valid until the DLL, its location or the AutoCAD executable change,
#: so stage 1 of a run does not cold-start accoreconsole again right after
#: the GUI's preflight proved the same thing.
_verified: Set[Tuple[str, str, float]] = set()


def clear_verified_cache() -> None:
    """Forget earlier passes (used by an explicit re-check)."""

    _verified.clear()


def _cache_key(settings: Settings, dll_path: Path) -> Optional[Tuple[str, str, float]]:
    try:
        mtime = dll_path.stat().st_mtime
    except OSError:
        return None
    return (str(dll_path.resolve()), str(settings.autocad_executable), mtime)


class TrustedFolderChecker:
    """Validates that AutoCAD can NETLOAD tectonica.dll from the app folder.

    The check script is generated at runtime so it always points at the
    configured ``tectonica_path`` instead of a hardcoded location.
    """

    def __init__(self, runner: "AutoCadRunnerProtocol", *, use_cache: bool = True) -> None:
        self._runner = runner
        self._use_cache = use_cache

    def check(self, settings: Settings, logger) -> None:
        dll_path = settings.tectonica_path / "tectonica.dll"
        if not dll_path.exists():
            raise TrustedFolderError(
                f"tectonica.dll not found at {dll_path}",
                hint="Reinstall DWGMAGIC — releases ship tectonica.dll alongside the executable.",
            )

        key = _cache_key(settings, dll_path)
        if self._use_cache and key is not None and key in _verified:
            logger.info("Trusted folder check already passed for %s; skipping", dll_path)
            return

        script_path, is_temporary = self._resolve_script(settings)
        try:
            result = self._runner.run_script(script_path=script_path, logger=logger)
        finally:
            if is_temporary:
                # This check runs at startup, on every project load and as
                # stage 1 of every run; without this the temp files pile up
                # forever, because the static fallback script is never shipped.
                shutil.rmtree(script_path.parent, ignore_errors=True)
        if not result.succeeded:
            reason = getattr(result, "failure_reason", None) or f"exit code {result.returncode}"
            raise TrustedFolderError(
                f"Trusted folder validation failed ({reason})",
                hint=(
                    f"Add {settings.tectonica_path} to AutoCAD's trusted locations "
                    "(OPTIONS > Files > Trusted Locations, or the TRUSTEDPATHS variable)."
                ),
            )
        if is_temporary and PLUGIN_LOADED_MARKER not in (result.stdout or ""):
            # accoreconsole can decline the load (untrusted location, blocked
            # file) without printing any of the known failure markers.
            raise TrustedFolderError(
                "Trusted folder validation failed (tectonica did not respond to tecTest)",
                hint=(
                    f"Add {settings.tectonica_path} to AutoCAD's trusted locations "
                    "(OPTIONS > Files > Trusted Locations, or the TRUSTEDPATHS variable). "
                    "If it is already trusted, unblock tectonica.dll in its file properties."
                ),
            )
        if key is not None:
            _verified.add(key)

    def _resolve_script(self, settings: Settings) -> tuple[Path, bool]:
        """Use a pre-existing check script if present, else generate one.

        Returns the path and whether the caller owns it (and must delete it).
        """

        static_script = settings.tectonica_path / settings.trusted_folder_script
        if static_script.exists():
            return static_script, False

        from dwgmagic.script_generator import script_path_text

        folder = script_path_text(settings.tectonica_path, settings.script_encoding)
        body = f'netload "{folder}/tectonica.dll"\ntectest\n'
        try:
            body.encode(settings.script_encoding)
        except UnicodeEncodeError as exc:
            # errors="replace" used to turn these into "?", and the check then
            # failed as an untrusted folder, pointing at the wrong fix.
            raise TrustedFolderError(
                f"The DWGMAGIC folder {settings.tectonica_path} contains characters "
                f"that AutoCAD scripts in {settings.script_encoding!r} cannot express",
                hint=(
                    "Reinstall DWGMAGIC to a folder with plain (ASCII) characters, "
                    "or set script_encoding in the configuration."
                ),
            ) from exc
        # A fixed file name inside a private temp dir: the runner names its
        # logs/jobs dump after the script, and a random name left a new dump
        # behind on every launch (in the app folder, before a project is open).
        script = Path(tempfile.mkdtemp(prefix="dwgmagic_trusted_")) / _CHECK_SCRIPT_NAME
        script.write_text(body, encoding=settings.script_encoding)
        return script, True


class AutoCadRunnerProtocol:
    """Protocol subset used for type-checking."""

    def run_script(self, script_path: Path, logger, input_path: Optional[Path] = None) -> "AutoCadResult":  # pragma: no cover - interface
        raise NotImplementedError


__all__ = ["TrustedFolderChecker", "PLUGIN_LOADED_MARKER", "clear_verified_cache"]
