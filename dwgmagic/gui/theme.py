"""Theme-aware colour tokens for the GUI.

The previous UI mixed three incompatible conventions: CustomTkinter
``(light, dark)`` tuples, bare hex strings, and literal Tk colour names
(``"gray"``, ``"orange"``, ``"green"``) painted onto a ``#333333`` Treeview.
Only the first adapts to the appearance mode, so status colours were
effectively unreadable in one theme or the other.

Everything here resolves against the *current* appearance mode, so callers ask
for a role (``status.failed``) rather than a colour, and re-resolve when the
mode changes.
"""
from __future__ import annotations

from typing import Dict, Tuple

import customtkinter as ctk

#: role -> (light, dark)
_TOKENS: Dict[str, Tuple[str, str]] = {
    # Surfaces
    "surface": ("#f4f4f4", "#2b2b2b"),
    "surface.raised": ("#ffffff", "#333333"),
    "surface.sunken": ("#e6e6e6", "#242424"),
    "border": ("#d0d0d0", "#404040"),
    # Text
    "text": ("#1a1a1a", "#f0f0f0"),
    "text.muted": ("#5c5c5c", "#a0a0a0"),
    "text.inverted": ("#ffffff", "#ffffff"),
    # Status. Darkened in light mode / lightened in dark mode so both keep
    # usable contrast against the surface they sit on.
    "status.pending": ("#6b6b6b", "#9a9a9a"),
    "status.queued": ("#6b6b6b", "#9a9a9a"),
    "status.running": ("#a06a00", "#e0a53d"),
    "status.completed": ("#1f7a4d", "#4cc38a"),
    "status.failed": ("#b3261e", "#f2827a"),
    "status.skipped": ("#8a8a8a", "#7a7a7a"),
    # Semantic accents
    "accent": ("#3b8ed0", "#1f6aa5"),
    "accent.hover": ("#36719f", "#144870"),
    "success": ("#1f7a4d", "#2fa572"),
    "success.hover": ("#17603c", "#20744f"),
    "warning": ("#a06a00", "#b8860b"),
    "warning.hover": ("#7d5300", "#8a6508"),
    "danger": ("#b3261e", "#b04a4a"),
    "danger.hover": ("#8c1d17", "#7a2f2f"),
    # Treeview
    "tree.background": ("#f4f4f4", "#333333"),
    "tree.foreground": ("#1a1a1a", "#f0f0f0"),
    "tree.heading": ("#dcdcdc", "#555555"),
    "tree.selected": ("#3b8ed0", "#1f538d"),
}

#: Status keys the work view and stage rows use, in lifecycle order.
STATUS_KEYS = ("pending", "queued", "running", "completed", "failed", "skipped")


def is_dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def color(role: str) -> str:
    """Resolve a role to a concrete colour for the current appearance mode."""

    try:
        light, dark = _TOKENS[role]
    except KeyError:  # pragma: no cover - programming error, but never crash the UI
        return "#ff00ff"
    return dark if is_dark() else light


def pair(role: str) -> Tuple[str, str]:
    """The raw ``(light, dark)`` tuple, for widgets that accept one directly."""

    return _TOKENS.get(role, ("#ff00ff", "#ff00ff"))


def status_color(status: str) -> str:
    return color(f"status.{status.lower()}")


__all__ = ["STATUS_KEYS", "color", "is_dark", "pair", "status_color"]
