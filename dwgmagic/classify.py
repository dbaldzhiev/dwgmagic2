"""Classification of project DWG files into views, sheets, and unmatched files.

The naming convention (Revit DWG export) is:
  - ``<Sheet>-View-<n>.dwg`` — a model-space view placed on a sheet.
  - ``<Sheet>.dwg``          — a sheet drawing.
  - files containing ``-rvt-`` are Revit link exports and are ignored.

This module is the single source of truth for that convention; the stages and
the script generator both consume it so the rules cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

VIEW_MARKER = "-View-"
RVT_MARKER = "-rvt-"


@dataclass(slots=True)
class ProjectFiles:
    """Result of classifying a project's DWG file names."""

    views: List[str] = field(default_factory=list)
    sheets: List[str] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)
    #: Views whose name does not begin with any known sheet name.
    orphan_views: List[str] = field(default_factory=list)

    @property
    def sheet_views_lookup(self) -> Dict[str, List[str]]:
        lookup: Dict[str, List[str]] = {}
        for sheet in self.sheets:
            stem = Path(sheet).stem
            lookup[stem] = [
                view for view in self.views if view.startswith(f"{stem}{VIEW_MARKER}")
            ]
        return lookup

    @property
    def structured_sheets(self) -> List[Dict[str, object]]:
        structured: List[Dict[str, object]] = []
        for sheet_stem, views in self.sheet_views_lookup.items():
            structured.append(
                {
                    "sheetName": sheet_stem,
                    "viewsOnSheet": [
                        {
                            "viewIndx": Path(view).stem.split(VIEW_MARKER)[-1],
                            "name": Path(view).stem,
                        }
                        for view in views
                    ],
                }
            )
        return structured

    def summary(self) -> str:
        parts = [f"{len(self.sheets)} sheet(s)", f"{len(self.views)} view(s)"]
        if self.ignored:
            parts.append(f"{len(self.ignored)} ignored")
        if self.orphan_views:
            parts.append(f"{len(self.orphan_views)} orphan view(s)")
        return ", ".join(parts)


def classify_dwg_files(dwg_files: Sequence[str]) -> ProjectFiles:
    """Split DWG file names into views, sheets, and ignored files."""

    result = ProjectFiles()
    for name in dwg_files:
        if not name.lower().endswith(".dwg"):
            result.ignored.append(name)
            continue
        if RVT_MARKER in name:
            result.ignored.append(name)
            continue
        if VIEW_MARKER in name:
            result.views.append(name)
        else:
            result.sheets.append(name)

    sheet_stems = [Path(sheet).stem for sheet in result.sheets]
    for view in result.views:
        if not any(view.startswith(f"{stem}{VIEW_MARKER}") for stem in sheet_stems):
            result.orphan_views.append(view)
    return result


__all__ = ["ProjectFiles", "classify_dwg_files", "VIEW_MARKER", "RVT_MARKER"]
