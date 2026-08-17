# dwgmagic2 — Packaging Rewrite Plan

Status: **planning only** — nothing in this repo has been created yet. This doc is the
handoff to the DEV session. Scope is explicitly **packaging/installer/updater**, not app
logic. `dwgmagic/` core code is carried over close to as-is.

---

## 1. What we're replacing, and why it's broken

Source of truth analyzed: `C:\dwgmagic` (= github.com/dbaldzhiev/dwgmagic, branch `main`,
currently `v1.1.1`).

**Stack:** Python 3.10+ desktop app. `customtkinter` GUI (default) + headless CLI, same
entrypoint (`main.py` → `dwgmagic.cli`). Core deps are light: `Jinja2`, `rich`, `packaging`,
`customtkinter`, `tkinterdnd2`. No heavy/compiled deps (no numpy/torch/etc.) — this matters,
it means a compiled bundle will be small and PyInstaller-friendly.

**Companion artifact:** `tectonica.dll` — a .NET 8 AutoCAD plugin, source lives in a separate
git submodule (`vendor/tectonica`), built with MSBuild against the **proprietary** Autodesk
ObjectARX SDK. Can't be built in CI. Today it's built locally (`build_tectonica.ps1`) and
must be manually present. Release zips from CI **do not** include it.

### Root cause of "installed, app doesn't run, no errors"

This traces to a stack of fragility, not one bug:

1. **`install.bat` depends on a real Python 3.10+ already on the user's PATH.** It probes
   `py -3` then `python`. On a machine without a real Python, this can silently resolve to
   the Windows Store `python.exe` alias stub, or pick up a wrong/incompatible interpreter.
   Batch windows opened by double-click don't reliably `pause` on every failure path, so an
   install error can flash and vanish before it's read.
2. **First run does a live `pip install` of 5 packages into a freshly created venv** under
   `%LOCALAPPDATA%\dwgmagic\venv`. Any network/proxy/AV interference breaks this with a
   terse message the user may never see (see #1).
3. **`install.bat` hard-requires `tectonica.dll` to already be sitting next to it** — but a
   plain "download release zip → run install.bat" flow never has that file, since CI can't
   build it. Anyone following the documented "end user" path hits an install-time error
   unless they've separately built the DLL.
4. **The GUI launches via `pythonw.exe`** (`run_gui.bat`) — a windowed process with **no
   console, stdout/stderr silently discarded**. Critically, `dwgmagic/logger.py`'s
   `LoggerFactory` is scoped **per project** (writes to `<project_root>/logs/run.log`) — so
   **nothing is logged anywhere until a project folder is opened**. There's no top-level
   `sys.excepthook` and no startup crash log anywhere in `dwgmagic/gui/app.py`. If steps 1–3
   left a broken venv/interpreter/Tk install, the very first import raises, `pythonw.exe`
   eats it, and the user sees **literally nothing** — window never appears, no error, no
   log file. This is exactly the symptom reported.
5. Auto-update (`dwgmagic/update.py` + `update.bat`/`update.ps1`) is a reasonable design —
   dependency-free GitHub Releases polling, version compare via `packaging.version` — but it
   updates the same fragile venv/pip layer, and has the same silent-failure characteristics
   if something goes wrong.
6. **Repo hygiene:** `__pycache__/`, `.pytest_cache/` committed at repo root; a stray
   `tectonica.pdb` (debug symbols) committed at repo root; `vendor/tectonica` submodule
   couples an unrelated proprietary-SDK C# project into this repo's clone graph;
   `sample_data/`, `.conda/`, `logs/` present in the working tree.

**Conclusion:** the app logic is fine (consistent with "works fine from source on my
machine" — that's a real terminal-attached interpreter, not the frozen install path). The
install/launch pipeline is what needs to change.

---

## 2. Goals (confirmed with user)

- New repo `dwgmagic2`, clean history, only what's needed — not a fork/clone of `dwgmagic`.
- **No dependency on the end user's system Python.** Ship one compiled artifact with all
  Python + dependencies embedded.
- **Single file to install.** Download one `.exe`, run it, get a deterministic clean
  install every time — no network installs at install time.
- **Auto-update via GitHub Releases**, replacing the pip/venv-based updater with an update
  path appropriate to a compiled bundle.
- **No app-logic refactor.** This is a packaging project. `dwgmagic/` core, GUI, CLI carry
  over essentially unchanged.

---

## 3. Proposed architecture

### 3.1 Build: PyInstaller, onedir

Recommend **PyInstaller** over Nuitka: most battle-tested path for `tkinter`/`customtkinter`
(well-documented `--add-data` patterns for customtkinter's theme JSON, no C-compile step
per build), and this app has no perf-critical hot path that would justify Nuitka's extra
friction.

Recommend **onedir**, not onefile, for the installed application: onefile self-extracts to a
temp dir on every launch — slower, and if that extraction is interfered with (AV, disk
permissions), it fails in exactly the same "windowed app, no console, dies silently" way
we're trying to eliminate. Onedir starts directly, no extraction step. "Single file to
install" refers to the **installer**, not the installed layout — satisfied by the installer
itself, not by onefile.

**Two executables from the same codebase** (mirrors today's `python.exe` vs `pythonw.exe`
split, but done properly):
- `dwgmagic2.exe` — `console=True`. Used for `--cli` and any terminal-launched debugging.
  Uncaught exceptions print to the attached console for free.
- `dwgmagic2w.exe` — `console=False`, windowed. What the shortcut / context-menu entry
  launches for the GUI.

### 3.2 Startup diagnosability (small, targeted addition — not a refactor)

Add a top-level `sys.excepthook` (and a `try/except` around GUI construction, before
`mainloop()`) in the entrypoint that, on any startup failure:
- writes the traceback to `%LOCALAPPDATA%\dwgmagic2\logs\crash.log`, and
- shows a native message box with a short error + "see crash.log" pointer, even in the
  windowed (`console=False`) build.

This directly targets the reported symptom. Cheap insurance: if a future build/DLL/dependency
problem ever recurs, the user gets a visible, actionable message instead of silence — today
that's structurally impossible before a project is opened.

### 3.3 Installer: Inno Setup → single `dwgmagic2-setup-vX.Y.Z.exe`

- Installs to `%LOCALAPPDATA%\dwgmagic2` (no admin/UAC needed — matches current model).
- Registers the folder + folder-background context-menu entries ("Run with DWGMAGIC").
- Creates Start Menu / Desktop shortcut pointing at `dwgmagic2w.exe`.
- Registers a real Windows "Apps & Features" uninstall entry — Inno Setup does this for
  free; today's custom `uninstall.bat` isn't discoverable through Windows' own UI.
- No network access during install — every dependency is already inside the onedir bundle.

### 3.4 Auto-update

Keep `dwgmagic/update.py`'s GitHub Releases polling logic close to verbatim — it's already
dependency-free (`urllib` + stdlib `json`) and network-tolerant. Change what happens once an
update is found:

- Download the release's **onedir bundle zip** (a separate, smaller release asset than the
  full installer — no installer UI to drive silently), extract to a temp dir, and swap it
  over the live install directory (same robocopy-style swap `update.ps1` already does),
  preserving `logs/` and the local `tectonica.dll`. No `pip install` step — the bundle is
  already fully self-contained, which removes the update's biggest current failure mode.
- Relaunch `dwgmagic2w.exe` when done.

This is a smaller change than switching to "silently re-run the installer," reuses proven
logic, and removes the pip/venv step that's the main updater risk today.

### 3.5 `tectonica.dll` — stays an external artifact, decoupled from this repo

Recommend **dropping the `vendor/tectonica` submodule from dwgmagic2 entirely.** The DLL
build still requires a proprietary SDK and a human with AutoCAD/ObjectARX installed — CI
can't touch it either way. Keep the `tectonica` source in its own separate repo, fully
unrelated to dwgmagic2's clone/build graph. dwgmagic2 only ever consumes the **compiled
`tectonica.dll`** as an opaque binary, dropped into the packaging folder by whoever cuts a
release, same as today's local workflow — just without dragging the C# source into this
repo. The app's existing preflight check (already checks for AutoCAD + the DLL) continues
to give a clear, visible warning if it's missing — this is a "flag clearly," not "silently
fail," case already, so no change needed there.

### 3.6 What moves over vs. gets left behind

**Bring over:** `dwgmagic/` package (source only), `main.py`, `tests/`, `requirements.txt` /
`pyproject.toml` (trimmed as needed for the build), `magic.ico`, README (rewritten for the
new install story), CI workflow (adapted).

**Leave behind:** `__pycache__/`, `.pytest_cache/`, `.conda/`, `logs/`, `sample_data/`
(unless tests need it — check first), `tectonica.pdb`, `vendor/tectonica` submodule,
`install.bat`/`uninstall.bat`/`update.bat`/`update.ps1`/`build_tectonica.ps1` (all superseded
by the Inno Setup installer + new updater — `build_tectonica.ps1`'s *logic* can live on in
the separate `tectonica` repo instead).

**New repo, fresh git history** (`git init`, not a clone) — deliberate, so none of the above
cruft or old commit baggage carries forward. Proper `.gitignore` from the start (Python +
`build/`, `dist/`, PyInstaller's `*.spec` build artifacts, Inno Setup `Output/`).

### 3.7 CI/CD

- **CI** (`ci.yml`): keep close to as-is — `pytest` + `ruff` on `windows-latest`, on
  push/PR.
- **Release** (`release.yml`), on tag `vX.Y.Z`:
  1. Verify tag matches `dwgmagic.__version__`.
  2. Run tests.
  3. `pyinstaller` build (onedir, both exes).
  4. Compile the Inno Setup script (`iscc`) → `dwgmagic2-setup-vX.Y.Z.exe`.
  5. Zip the onedir bundle as the update-package asset (`dwgmagic2-vX.Y.Z-win64.zip`).
  6. Upload both as release assets (`softprops/action-gh-release`, as today).
  - **Caveat, needs a decision:** CI-built releases won't have `tectonica.dll` (same
    constraint as today). Either (a) CI-built installers ship without it and rely on the
    existing preflight warning, with DLL-bearing releases being a separate manual local
    build+upload step when `tectonica` changes, or (b) every release is cut manually/locally
    so the DLL is always bundled. Flagging this for the DEV session rather than deciding it
    here.

---

## 4. Migration steps (for DEV session)

1. Create the new repo (`dbaldzhiev/dwgmagic2`), `git init` fresh — no history carried over.
2. Copy over the "bring over" list from §3.6, skipping the "leave behind" list.
3. Write PyInstaller `.spec` covering both entrypoints, `customtkinter`/`tkinterdnd2` data
   files, icon.
4. Add the `sys.excepthook` + crash-log safety net (§3.2) to the entrypoint.
5. Write the Inno Setup script (§3.3).
6. Rewrite `update.py`'s apply-side to swap in the onedir bundle zip instead of git
   pull/pip install (§3.4); keep the check/poll side.
7. Adapt CI/release workflows (§3.7).
8. Rewrite README for the new one-file install story.
9. Tag a release, let CI build it, then **validate on a clean machine/VM with no Python
   installed at all** — this is the real regression test for the bug that started this.

---

## 5. Open decisions — for the DEV session / user to make, not decided here

- **Installer engine:** Inno Setup (recommended — free, scriptable, silent-mode friendly)
  vs. WiX/MSIX (more "modern Windows," heavier setup, stricter signing/identity
  requirements).
- **Two-exe split (console + windowed)** — recommended for CLI robustness — vs. a single
  windowed exe relying only on the crash-log safety net.
- **Versioning:** continue from `1.1.x`, or restart at `1.0.0` for the new packaging line?
- **Repo visibility:** public (current `dwgmagic` is public) or private?
- **CI-built vs. manual releases**, re: `tectonica.dll` bundling (§3.7 caveat).
- **Code signing:** an unsigned installer/exe will trigger SmartScreen warnings on a truly
  clean machine, which cuts against "clean install clean run." Worth deciding whether to get
  a code-signing cert, or accept and document the warning.
