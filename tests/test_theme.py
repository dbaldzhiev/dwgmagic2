"""Tests for the GUI colour tokens.

The old UI mixed CustomTkinter ``(light, dark)`` tuples, bare hex strings and
literal Tk colour names, so most status colours adapted to only one appearance
mode. Every role must now resolve in both.
"""
import customtkinter as ctk
import pytest

from dwgmagic.gui import theme
from dwgmagic.gui.widgets import elide_path


@pytest.fixture(params=["Light", "Dark"])
def appearance(request):
    previous = ctk.get_appearance_mode()
    ctk.set_appearance_mode(request.param)
    yield request.param
    ctk.set_appearance_mode(previous)


def test_every_status_resolves_in_both_modes(appearance):
    for status in theme.STATUS_KEYS:
        value = theme.status_color(status)
        assert value.startswith("#") and len(value) == 7, f"{status} -> {value}"


def test_light_and_dark_differ_for_status_colors():
    ctk.set_appearance_mode("Light")
    light = {status: theme.status_color(status) for status in theme.STATUS_KEYS}
    ctk.set_appearance_mode("Dark")
    dark = {status: theme.status_color(status) for status in theme.STATUS_KEYS}
    # If a role were a single hardcoded value it would be unreadable in one mode.
    assert light["failed"] != dark["failed"]
    assert light["completed"] != dark["completed"]


def test_unknown_role_is_loud_rather_than_crashing():
    assert theme.color("nope.not.a.role") == "#ff00ff"


def test_pair_returns_both_variants():
    light, dark = theme.pair("danger")
    assert light.startswith("#") and dark.startswith("#")


def test_elide_path_keeps_root_and_leaf():
    long_path = r"\\SERVER.TEC\share\KACHAKOVI\some\deep\nesting\260817_testmerge"
    elided = elide_path(long_path, 40)
    assert len(elided) <= 41
    assert elided.endswith("260817_testmerge")
    assert "…" in elided


def test_elide_path_leaves_short_paths_alone():
    assert elide_path(r"C:\proj", 40) == r"C:\proj"
