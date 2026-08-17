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
        project_path=r"\\server\share\SampleProject",
        xrefXplodeToggle=True,
        sheetNamesList=[],
        sheets=[],
    )

    assert "SampleProject_MXR.dwg" in rendered
    # NETLOAD paths are quoted so install locations with spaces work.
    assert 'netload "' in rendered


def test_generated_scripts_address_outputs_absolutely(tmp_path):
    """Windows cannot give a process a UNC working directory.

    A relative output path in a .scr therefore resolves outside a project that
    lives on a network share: AutoCAD reports success and writes nothing where
    the pipeline looks for it.
    """

    settings = Settings(project_root=tmp_path, tectonica_path=tmp_path)
    environment = main.build_environment(settings)
    project_path = r"\\server\share\Proj"
    common = {
        "tectonica_path": tmp_path.as_posix(),
        "project_name": "Proj",
        "project_path": project_path,
        "xrefXplodeToggle": True,
    }

    sheet = environment.get_template("templates/sheet_script_template.tmpl").render(
        sheetName="1", viewsOnSheet=["1-View-1.dwg"], **common
    )
    assert f'save "{project_path}\\derevitized\\1_xrefed.dwg"' in sheet
    # Xrefs stay relative on purpose — they resolve against the host drawing,
    # and absolute paths would be baked into the delivered DWG.
    assert 'xref path "1-View-1" "./1-View-1.dwg"' in sheet

    project = environment.get_template("templates/project_script_template.tmpl").render(
        sheetNamesList=["1.dwg"], sheets=[], **common
    )
    assert f'"{project_path}\\derevitized\\1_xrefed.dwg"' in project
    assert f'"{project_path}\\Proj_MXR.dwg"' in project
    assert f'"{project_path}\\Proj_MM.dwg"' in project

    merge = environment.get_template("templates/mmm_script_template.tmpl").render(
        sheets=[], **common
    )
    assert f'"{project_path}\\Proj_MMM.dwg"' in merge


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
