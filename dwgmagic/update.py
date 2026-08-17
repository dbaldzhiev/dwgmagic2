"""Update checking against GitHub releases.

Deliberately dependency-free (urllib + stdlib json) and network-tolerant:
every failure path returns ``None`` so an offline machine never sees an
error because of the update check.

Applying an update swaps the whole PyInstaller bundle: the release ships a
self-contained zip, so there is no interpreter to locate and nothing to
install at update time. The download and the directory swap are handed to a
detached PowerShell process (:file:`updater.ps1`, copied to TEMP first) because
the running application cannot replace its own files.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from packaging.version import InvalidVersion, Version

import dwgmagic
from dwgmagic.settings import APP_ROOT, GITHUB_REPO

_API_TIMEOUT = 6.0

#: Release asset holding the onedir bundle, e.g. ``dwgmagic2-v1.0.0-win64.zip``.
_BUNDLE_SUFFIX = "-win64.zip"


@dataclass(slots=True)
class UpdateInfo:
    current: str
    latest: str
    url: str
    notes: str
    package_url: Optional[str] = None


def _parse_version(raw: str) -> Optional[Version]:
    try:
        return Version(raw.lstrip("vV"))
    except InvalidVersion:
        return None


def fetch_latest_release(repo: str = GITHUB_REPO) -> Optional[dict]:
    """Return the GitHub 'latest release' payload, or None on any failure."""

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"dwgmagic/{dwgmagic.__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_API_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - offline/ratelimit/404 all mean "no update info"
        return None


def find_bundle_asset(payload: dict) -> Optional[str]:
    """Download URL of the release's onedir bundle zip, if it published one."""

    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(_BUNDLE_SUFFIX):
            download = asset.get("browser_download_url")
            if download:
                return str(download)
    return None


def check_for_update(repo: str = GITHUB_REPO) -> Optional[UpdateInfo]:
    """Compare the installed version against the latest GitHub release."""

    payload = fetch_latest_release(repo)
    if not payload:
        return None

    latest_raw = str(payload.get("tag_name") or "")
    latest = _parse_version(latest_raw)
    current = _parse_version(dwgmagic.__version__)
    if latest is None or current is None or latest <= current:
        return None

    return UpdateInfo(
        current=str(current),
        latest=str(latest),
        url=str(payload.get("html_url") or f"https://github.com/{repo}/releases"),
        notes=str(payload.get("body") or "").strip(),
        package_url=find_bundle_asset(payload),
    )


def updater_script() -> Optional[Path]:
    """Path to the on-disk updater, if this copy of the app ships one."""

    script = APP_ROOT / "updater.ps1"
    return script if script.exists() else None


def launch_updater(package_url: str, *, relaunch_gui: bool = True) -> bool:
    """Start the detached updater; returns False if it is unavailable.

    The caller should exit promptly afterwards so the updater can replace the
    application files.
    """

    script = updater_script()
    if script is None:
        return False

    # Run from TEMP: the updater is inside the directory it is about to replace.
    staged = Path(tempfile.gettempdir()) / "dwgmagic2_updater.ps1"
    try:
        shutil.copyfile(script, staged)
    except OSError:
        return False

    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(staged),
        "-AppDir",
        str(APP_ROOT),
        "-PackageUrl",
        package_url,
    ]
    if relaunch_gui:
        args.append("-Relaunch")

    subprocess.Popen(  # noqa: S603 - launching our own updater
        args,
        cwd=os.environ.get("TEMP") or tempfile.gettempdir(),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )
    return True


__all__ = [
    "UpdateInfo",
    "check_for_update",
    "fetch_latest_release",
    "find_bundle_asset",
    "launch_updater",
]
