# DWGMAGIC

DWGMAGIC automates the end-to-end workflow for converting batches of Revit-exported DWG files into a single deliverable package.
The pipeline (trusted-folder validation → preprocessing → script generation → AutoCAD console execution) can be driven from a full-featured GUI (the default) or a headless CLI.

## Key Features
- **Pipeline orchestration** — discrete stages with shared context, per-stage timing, and structured results.
- **Resilient AutoCAD execution** — bounded parallelism, per-job timeouts, live console output streaming, cancellation, and failure detection that catches jobs which "succeed" with a zero exit code but fail in their output.
- **Output validation** — sheet batches must actually produce their `*_xrefed.dwg` files before the merge is attempted; the final deliverables (`<project>_MXR.dwg`, `<project>_MM.dwg`) are verified and reported with sizes.
- **Safety guardrails** — preprocessing refuses to touch folders that don't look like DWGMAGIC projects, and the GUI asks for confirmation before the first run in a folder.
- **Run tracking** — every run writes a chronological `logs/run.log`, per-job console dumps under `logs/jobs/`, and a machine-readable manifest `logs/run_<timestamp>.json`.
- **Auto-update** — the GUI checks GitHub releases on startup and can replace itself in place.

## Installation

Download **`dwgmagic2-setup-vX.Y.Z.exe`** from the [latest release](https://github.com/dbaldzhiev/dwgmagic2/releases) and run it. That's the whole procedure.

The installer is fully self-contained: Python and every dependency are compiled into the bundle, and `tectonica.dll` ships inside it. **Nothing is downloaded during installation and no system Python is required** — the machine does not need Python installed at all.

It installs to `%LOCALAPPDATA%\dwgmagic2` (per-user, so no administrator prompt) and sets up:
- a Start Menu and optional desktop shortcut,
- **Run with DWGMAGIC** on the right-click menu of folders and folder backgrounds,
- a normal entry in **Settings → Apps → Installed apps** for uninstalling.

> **SmartScreen warning:** the installer is not code-signed yet, so Windows shows
> *"Windows protected your PC"* on first run. Choose **More info → Run anyway**.

### Two executables
| Executable | Console? | Use |
| --- | --- | --- |
| `dwgmagic2w.exe` | no | The GUI. What the shortcut and context menu launch. |
| `dwgmagic2.exe` | yes | `--cli` runs and debugging — errors print to the terminal. |

### If the GUI does not appear
A startup failure writes a full traceback to `%LOCALAPPDATA%\dwgmagic2\logs\crash.log` and shows a message box naming the error. If you hit one, that file is what to attach to a bug report. Running `dwgmagic2.exe` from a terminal shows the same failure inline.

## Updating

- **From the GUI** — when a newer release exists, an **Update to vX.Y.Z** button appears in the sidebar. It downloads the release bundle, swaps it over the installation, and relaunches. Your `logs/` folder is preserved, and there is no dependency installation step to fail.
- Set `DWGMAGIC_CHECK_UPDATES=0` (or pass `--no-update-check`) to disable the startup check.

## Usage

### GUI (default)
```
dwgmagic2w.exe [project_directory]
```
- Open a project via the button, the recent-projects list, or by dropping a folder onto the window.
- Preflight checks for AutoCAD, the `tectonica.dll` plugin, and the trusted-path configuration (with a one-click fix).
- Stage table with per-stage timing; task tree with per-job status, exit codes, durations, and **live AutoCAD console output**.
- **Cancel Run** stops scheduling new jobs and kills running AutoCAD consoles.
- Light/Dark/System appearance; window size, appearance, and recent projects persist between sessions.

`--autorun` starts the pipeline automatically once the project loads (used by the context-menu integration).

### CLI (headless)
```
dwgmagic2.exe <project_directory> --cli [--verbose] [--config path/to/settings.toml]
```
Exit code is `0` on success, `1` on failure, `130` when cancelled with Ctrl+C — suitable for scripting.

Common flags (both front-ends):
- `--template-root PATH` — additional template search roots (repeatable).
- `--autocad-path PATH` — explicit path to `accoreconsole.exe`.
- `--verbose` — stream detailed logs to the terminal.
- `--version` — print the application version.

## Configuration
Runtime settings are defined by the [`Settings` dataclass](dwgmagic/settings.py). Precedence: CLI flags > config file (`--config`, TOML/YAML) > environment variables > defaults.

| Setting | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `autocad_executable` | `DWGMAGIC_AUTOCAD_PATH` | auto-discovered | Explicit `accoreconsole.exe` path. Discovery checks the registry, then `C:\Program Files\Autodesk\AutoCAD 2017–2026`. |
| `tectonica_path` | `DWGMAGIC_TECTONICA_PATH` | the app folder | Where `tectonica.dll` is NETLOADed from (relocatable). |
| `max_workers` | `DWGMAGIC_MAX_WORKERS` | CPU count | Simultaneous AutoCAD console processes. |
| `job_timeout` | `DWGMAGIC_JOB_TIMEOUT` | `1800` | Seconds before a hung job is killed. |
| `continue_on_error` | `DWGMAGIC_CONTINUE_ON_ERROR` | `false` | Keep going when individual jobs fail. |
| `xref_xplode_toggle` | `DWGMAGIC_XREF_EXPLODE` | `true` | Use the tecbxt bind/explode path. |
| `template_roots` | `DWGMAGIC_TEMPLATE_ROOT` | bundled | Extra template search roots. |
| `log_dir` / `log_level` / `log_encoding` | `DWGMAGIC_LOG_DIR` / `_LOG_LEVEL` / `_LOG_ENCODING` | `logs` / `DEBUG` / `utf-8` | Logging behaviour. |
| `script_encoding` | — | `cp1251` | Encoding of generated `.scr` files. |
| `check_updates` | `DWGMAGIC_CHECK_UPDATES` | `true` | GitHub release check on GUI startup. |

## Development

```bash
git clone https://github.com/dbaldzhiev/dwgmagic2.git
cd dwgmagic2
python -m pip install -r requirements.txt
python main.py                    # opens the GUI
python -m pytest
```

### Building the bundle locally
```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv-build\Scripts\python.exe -m PyInstaller dwgmagic2.spec --noconfirm --clean
```
Output lands in `dist\dwgmagic2\`. Note that a bundle built this way has no `tectonica.dll` and no `updater.ps1` — `scripts\release.ps1` is what places those next to the executables.

### Building tectonica.dll
The AutoCAD plugin lives in [`tectonica/`](tectonica/) and targets AutoCAD 2025's managed API (`net8.0-windows`). Generated `.scr` scripts `NETLOAD` it from the application folder.

```powershell
.\scripts\build_tectonica.ps1
```

Building requires:
- The ObjectARX 2025 managed reference assemblies (`AcCoreMgd.dll`, `AcDbMgd.dll`, `AcMgd.dll`, `AcDx.dll`) in `C:\Autodesk\ObjectARX2025\inc\` — the path referenced by `tectonica/tectonica/tectonica.csproj`.
- MSBuild (Visual Studio or Build Tools) and the .NET 8 SDK.

The ObjectARX SDK is proprietary and cannot be installed on a GitHub-hosted runner, which is why releases are cut locally rather than by CI.

### Cutting a release
1. Bump `__version__` in [`dwgmagic/__init__.py`](dwgmagic/__init__.py).
2. Commit, then run:

```powershell
.\scripts\release.ps1
```

This runs the tests, builds `tectonica.dll`, builds the PyInstaller bundle, places the DLL and updater beside the executables, compiles the Inno Setup installer, zips the bundle, tags the commit, and publishes a GitHub release with both assets:

| Asset | Purpose |
| --- | --- |
| `dwgmagic2-setup-vX.Y.Z.exe` | What end users download. |
| `dwgmagic2-vX.Y.Z-win64.zip` | What the in-app updater downloads and swaps in. |

Use `-NoPublish` to build the assets without tagging or publishing, and `-SkipTectonica` to reuse an already-built DLL.

## Project Structure
- `dwgmagic/core/` — pipeline primitives and stage implementations.
- `dwgmagic/integrations/` — AutoCAD runner/coordinator (subprocess management, discovery).
- `dwgmagic/gui/` — CustomTkinter application and persisted UI state.
- `dwgmagic/ui/` — shared progress listeners for CLI and GUI.
- `dwgmagic/templates/` — packaged Jinja templates for AutoCAD script generation.
- `dwgmagic/classify.py` — the single source of truth for the sheet/view file-naming convention.
- `dwgmagic/crashlog.py` — startup crash reporting for the windowed build.
- `dwgmagic/update.py` — GitHub release checks and updater launch.
- `dwgmagic2.spec` — PyInstaller build definition (both executables).
- `installer/dwgmagic2.iss` — Inno Setup installer definition.
- `scripts/` — release tooling and the updater that runs outside the app.
- `tectonica/` — C# source of the AutoCAD plugin.

## Troubleshooting a failed run
Inside the project folder:
- `logs/run.log` — chronological log of the whole run, all components.
- `logs/jobs/<script>.out.txt` — raw AutoCAD console output per job.
- `logs/run_<timestamp>.json` — the run manifest: settings snapshot, stage timings, per-job exit codes/durations/failure reasons, deliverable status.

For failures that happen *before* a project is opened, see `%LOCALAPPDATA%\dwgmagic2\logs\crash.log`.

## Testing & CI
GitHub Actions runs the test suite and a full PyInstaller build on every push/PR ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — the build job exists so a broken bundle is caught before release time. Releases themselves are published from a developer machine (see [Cutting a release](#cutting-a-release)).

## Contributing & Support
Issues and pull requests are welcome. When reporting bugs, include:
- The command you ran (CLI arguments or GUI launch instructions).
- The run manifest and relevant snippets from `logs/run.log`, or `crash.log` for startup failures.
- AutoCAD job output from `logs/jobs/`, if applicable.
