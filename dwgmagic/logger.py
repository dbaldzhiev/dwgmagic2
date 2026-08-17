"""Logging utilities that honour runtime settings.

Loggers are scoped to a single run (factory instance) so that loading a new
project in the GUI cannot leak file handlers pointing at the previous
project's log directory. All components share one chronological ``run.log``;
raw AutoCAD console output is dumped separately under ``logs/jobs/``.
"""
from __future__ import annotations

import itertools
import logging
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
    ) -> None:
        self.settings = settings
        self.extra_handlers = tuple(extra_handlers)
        self._scope = _scope or f"dwgmagic.run{next(_factory_counter)}"
        self._file_handler: Optional[logging.Handler] = None
        self._loggers: List[logging.Logger] = []

    def with_handlers(self, *handlers: logging.Handler) -> "LoggerFactory":
        """Return a new factory (same run scope) with additional handlers."""

        existing = list(self.extra_handlers)
        for handler in handlers:
            if handler not in existing:
                existing.append(handler)
        return LoggerFactory(self.settings, tuple(existing), _scope=self._scope)

    def _log_directory(self) -> Path:
        log_dir = self.settings.project_root / self.settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def _shared_file_handler(self) -> logging.Handler:
        if self._file_handler is None:
            log_path = self._log_directory() / "run.log"
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
