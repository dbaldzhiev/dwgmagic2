"""Script generation utilities backed by injected Jinja environment."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from jinja2 import Environment, TemplateNotFound

from dwgmagic.classify import classify_dwg_files
from dwgmagic.core.context import ProjectContext
from dwgmagic.errors import ScriptGenerationError


def execution_scripts_dir(project_root: Path) -> Path:
    """Local directory the .scr files are actually run from.

    AutoCAD refuses to load a script file from a network location — the job
    reports ``File load canceled`` and exits 0 having done nothing. The
    drawings themselves are fine on a share; only the script must be local.
    Verified: a script in an *untrusted* local temp dir runs, the same script
    on a UNC path does not.

    Scripts are always staged locally, not just for remote projects, so mapped
    network drives (which resolve to UNC too) take the same path.
    """

    digest = hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / "dwgmagic2" / f"{project_root.name}_{digest}"


@dataclass(slots=True)
class ScriptGenerator:
    environment: Environment

    def generate_all(self, context: ProjectContext, logger) -> Dict[str, Path]:
        project_root = context.project_root
        scripts_dir = project_root / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        classified = classify_dwg_files(context.get("dwg_files", []))
        view_files = classified.views
        sheet_files = classified.sheets
        sheet_views_lookup = classified.sheet_views_lookup
        structured_sheets = classified.structured_sheets

        context.set("structured_sheets", structured_sheets)
        context.set("sheet_views_lookup", sheet_views_lookup)

        artifacts: Dict[str, Path] = {}
        artifacts["project_script"] = self._render(
            "templates/project_script_template.tmpl",
            scripts_dir / "DWGMAGIC.scr",
            context,
            logger,
            sheetNamesList=sheet_files,
            sheets=structured_sheets,
        )
        artifacts["merge_script"] = self._render(
            "templates/mmm_script_template.tmpl",
            scripts_dir / "MMM.scr",
            context,
            logger,
            sheets=structured_sheets,
        )
        artifacts["merge_bat"] = self._render(
            "templates/manual_merge_bat_template.tmpl",
            project_root / "MANUALMERGE.bat",
            context,
            logger,
            acc=self._autocad_path(context),
        )

        for view in view_files:
            name = Path(view).stem
            artifacts[f"view:{name}"] = self._render(
                "templates/view_script_template.tmpl",
                scripts_dir / f"{name.upper()}.scr",
                context,
                logger,
                viewName=name,
            )

        for sheet in sheet_files:
            name = Path(sheet).stem
            views_on_sheet = sheet_views_lookup.get(name, [])
            artifacts[f"sheet:{name}"] = self._render(
                "templates/sheet_script_template.tmpl",
                scripts_dir / f"{name.upper()}_SHEET.scr",
                context,
                logger,
                sheetName=name,
                viewsOnSheet=views_on_sheet,
            )

        return artifacts

    @staticmethod
    def _stage_locally(destination: Path, context: ProjectContext) -> None:
        """Mirror a generated .scr into the local execution directory.

        The copy in the project stays for inspection and for MANUALMERGE.bat;
        the local copy is what accoreconsole is pointed at.
        """

        if destination.suffix.lower() != ".scr":
            return
        staged = execution_scripts_dir(context.project_root) / destination.name
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, staged)
        except OSError as exc:  # pragma: no cover - disk/AV specific
            raise ScriptGenerationError(
                f"Could not stage {destination.name} for execution: {exc}",
                hint=(
                    "AutoCAD cannot run scripts from a network location, so they "
                    "are copied to the local temp folder first."
                ),
            ) from exc

    @staticmethod
    def _autocad_path(context: ProjectContext) -> str:
        """Best-effort accoreconsole path for the manual merge batch file."""

        from dwgmagic.integrations.autocad import discover_autocad

        try:
            return str(
                discover_autocad(
                    context.settings.autocad_executable,
                    context.settings.autocad_candidates,
                )
            )
        except Exception:
            # Fall back to relying on PATH so the generated bat stays usable.
            return "accoreconsole.exe"

    def _render(self, template_name: str, destination: Path, context: ProjectContext, logger, **kwargs) -> Path:
        try:
            template = self.environment.get_template(template_name)
        except TemplateNotFound:
            try:
                template = self.environment.get_template(Path(template_name).name)
            except TemplateNotFound as exc:
                raise ScriptGenerationError(
                    f"Template {template_name} not found in any search path",
                    hint="Check --template-root / template_roots configuration.",
                ) from exc
        rendered = template.render(
            tectonica_path=context.settings.tectonica_path.as_posix(),
            project_name=context.project_root.name,
            # Scripts address their outputs absolutely. Windows cannot give a
            # process a UNC working directory, so a relative path in a script
            # silently resolves outside a project that lives on a network share.
            project_path=str(context.project_root),
            xrefXplodeToggle=context.settings.xref_xplode_toggle,
            **kwargs,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoding = context.settings.script_encoding
        try:
            destination.write_text(rendered, encoding=encoding)
            self._stage_locally(destination, context)
        except UnicodeEncodeError as exc:
            raise ScriptGenerationError(
                f"Cannot write {destination.name}: content is not representable "
                f"in the configured script encoding {encoding!r} ({exc})",
                hint=(
                    "Rename the project/DWG files to characters supported by the "
                    "encoding, or set script_encoding in the configuration."
                ),
            ) from exc
        logger.info("Generated %s", destination)
        return destination


__all__ = ["ScriptGenerator"]
