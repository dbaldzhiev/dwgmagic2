from pathlib import Path

from dwgmagic.logger import LoggerFactory
from dwgmagic.settings import Settings


def _log_run(factory, message):
    factory.create("TEST").info(message)
    path = Path(factory._file_handler.baseFilename)
    factory.close()
    return path


def test_each_run_gets_its_own_log_file(tmp_path):
    """Regression: every run in a GUI session appended to the log named after
    the moment the project was opened."""

    factory = LoggerFactory(Settings(project_root=tmp_path))
    first = _log_run(factory, "first run")
    factory.start_new_run()
    second = _log_run(factory, "second run")

    assert first != second
    assert "second run" not in first.read_text(encoding="utf-8")
    assert "first run" not in second.read_text(encoding="utf-8")
    assert second.name == f"run_{factory.run_id}.log"


def test_run_id_is_shared_with_derived_factories(tmp_path):
    factory = LoggerFactory(Settings(project_root=tmp_path))
    assert factory.with_handlers().run_id == factory.run_id
