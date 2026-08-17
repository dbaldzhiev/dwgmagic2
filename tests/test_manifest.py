import json
from pathlib import Path

from jinja2 import DictLoader, Environment

from dwgmagic.core.context import ProjectConfig, ProjectContext, StageResult
from dwgmagic.integrations.autocad import AutoCadResult
from dwgmagic.manifest import build_manifest, build_summary_lines, write_manifest
from dwgmagic.settings import Settings


def make_context(tmp_path):
    settings = Settings(project_root=tmp_path, log_dir=Path("logs"))
    env = Environment(loader=DictLoader({}))
    return ProjectContext(config=ProjectConfig(settings=settings), environment=env)


def _sample_run(tmp_path):
    context = make_context(tmp_path)
    context.set("dwg_files", ["SheetA.dwg"])
    context.set(
        "autocad_results",
        [
            AutoCadResult(
                name="sheet:SheetA",
                returncode=0,
                stdout="",
                stderr="",
                command=("acc",),
                duration=12.5,
            ),
            AutoCadResult(
                name="merge",
                returncode=1,
                stdout="",
                stderr="",
                command=("acc",),
                duration=3.0,
                failure_reason="exit code 1",
            ),
        ],
    )
    results = [
        StageResult("preprocess", True, duration=1.0),
        StageResult("autocad", False, "merge batch failed", duration=15.5),
    ]
    return context, results


def test_build_manifest_contents(tmp_path):
    context, results = _sample_run(tmp_path)
    manifest = build_manifest(context, results)

    assert manifest["succeeded"] is False
    assert manifest["project"] == tmp_path.name
    assert [stage["name"] for stage in manifest["stages"]] == ["preprocess", "autocad"]
    assert manifest["jobs"][1]["failure_reason"] == "exit code 1"
    deliverables = {entry["path"]: entry["exists"] for entry in manifest["deliverables"]}
    assert str(tmp_path / f"{tmp_path.name}_MXR.dwg") in deliverables


def test_write_manifest_creates_json(tmp_path):
    context, results = _sample_run(tmp_path)
    path = write_manifest(context, results)
    assert path is not None and path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["app"] == "dwgmagic"
    assert payload["succeeded"] is False


def test_summary_lines_report_outcome_and_deliverables(tmp_path):
    context, results = _sample_run(tmp_path)
    (tmp_path / f"{tmp_path.name}_MXR.dwg").write_text("x" * 2048)

    lines = build_summary_lines(context, results)
    text = "\n".join(lines)
    assert "FAILED" in lines[0]
    assert "1 succeeded, 1 failed" in text
    assert "merge: exit code 1" in text
    assert f"✓ {tmp_path.name}_MXR.dwg (2 KB)" in text
    assert f"✗ {tmp_path.name}_MM.dwg (missing)" in text
