"""Runtime settings and configuration loading for DWGMAGIC."""
from __future__ import annotations

import dataclasses
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

try:  # Python 3.11
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


def _app_root() -> Path:
    """Directory holding the application, i.e. where tectonica.dll must live.

    In a PyInstaller bundle the package lives under ``_internal/``, so deriving
    this from ``__file__`` would point one level too deep — and AutoCAD would be
    asked to trust ``_internal`` rather than the install directory.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


#: Directory containing the application itself (repo checkout or installed copy).
#: tectonica.dll, trustedFolderCheck.scr, and the bundled templates live here,
#: so the app is relocatable instead of assuming a fixed install path.
APP_ROOT = _app_root()

#: Newest first: tectonica.dll targets .NET 8 (AutoCAD 2025+), so when several
#: releases are installed the newest one is the one that can load it. A few
#: future years are listed so a new AutoCAD release is found without an update.
DEFAULT_AUTOCAD_CANDIDATES = tuple(
    Path(f"C:/Program Files/Autodesk/AutoCAD {year}/accoreconsole.exe")
    for year in range(2030, 2016, -1)
)

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

#: Upper bound on the default parallelism. Each accoreconsole takes several
#: hundred MB and competes for disk; one per CPU on a 32-thread workstation
#: is slower overall, not faster.
_MAX_DEFAULT_WORKERS = 8
#: Physical memory budgeted per accoreconsole when sizing the default.
_MEMORY_PER_WORKER = 1024**3


def _total_memory_bytes() -> Optional[int]:
    """Physical memory, or None when it cannot be determined."""

    if sys.platform == "win32":
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except Exception:  # noqa: BLE001 - sizing is best-effort
            return None
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None


def default_max_workers() -> int:
    """Parallel AutoCAD jobs when nothing is configured.

    The CPU count, capped at :data:`_MAX_DEFAULT_WORKERS` and at roughly one
    job per GB of physical memory.
    """

    workers = min(os.cpu_count() or 4, _MAX_DEFAULT_WORKERS)
    memory = _total_memory_bytes()
    if memory:
        workers = min(workers, memory // _MEMORY_PER_WORKER)
    return max(1, int(workers))


_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off", ""}


def _as_bool(value: object, name: str) -> bool:
    """Interpret a config/env value as a boolean.

    ``bool("false")`` is True, so a YAML ``continue_on_error: "false"`` used
    to switch the option on.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise ValueError(f"Invalid value for {name}: {value!r} (expected true/false)")


def _as_number(value: object, name: str, kind: type) -> float | int:
    try:
        return kind(value)
    except (TypeError, ValueError):
        expected = "a whole number" if kind is int else "a number"
        raise ValueError(f"Invalid value for {name}: {value!r} (expected {expected})") from None

GITHUB_REPO = "dbaldzhiev/dwgmagic2"


@dataclass(slots=True)
class Settings:
    project_root: Path
    template_roots: Tuple[Path, ...] = field(default_factory=tuple)
    tectonica_path: Path = APP_ROOT
    trusted_folder_script: Path = Path("trustedFolderCheck.scr")
    autocad_executable: Optional[Path] = None
    autocad_candidates: Tuple[Path, ...] = DEFAULT_AUTOCAD_CANDIDATES
    verbose: bool = False
    log_dir: Path = Path("logs")
    log_encoding: str = "utf-8"
    log_level: str = "DEBUG"
    xref_xplode_toggle: bool = True
    #: Run FixSpotElevations.lsp (MCPFIXSPOT) on each sheet before merge.
    fix_spot_elevations: bool = False
    #: Maximum simultaneous accoreconsole processes. Defaults to
    #: :func:`default_max_workers`; set it explicitly (config/env/GUI) to override.
    max_workers: int = field(default_factory=default_max_workers)
    #: Per-job timeout in seconds; a hung console job is killed after this.
    job_timeout: float = 1800.0
    #: Encoding used when writing generated .scr/.bat files.
    script_encoding: str = "cp1251"
    #: Keep running remaining jobs/stages when an AutoCAD job fails.
    continue_on_error: bool = False
    #: Query GitHub for a newer release on GUI startup.
    check_updates: bool = True

    def with_project_root(self, root: Path) -> "Settings":
        return dataclasses.replace(self, project_root=root)

    def resolve_template_roots(self) -> Tuple[Path, ...]:
        if self.template_roots:
            return self.template_roots

        candidates = []
        if self.tectonica_path:
            candidates.append(self.tectonica_path)
        if self.project_root and self.project_root not in candidates:
            candidates.append(self.project_root)
        return tuple(candidates)


def _read_config_file(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    if suffix in {".toml", ".tml"}:
        if tomllib is None:
            raise RuntimeError("tomllib is required to read TOML configuration files")
        with path.open("rb") as fh:
            return tomllib.load(fh)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PyYAML is required to read YAML configuration files") from exc
        with path.open("r", encoding="utf-8") as fh:  # pragma: no cover - optional dependency
            return yaml.safe_load(fh) or {}
    else:
        raise ValueError(f"Unsupported configuration format: {suffix}")


def _normalise_sequence(value: object) -> Tuple[Path, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, Path)):
        return (Path(value),)
    return tuple(Path(item) for item in value)  # type: ignore[arg-type]


def _validated_log_level(value: object) -> str:
    level = str(value).upper()
    if level not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid log level {value!r}; expected one of {sorted(_VALID_LOG_LEVELS)}"
        )
    # Not an assert: validation must survive `python -O`.
    if not isinstance(getattr(logging, level, None), int):
        raise ValueError(f"Log level {level!r} is not exposed by the logging module")
    return level


def load_settings(
    project_root: Path,
    *,
    verbose: bool = False,
    config_file: Optional[Path] = None,
    template_roots: Optional[Sequence[Path]] = None,
    autocad_path: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Settings:
    env = dict(env or os.environ)
    data: Dict[str, object] = {}

    if config_file:
        config_data = _read_config_file(config_file)
        data.update(config_data)

    def _env_path(name: str) -> Optional[Path]:
        value = env.get(f"DWGMAGIC_{name}")
        return Path(value) if value else None

    def _env_bool(name: str) -> Optional[bool]:
        value = env.get(f"DWGMAGIC_{name}")
        if value is None:
            return None
        return _as_bool(value, f"DWGMAGIC_{name}")

    # Environment overrides
    env_template = env.get("DWGMAGIC_TEMPLATE_ROOT")
    if env_template:
        data["template_roots"] = [Path(part) for part in env_template.split(os.pathsep) if part]

    env_autocad = _env_path("AUTOCAD_PATH")
    if env_autocad:
        data["autocad_executable"] = env_autocad

    env_tectonica = _env_path("TECTONICA_PATH")
    if env_tectonica:
        data["tectonica_path"] = env_tectonica

    env_log_dir = env.get("DWGMAGIC_LOG_DIR")
    if env_log_dir:
        data["log_dir"] = Path(env_log_dir)

    env_verbose = _env_bool("VERBOSE")
    if env_verbose is not None:
        data["verbose"] = env_verbose

    env_xref = _env_bool("XREF_EXPLODE")
    if env_xref is not None:
        data["xref_xplode_toggle"] = env_xref

    env_fix_spot = _env_bool("FIX_SPOT_ELEVATIONS")
    if env_fix_spot is not None:
        data["fix_spot_elevations"] = env_fix_spot

    env_continue = _env_bool("CONTINUE_ON_ERROR")
    if env_continue is not None:
        data["continue_on_error"] = env_continue

    env_updates = _env_bool("CHECK_UPDATES")
    if env_updates is not None:
        data["check_updates"] = env_updates

    env_log_level = env.get("DWGMAGIC_LOG_LEVEL")
    if env_log_level:
        data["log_level"] = env_log_level.upper()

    env_log_encoding = env.get("DWGMAGIC_LOG_ENCODING")
    if env_log_encoding:
        data["log_encoding"] = env_log_encoding

    env_max_workers = env.get("DWGMAGIC_MAX_WORKERS")
    if env_max_workers:
        data["max_workers"] = _as_number(env_max_workers, "DWGMAGIC_MAX_WORKERS", int)

    env_job_timeout = env.get("DWGMAGIC_JOB_TIMEOUT")
    if env_job_timeout:
        data["job_timeout"] = _as_number(env_job_timeout, "DWGMAGIC_JOB_TIMEOUT", float)

    # CLI overrides
    if template_roots:
        data["template_roots"] = list(template_roots)
    if autocad_path:
        data["autocad_executable"] = autocad_path

    max_workers = max(
        1, int(_as_number(data.get("max_workers", default_max_workers()), "max_workers", int))
    )
    job_timeout = float(_as_number(data.get("job_timeout", 1800.0), "job_timeout", float))
    if job_timeout <= 0:
        raise ValueError("job_timeout must be a positive number of seconds")

    settings = Settings(
        project_root=project_root,
        template_roots=_normalise_sequence(data.get("template_roots")),
        tectonica_path=Path(data.get("tectonica_path", APP_ROOT)),
        trusted_folder_script=Path(data.get("trusted_folder_script", "trustedFolderCheck.scr")),
        autocad_executable=Path(data["autocad_executable"]) if data.get("autocad_executable") else None,
        autocad_candidates=tuple(Path(path) for path in data.get("autocad_candidates", DEFAULT_AUTOCAD_CANDIDATES)),
        verbose=_as_bool(data.get("verbose", verbose), "verbose"),
        log_dir=Path(data.get("log_dir", "logs")),
        log_encoding=str(data.get("log_encoding", "utf-8")),
        log_level=_validated_log_level(data.get("log_level", "DEBUG")),
        xref_xplode_toggle=_as_bool(data.get("xref_xplode_toggle", True), "xref_xplode_toggle"),
        fix_spot_elevations=_as_bool(data.get("fix_spot_elevations", False), "fix_spot_elevations"),
        max_workers=max_workers,
        job_timeout=job_timeout,
        script_encoding=str(data.get("script_encoding", "cp1251")),
        continue_on_error=_as_bool(data.get("continue_on_error", False), "continue_on_error"),
        check_updates=_as_bool(data.get("check_updates", True), "check_updates"),
    )

    if verbose:
        settings.verbose = True

    return settings


__all__ = [
    "Settings",
    "default_max_workers",
    "load_settings",
    "DEFAULT_AUTOCAD_CANDIDATES",
    "APP_ROOT",
    "GITHUB_REPO",
]
