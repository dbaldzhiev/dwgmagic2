"""Tests for persisted GUI state.

``GuiState`` is best-effort by design — it must degrade to defaults rather than
prevent the window from opening, which is what these tests pin down.
"""
import json

from dwgmagic.gui.state import GuiState


def _redirect_state(monkeypatch, tmp_path):
    path = tmp_path / "dwgmagic" / "gui.json"
    monkeypatch.setattr("dwgmagic.gui.state._state_path", lambda: path)
    return path


def test_round_trips_through_disk(monkeypatch, tmp_path):
    path = _redirect_state(monkeypatch, tmp_path)
    state = GuiState(geometry="800x600", appearance="Dark", max_workers=3)
    state.remember_project(tmp_path / "ProjectA")
    state.save()

    loaded = GuiState.load()
    assert loaded.geometry == "800x600"
    assert loaded.appearance == "Dark"
    assert loaded.max_workers == 3
    assert loaded.recent_projects == [str(tmp_path / "ProjectA")]
    assert path.exists()


def test_load_falls_back_to_defaults_on_corrupt_file(monkeypatch, tmp_path):
    path = _redirect_state(monkeypatch, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    state = GuiState.load()
    assert state.geometry == "1400x900"
    assert state.appearance == "System"
    assert state.recent_projects == []


def test_load_rejects_out_of_range_values(monkeypatch, tmp_path):
    path = _redirect_state(monkeypatch, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"geometry": 42, "appearance": "Neon", "recent_projects": "nope", "max_workers": 0}
        ),
        encoding="utf-8",
    )

    state = GuiState.load()
    assert state.geometry == "1400x900"
    assert state.appearance == "System"
    assert state.recent_projects == []
    assert state.max_workers is None


def test_remember_project_is_most_recent_first_and_deduplicated(tmp_path):
    state = GuiState()
    for name in ("A", "B", "A"):
        state.remember_project(tmp_path / name)

    assert state.recent_projects[0] == str(tmp_path / "A")
    assert state.recent_projects.count(str(tmp_path / "A")) == 1
    assert len(state.recent_projects) == 2


def test_recent_projects_are_capped(tmp_path):
    state = GuiState()
    for index in range(20):
        state.remember_project(tmp_path / f"P{index}")

    assert len(state.recent_projects) == 8
    assert state.recent_projects[0] == str(tmp_path / "P19")


def test_save_never_raises_when_the_location_is_unwritable(monkeypatch, tmp_path):
    """UI state is cosmetic; it must never break the app."""

    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setattr("dwgmagic.gui.state._state_path", lambda: blocker / "gui.json")

    GuiState().save()  # must not raise
