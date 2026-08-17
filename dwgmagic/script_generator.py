"""Script generation utilities backed by injected Jinja environment."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from jinja2 import Environment, TemplateNotFound

from dwgmagic.classify import classify_dwg_files
from dwgmagic.core.context import ProjectContext
from dwgmagic.errors import ScriptGenerationError


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
            xrefXplodeToggle=context.settings.xref_xplode_toggle,
            **kwargs,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoding = context.settings.script_encoding
        try:
            destination.write_text(rendered, encoding=encoding)
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
