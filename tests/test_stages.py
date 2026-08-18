import zipfile
from pathlib import Path
from types import SimpleNamespace

from jinja2 import DictLoader, Environment

from dwgmagic.core.context import ProjectConfig, ProjectContext
from dwgmagic.core.stages import (
    AutoCadStage,
    PreprocessorStage,
    ScriptGenerationStage,
    TrustedFolderCheckStage,
)
from dwgmagic.integrations.autocad import AutoCadResult
from dwgmagic.logger import LoggerFactory
from dwgmagic.miscutil import Preprocessor, inspect_project
from dwgmagic.script_generator import ScriptGenerator
from dwgmagic.settings import Settings
from dwgmagic.trusted_folder import TrustedFolderChecker


def make_context(tmp_path):
    settings = Settings(project_root=tmp_path, log_dir=Path("logs"))
    env = Environment(loader=DictLoader({}))
    config = ProjectConfig(settings=settings)
    return ProjectContext(config=config, environment=env), settings


def test_trusted_folder_check_stage(tmp_path):
    context, settings = make_context(tmp_path)
    tectonica = tmp_path / "tectonica"
    tectonica.mkdir()
    (tectonica / "tectonica.dll").write_text("dll")
    script = tectonica / settings.trusted_folder_script
    script.write_text("test")
    settings.tectonica_path = tectonica

    calls = {}

    def run_script(script_path, logger, input_path=None, **kwargs):
        calls["script_path"] = script_path
        return AutoCadResult(name="trusted", returncode=0, stdout="", stderr="", command=(str(script_path),))

    checker = TrustedFolderChecker(SimpleNamespace(run_script=run_script))
    stage = TrustedFolderCheckStage(checker, LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is True
    assert calls["script_path"] == script


def test_trusted_folder_check_generates_script_when_missing(tmp_path):
    context, settings = make_context(tmp_path)
    tectonica = tmp_path / "tectonica"
    tectonica.mkdir()
    (tectonica / "tectonica.dll").write_text("dll")
    settings.tectonica_path = tectonica

    calls = {}

    def run_script(script_path, logger, input_path=None, **kwargs):
        # Read it while the check owns it: the generated script is temporary
        # and is deleted as soon as the run finishes.
        calls["script_path"] = script_path
        calls["body"] = Path(script_path).read_text(encoding=settings.script_encoding)
        return AutoCadResult(name="trusted", returncode=0, stdout="", stderr="", command=())

    checker = TrustedFolderChecker(SimpleNamespace(run_script=run_script))
    stage = TrustedFolderCheckStage(checker, LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is True
    assert "netload" in calls["body"]
    assert (tectonica / "tectonica.dll").as_posix() in calls["body"]
    # The check runs three-plus times per run; it must not leave files behind.
    assert not Path(calls["script_path"]).exists()


def test_trusted_folder_check_fails_without_dll(tmp_path):
    context, settings = make_context(tmp_path)
    settings.tectonica_path = tmp_path / "nowhere"

    checker = TrustedFolderChecker(SimpleNamespace(run_script=None))
    stage = TrustedFolderCheckStage(checker, LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is False
    assert "tectonica.dll" in (result.details or "")


def test_preprocessor_stage(tmp_path):
    context, settings = make_context(tmp_path)
    (tmp_path / "example.dwg").write_text("content")
    stage = PreprocessorStage(Preprocessor(), LoggerFactory(settings))
    result = stage.run(context)
    assert result.succeeded is True
    assert context.get("dwg_files") == ["example.dwg"]
    assert (tmp_path / "derevitized" / "example.dwg").exists()
    assert (tmp_path / "originals" / "example.dwg").exists()
    archive = tmp_path / "original.zip"
    assert archive.exists()
    with zipfile.ZipFile(archive) as zip_file:
        assert sorted(zip_file.namelist()) == ["example.dwg"]


def test_preprocessor_stage_refuses_non_project_folder(tmp_path):
    context, settings = make_context(tmp_path)
    precious = tmp_path / "documents"
    precious.mkdir()
    (precious / "thesis.docx").write_text("do not delete")
    (tmp_path / "notes.txt").write_text("misc")

    stage = PreprocessorStage(Preprocessor(), LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is False
    assert "does not look like a DWGMAGIC project" in (result.details or "")
    # Nothing was touched.
    assert precious.exists()
    assert (precious / "thesis.docx").exists()
    assert (tmp_path / "notes.txt").exists()


def test_preprocessor_stage_reruns_from_originals(tmp_path):
    context, settings = make_context(tmp_path)
    source = tmp_path / "rerun.dwg"
    source.write_text("content")
    stage = PreprocessorStage(Preprocessor(), LoggerFactory(settings))
    first_result = stage.run(context)
    assert first_result.succeeded is True

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "old.scr").write_text("old")
    derevitized_file = tmp_path / "derevitized" / "rerun.dwg"
    derevitized_file.write_text("stale")
    stale_root = tmp_path / "converted_output"
    stale_root.mkdir()
    (stale_root / "artifact.txt").write_text("old")

    rerun_context, rerun_settings = make_context(tmp_path)
    rerun_stage = PreprocessorStage(Preprocessor(), LoggerFactory(rerun_settings))
    rerun_result = rerun_stage.run(rerun_context)

    assert rerun_result.succeeded is True
    assert rerun_context.get("dwg_files") == ["rerun.dwg"]
    assert (tmp_path / "originals" / "rerun.dwg").exists()
    assert (tmp_path / "derevitized" / "rerun.dwg").exists()
    assert not (scripts_dir / "old.scr").exists()
    assert not stale_root.exists()
    assert not (tmp_path / "rerun.dwg").exists()


def test_preprocessor_stage_reruns_from_originals_without_archive(tmp_path):
    context, settings = make_context(tmp_path)
    (tmp_path / "rerun.dwg").write_text("content")
    stage = PreprocessorStage(Preprocessor(), LoggerFactory(settings))
    assert stage.run(context).succeeded is True

    # Simulate an environment where the archive backup is unavailable.
    (tmp_path / "original.zip").unlink()

    rerun_context, rerun_settings = make_context(tmp_path)
    rerun_stage = PreprocessorStage(Preprocessor(), LoggerFactory(rerun_settings))
    rerun_result = rerun_stage.run(rerun_context)

    assert rerun_result.succeeded is True
    assert rerun_context.get("dwg_files") == ["rerun.dwg"]
    assert (tmp_path / "originals" / "rerun.dwg").exists()
    assert (tmp_path / "derevitized" / "rerun.dwg").exists()


def test_preprocessor_stage_uses_archive_when_present(tmp_path):
    context, settings = make_context(tmp_path)
    archive = tmp_path / "original.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("archived.dwg", "archived")

    stray = tmp_path / "stray.txt"
    stray.write_text("obsolete")
    config_file = tmp_path / "dwgmagic.toml"
    config_file.write_text("max_workers = 1")

    stage = PreprocessorStage(Preprocessor(), LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is True
    assert context.get("dwg_files") == ["archived.dwg"]
    assert (tmp_path / "derevitized" / "archived.dwg").exists()
    assert (tmp_path / "originals" / "archived.dwg").exists()
    assert not (tmp_path / "archived.dwg").exists()
    assert not stray.exists()
    # Configuration files survive the archive restore.
    assert config_file.exists()

    with zipfile.ZipFile(archive) as zip_file:
        assert sorted(zip_file.namelist()) == ["archived.dwg"]


def test_inspect_project_modes(tmp_path):
    assert inspect_project(tmp_path).mode == "invalid"

    (tmp_path / "sheet.dwg").write_text("dwg")
    fresh = inspect_project(tmp_path)
    assert fresh.mode == "fresh"
    assert fresh.first_run is True
    assert fresh.dwg_names == ["sheet.dwg"]

    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / "sheet.dwg").write_text("dwg")
    assert inspect_project(tmp_path).first_run is False

    (tmp_path / "sheet.dwg").unlink()
    assert inspect_project(tmp_path).mode == "rerun"


def test_script_generation_stage(tmp_path):
    context, settings = make_context(tmp_path)
    context.set("dwg_files", ["SheetA.dwg", "SheetA-View-1.dwg"])
    env = Environment(
        loader=DictLoader(
            {
                "templates/project_script_template.tmpl": "{{ sheetNamesList|length }}",
                "templates/mmm_script_template.tmpl": "merge",
                "templates/manual_merge_bat_template.tmpl": "bat {{ acc }}",
                "templates/view_script_template.tmpl": "view {{ viewName }}",
                "templates/sheet_script_template.tmpl": "{% for view in viewsOnSheet %}xref path \"{{ view[:-4] }}\" \"./{{ view }}\"\n{% endfor %}",
            }
        ),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    context.environment = env
    stage = ScriptGenerationStage(ScriptGenerator(env), LoggerFactory(settings))
    result = stage.run(context)
    assert result.succeeded is True
    assert (tmp_path / "scripts" / "DWGMAGIC.scr").read_text(encoding="cp1251") == "1"
    sheet_script = (tmp_path / "scripts" / "SHEETA_SHEET.scr").read_text(encoding="cp1251")
    assert 'xref path "SheetA-View-1" "./SheetA-View-1.dwg"' in sheet_script
    assert (tmp_path / "scripts" / "SHEETA-VIEW-1.scr").exists()
    # The manual merge bat receives an AutoCAD path (or a PATH fallback).
    merge_bat = (tmp_path / "MANUALMERGE.bat").read_text(encoding="cp1251")
    assert merge_bat.startswith("bat ")
    assert "accoreconsole" in merge_bat.lower()


def test_log_cleanup_keeps_history_and_the_open_run_log(tmp_path):
    """Run logs rotate by filename rather than by moving a locked directory.

    The previous implementation renamed ``logs/`` to a timestamped backup, which
    on Windows always failed because the active run log inside it was already
    open — so a single ``run.log`` accumulated across every run forever.
    """

    settings = Settings(project_root=tmp_path, log_dir=Path("logs"))
    logs = tmp_path / "logs"
    logs.mkdir()
    previous = logs / "run_20260101_000000.log"
    previous.write_text("previous run")
    (logs / "jobs").mkdir()
    (logs / "jobs" / "1_SHEET.out.txt").write_text("stale console output")

    factory = LoggerFactory(settings)
    logger = factory.create("PREPROCESS")
    logger.info("run starting")
    active = Path(factory._file_handler.baseFilename)

    try:
        Preprocessor()._cleanup_logs(logs, logger)

        assert previous.exists(), "earlier run logs must be kept"
        assert active.exists(), "the log open in this process must survive"
        assert not (logs / "jobs").exists(), "stale job dumps are regenerated"
    finally:
        factory.close()

    assert active.stat().st_size > 0


def test_log_cleanup_prunes_the_oldest_run_logs(tmp_path):
    settings = Settings(project_root=tmp_path, log_dir=Path("logs"))
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(30):
        (logs / f"run_202601{index:02d}_000000.log").write_text("old")

    factory = LoggerFactory(settings)
    logger = factory.create("PREPROCESS")
    try:
        Preprocessor()._cleanup_logs(logs, logger)
    finally:
        factory.close()

    remaining = sorted(path.name for path in logs.glob("run_*.log"))
    assert len(remaining) == Preprocessor._MAX_RUN_LOGS
    # The newest are the ones kept.
    assert remaining[-1] == "run_20260129_000000.log"


class FakeCoordinator:
    """Coordinator double that optionally fails selected jobs."""

    def __init__(self, failing: set[str] | None = None, produce_outputs: bool = True):
        self.calls = []
        self.failing = failing or set()
        self.produce_outputs = produce_outputs

    def execute(self, jobs, logger, *, listener=None, cancel_event=None):
        batch = list(jobs)
        self.calls.append(batch)
        results = []
        for job in batch:
            failed = job.name in self.failing
            if self.produce_outputs and not failed:
                for output in job.expected_outputs:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text("output")
            results.append(
                AutoCadResult(
                    name=job.name,
                    returncode=1 if failed else 0,
                    stdout="",
                    stderr="",
                    command=(job.name,),
                )
            )
        return results


def _prepare_autocad_project(tmp_path, context):
    context.set("dwg_files", ["SheetA.dwg", "SheetA-View-1.dwg"])
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "DWGMAGIC.scr").write_text("merge")
    (scripts_dir / "SHEETA-VIEW-1.scr").write_text("view")
    (scripts_dir / "SHEETA_SHEET.scr").write_text("sheet")
    (tmp_path / "derevitized").mkdir()
    (tmp_path / "derevitized" / "SheetA.dwg").write_text("sheet")
    (tmp_path / "derevitized" / "SheetA-View-1.dwg").write_text("view")


def test_autocad_stage_builds_jobs(tmp_path):
    context, settings = make_context(tmp_path)
    _prepare_autocad_project(tmp_path, context)

    coordinator = FakeCoordinator()
    stage = AutoCadStage(coordinator, LoggerFactory(settings))
    result = stage.run(context)
    assert result.succeeded is True
    assert [
        [job.name for job in call] for call in coordinator.calls
    ] == [["view:SheetA-View-1"], ["sheet:SheetA"], ["merge"]]
    # Sheet jobs declare their expected outputs.
    sheet_job = coordinator.calls[1][0]
    assert sheet_job.expected_outputs == (
        tmp_path / "derevitized" / "SheetA_xrefed.dwg",
    )


def test_autocad_stage_publishes_the_whole_plan_before_running(tmp_path):
    """The job total must be known up front, or progress runs backwards.

    Jobs used to be counted as each batch was queued, so the denominator grew
    mid-run: after the view batch the bar showed ~75%, then the sheet batch
    queued and it dropped to ~67%.
    """

    context, settings = make_context(tmp_path)
    _prepare_autocad_project(tmp_path, context)

    planned = []
    queue_order = []

    class PlanRecordingCoordinator(FakeCoordinator):
        def execute(self, jobs, logger, listener=None, cancel_event=None):
            for job in jobs:
                queue_order.append(job.name)
            return super().execute(jobs, logger, listener=listener, cancel_event=cancel_event)

    listener = SimpleNamespace(
        on_jobs_planned=lambda batches: planned.append(batches),
        on_job_queued=lambda job: None,
    )
    context.set("autocad_listener", listener)

    stage = AutoCadStage(PlanRecordingCoordinator(), LoggerFactory(settings))
    assert stage.run(context).succeeded is True

    assert len(planned) == 1, "the plan is published exactly once"
    batches = planned[0]
    assert [batch.label for batch in batches] == ["views", "sheets", "merge"]

    total = sum(len(batch.job_names) for batch in batches)
    assert total == len(queue_order), "planned total must match what actually ran"
    # And it is known before the first job is dispatched.
    assert total == 3


def test_autocad_stage_fails_when_job_fails(tmp_path):
    context, settings = make_context(tmp_path)
    _prepare_autocad_project(tmp_path, context)

    coordinator = FakeCoordinator(failing={"sheet:SheetA"})
    stage = AutoCadStage(coordinator, LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is False
    assert "sheet:SheetA" in (result.details or "")
    # The merge batch never ran.
    assert [[job.name for job in call] for call in coordinator.calls] == [
        ["view:SheetA-View-1"],
        ["sheet:SheetA"],
    ]


def test_autocad_stage_fails_when_outputs_missing(tmp_path):
    context, settings = make_context(tmp_path)
    _prepare_autocad_project(tmp_path, context)

    coordinator = FakeCoordinator(produce_outputs=False)
    stage = AutoCadStage(coordinator, LoggerFactory(settings))
    result = stage.run(context)

    assert result.succeeded is False
    assert "expected outputs" in (result.details or "")


def test_autocad_stage_continue_on_error(tmp_path):
    context, settings = make_context(tmp_path)
    settings.continue_on_error = True
    _prepare_autocad_project(tmp_path, context)

    coordinator = FakeCoordinator(failing={"sheet:SheetA"})
    stage = AutoCadStage(coordinator, LoggerFactory(settings))
    result = stage.run(context)

    # All batches ran despite the failure, and the failure is reported.
    assert [[job.name for job in call] for call in coordinator.calls] == [
        ["view:SheetA-View-1"],
        ["sheet:SheetA"],
        ["merge"],
    ]
    assert result.succeeded is True
    assert result.data["failed_jobs"] == ["sheet:SheetA"]
