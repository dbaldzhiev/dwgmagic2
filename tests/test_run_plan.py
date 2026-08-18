"""Tests for the pre-run plan.

This drives the safety signal shown before a run, so it has to match what
``Preprocessor`` actually does. A plan that under-reports deletions is worse
than no plan at all.
"""
from dwgmagic.miscutil import plan_run


def test_fresh_project_only_clears_generated_directories(tmp_path):
    (tmp_path / "A101.dwg").write_text("dwg")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "derevitized").mkdir()
    (tmp_path / "notes.txt").write_text("keep me")

    plan = plan_run(tmp_path)

    assert plan.mode == "fresh"
    assert plan.dwg_count == 1
    deleted = {path.name for path in plan.deletes}
    assert deleted == {"scripts", "derevitized"}
    assert "notes.txt" not in deleted
    # A fresh run takes the original.zip backup.
    assert tmp_path / "original.zip" in plan.produces


def test_fresh_project_with_nothing_generated_deletes_nothing(tmp_path):
    (tmp_path / "A101.dwg").write_text("dwg")

    plan = plan_run(tmp_path)

    assert plan.deletes == []
    assert plan.is_destructive is False


def test_rerun_reports_everything_it_wipes(tmp_path):
    """A rerun means originals/ with no DWGs left at the root.

    Any .dwg at the top level — including a previous run's deliverables —
    makes ``inspect_project`` classify the folder as ``fresh`` instead, so a
    rerun folder cannot contain them by definition.
    """

    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / "A101.dwg").write_text("dwg")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "derevitized").mkdir()
    (tmp_path / "MANUALMERGE.bat").write_text("bat")
    (tmp_path / "dwgmagic.toml").write_text("config")

    plan = plan_run(tmp_path)

    assert plan.mode == "rerun"
    assert plan.is_destructive is True
    deleted = {path.name for path in plan.deletes}
    assert "MANUALMERGE.bat" in deleted
    assert "scripts" in deleted
    assert "derevitized" in deleted
    # Preserved: the DWG source of truth and config files.
    assert "originals" not in deleted
    assert "dwgmagic.toml" not in deleted


def test_a_completed_project_replans_as_archive(tmp_path):
    """The state a real second run starts from.

    After a successful run the folder holds original.zip plus the deliverables,
    and the archive branch wins — so the deliverables are correctly reported as
    about to be replaced.
    """

    import zipfile

    with zipfile.ZipFile(tmp_path / "original.zip", "w") as archive:
        archive.writestr("A101.dwg", "dwg")
    (tmp_path / "originals").mkdir()
    (tmp_path / "derevitized").mkdir()
    (tmp_path / f"{tmp_path.name}_MXR.dwg").write_text("previous deliverable")
    (tmp_path / f"{tmp_path.name}_MM.dwg").write_text("previous deliverable")

    plan = plan_run(tmp_path)

    assert plan.mode == "archive"
    deleted = {path.name for path in plan.deletes}
    assert f"{tmp_path.name}_MXR.dwg" in deleted
    assert f"{tmp_path.name}_MM.dwg" in deleted
    assert "original.zip" not in deleted


def test_archive_mode_preserves_the_archive_itself(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "original.zip", "w") as archive:
        archive.writestr("A101.dwg", "dwg")
    (tmp_path / "originals").mkdir()
    (tmp_path / "stale.dwg").write_text("superseded")
    (tmp_path / "settings.yaml").write_text("config")

    plan = plan_run(tmp_path)

    assert plan.mode == "archive"
    deleted = {path.name for path in plan.deletes}
    assert "original.zip" not in deleted, "the archive is the source of truth"
    assert "settings.yaml" not in deleted
    # originals/ is superseded by the archive and does get removed.
    assert "originals" in deleted
    assert "stale.dwg" in deleted


def test_non_project_yields_an_empty_plan(tmp_path):
    (tmp_path / "readme.txt").write_text("not a project")

    plan = plan_run(tmp_path)

    assert plan.mode == "invalid"
    assert plan.deletes == []
    assert plan.produces == []
    assert plan.is_destructive is False


def test_plan_names_the_expected_deliverables(tmp_path):
    (tmp_path / "A101.dwg").write_text("dwg")

    produced = {path.name for path in plan_run(tmp_path).produces}

    assert f"{tmp_path.name}_MXR.dwg" in produced
    assert f"{tmp_path.name}_MM.dwg" in produced
    assert {"originals", "derevitized", "scripts", "logs"} <= produced
