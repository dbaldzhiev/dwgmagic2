"""Logging utilities that honour runtime settings.

Loggers are scoped to a single run (factory instance) so that loading a new
project in the GUI cannot leak file handlers pointing at the previous
project's log directory. All components share one chronological ``run_<timestamp>.log``;
raw AutoCAD console output is dumped separately under ``logs/jobs/``.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from dwgmagic.settings import Settings

_factory_counter = itertools.count(1)


class _ComponentNameFilter(logging.Filter):
    """Rewrites the namespaced logger name back to its component label."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.name = record.name.rsplit(".", 1)[-1]
        return True


class LoggerFactory:
    """Creates loggers scoped to a project run."""

    def __init__(
        self,
        settings: Settings,
        extra_handlers: Tuple[logging.Handler, ...] = (),
        *,
        _scope: Optional[str] = None,
        _started: Optional[datetime] = None,
    ) -> None:
        self.settings = settings
        self.extra_handlers = tuple(extra_handlers)
        self._scope = _scope or f"dwgmagic.run{next(_factory_counter)}"
        self._started = _started or datetime.now()
        self._file_handler: Optional[logging.Handler] = None
        self._loggers: List[logging.Logger] = []

    def with_handlers(self, *handlers: logging.Handler) -> "LoggerFactory":
        """Return a new factory (same run scope) with additional handlers."""

        existing = list(self.extra_handlers)
        for handler in handlers:
            if handler not in existing:
                existing.append(handler)
        return LoggerFactory(
            self.settings, tuple(existing), _scope=self._scope, _started=self._started
        )

    @property
    def run_id(self) -> str:
        """Timestamp shared by this run's log and manifest file names."""

        return f"{self._started:%Y%m%d_%H%M%S}"

    def start_new_run(self) -> None:
        """Begin a new run in this scope: close the old log, name a new one.

        The GUI keeps one factory per loaded project. Without this every run
        in a session appended to the log named after the moment the project
        was opened.
        """

        self.close()
        started = datetime.now()
        if self._started is not None and f"{started:%Y%m%d_%H%M%S}" == self.run_id:
            # Two runs within the same second must not share a file.
            started = self._started + timedelta(seconds=1)
        self._started = started

    def _log_directory(self) -> Path:
        log_dir = self.settings.project_root / self.settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _shared_file_handler(self) -> logging.Handler:
        if self._file_handler is None:
            # One log per run, named like the manifests. Rotating by filename
            # avoids the unwinnable fight of trying to move or truncate a file
            # this process already holds open on Windows.
            log_path = self._log_directory() / f"run_{self.run_id}.log"
            handler = logging.FileHandler(
                log_path, encoding=self.settings.log_encoding, delay=True
            )
            handler.setLevel(getattr(logging, self.settings.log_level))
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            self._file_handler = handler
        return self._file_handler

    def create(self, name: str) -> logging.Logger:
        logger = logging.getLogger(f"{self._scope}.{name}")
        logger.setLevel(getattr(logging, self.settings.log_level))
        logger.propagate = False

        if not any(isinstance(f, _ComponentNameFilter) for f in logger.filters):
            logger.addFilter(_ComponentNameFilter())

        file_handler = self._shared_file_handler()
        if file_handler not in logger.handlers:
            logger.addHandler(file_handler)

        for handler in self.extra_handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)

        if logger not in self._loggers:
            self._loggers.append(logger)
        return logger

    def close(self) -> None:
        """Detach and close the run's file handler; call when a run finishes."""

        if self._file_handler is not None:
            for logger in self._loggers:
                logger.removeHandler(self._file_handler)
            try:
                self._file_handler.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
            self._file_handler = None


__all__ = ["LoggerFactory"]
