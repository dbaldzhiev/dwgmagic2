import os
from pathlib import Path

import pytest

from dwgmagic.settings import APP_ROOT, load_settings


def test_load_settings_precedence(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""
log_level = "INFO"
log_dir = "from_config"
template_roots = ["CONFIG_ROOT"]
""")

    cli_template = tmp_path / "cli_templates"
    env = {
        "DWGMAGIC_LOG_LEVEL": "WARNING",
        "DWGMAGIC_TEMPLATE_ROOT": str(tmp_path / "env_templates"),
        "DWGMAGIC_VERBOSE": "1",
    }

    settings = load_settings(
        tmp_path,
        config_file=config,
        template_roots=[cli_template],
        autocad_path=tmp_path / "cli_acc.exe",
        env=env,
    )

    assert settings.log_level == "WARNING"
    assert settings.log_dir == Path("from_config")
    assert settings.verbose is True
    assert settings.autocad_executable == tmp_path / "cli_acc.exe"
    assert settings.template_roots == (cli_template,)


def test_resolve_template_roots_defaults(tmp_path):
    settings = load_settings(tmp_path, env={})
    roots = settings.resolve_template_roots()
    assert settings.tectonica_path in roots
    assert tmp_path in roots


def test_tectonica_path_defaults_to_app_root(tmp_path):
    settings = load_settings(tmp_path, env={})
    assert settings.tectonica_path == APP_ROOT


def test_tectonica_path_env_override(tmp_path):
    env = {"DWGMAGIC_TECTONICA_PATH": str(tmp_path / "plugin")}
    settings = load_settings(tmp_path, env=env)
    assert settings.tectonica_path == tmp_path / "plugin"


def test_execution_tuning_settings(tmp_path):
    env = {
        "DWGMAGIC_MAX_WORKERS": "4",
        "DWGMAGIC_JOB_TIMEOUT": "60",
        "DWGMAGIC_CONTINUE_ON_ERROR": "true",
        "DWGMAGIC_CHECK_UPDATES": "0",
    }
    settings = load_settings(tmp_path, env=env)
    assert settings.max_workers == 4
    assert settings.job_timeout == 60.0
    assert settings.continue_on_error is True
    assert settings.check_updates is False


def test_defaults_are_sane(tmp_path):
    settings = load_settings(tmp_path, env={})
    assert settings.max_workers == (os.cpu_count() or 4)
    assert settings.job_timeout == 1800.0
    assert settings.continue_on_error is False
    assert settings.check_updates is True
    assert settings.script_encoding == "cp1251"


def test_invalid_log_level_rejected(tmp_path):
    with pytest.raises(ValueError, match="Invalid log level"):
        load_settings(tmp_path, env={"DWGMAGIC_LOG_LEVEL": "LOUD"})


def test_invalid_job_timeout_rejected(tmp_path):
    with pytest.raises(ValueError, match="job_timeout"):
        load_settings(tmp_path, env={"DWGMAGIC_JOB_TIMEOUT": "-5"})
