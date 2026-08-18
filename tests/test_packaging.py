"""Guards on the files that ship alongside the executables.

These are not exercised by importing the package, so nothing else would catch
a regression in them until a user tried to update.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_SCRIPTS = sorted((REPO_ROOT / "scripts").glob("*.ps1"))


def test_powershell_scripts_are_discovered():
    assert POWERSHELL_SCRIPTS, "expected scripts/*.ps1 to exist"


@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS, ids=lambda p: p.name)
def test_powershell_scripts_are_pure_ascii(script: Path):
    """Windows PowerShell 5.1 reads a BOM-less .ps1 as system-codepage ANSI.

    A single UTF-8 character therefore arrives mangled: an em dash becomes a
    smart quote, which unbalances string parsing and kills the whole file.
    updater.ps1 is run by 5.1 on every in-app update, so this broke updating
    for everyone on the shipping version and could only be fixed by a manual
    reinstall.
    """

    raw = script.read_bytes()
    offenders = {byte for byte in raw if byte > 127}
    assert not offenders, (
        f"{script.name} contains non-ASCII bytes {sorted(offenders)}. "
        "Use ASCII (e.g. '-' instead of an em dash)."
    )


def test_updater_is_shipped_next_to_the_executables():
    """release.ps1 copies it beside the exe because APP_ROOT is that folder."""

    release = (REPO_ROOT / "scripts" / "release.ps1").read_text(encoding="utf-8")
    assert "updater.ps1" in release
    assert 'Join-Path $DistDir "updater.ps1"' in release
