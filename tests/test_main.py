import main
from dwgmagic.cli import parse_args, run
from dwgmagic.settings import Settings


def test_build_environment_uses_bundled_templates(tmp_path):
    settings = Settings(project_root=tmp_path, tectonica_path=tmp_path / "missing")
    environment = main.build_environment(settings)

    template = environment.get_template("templates/project_script_template.tmpl")
    rendered = template.render(
        tectonica_path=settings.tectonica_path.as_posix(),
        project_name="SampleProject",
        xrefXplodeToggle=True,
        sheetNamesList=[],
        sheets=[],
    )

    assert "SampleProject_MXR.dwg" in rendered
    # NETLOAD paths are quoted so install locations with spaces work.
    assert 'netload "' in rendered


def test_parse_args_defaults_to_gui():
    args = parse_args([])
    assert args.cli is False
    assert args.path is None

    args = parse_args(["C:/some/project", "--autorun"])
    assert args.path == "C:/some/project"
    assert args.autorun is True


def test_cli_mode_requires_path(capsys):
    exit_code = run(["--cli"])
    assert exit_code == 2
