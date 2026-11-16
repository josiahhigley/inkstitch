# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Import purchased embroidery designs into the current document."""

from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import inkex
import pystitch

from .commands import ensure_symbol
from .i18n import _
from .stitch_plan import generate_stitch_plan
from .svg import get_correction_transform
from .svg.tags import INKSCAPE_GROUPMODE, INKSCAPE_LABEL, SVG_GROUP_TAG
from .update import mark_elements_as_current

# Color-only files cannot become a design on their own.
COLOR_ONLY_EXTENSIONS = {"edr", "col", "inf"}

# Formats we intentionally skip even if pystitch has a reader.
SKIP_EXTENSIONS = COLOR_ONLY_EXTENSIONS | {"csv", "json"}


def embroidery_reader_extensions() -> List[str]:
    """Extensions pystitch can read as stitch designs (for Design Tray filters)."""
    extensions = []
    for fmt in pystitch.supported_formats():
        if "reader" not in fmt:
            continue
        if fmt.get("category") not in ("embroidery", "stitch", "quilting"):
            continue
        ext = fmt["extension"].lower()
        if ext in SKIP_EXTENSIONS:
            continue
        extensions.append(ext)
    # SVG designs (already digitised in Inkscape / Ink/Stitch)
    if "svg" not in extensions:
        extensions.append("svg")
    return sorted(set(extensions))


def filetype_filter() -> str:
    """Comma-separated extensions for an Inkscape path param."""
    return ",".join(embroidery_reader_extensions())


def split_inkscape_paths(raw: str) -> List[str]:
    """Inkscape multi-file path params join paths with '|'."""
    if not raw:
        return []
    return [part.strip() for part in raw.split("|") if part.strip()]


def _load_svg_group(path: Path) -> inkex.Group:
    document = inkex.load_svg(str(path)).getroot()
    group = inkex.Group()
    group.label = path.name
    for child in list(document):
        tag = getattr(child, "tag", None)
        if not isinstance(tag, str):
            continue
        if tag == SVG_GROUP_TAG or tag.endswith(("}g", "}path", "}rect", "}circle", "}ellipse",
                                                   "}polyline", "}polygon", "}line", "}use")):
            group.append(child)
    return group


def import_design_file(path: str, import_commands: str = "symbols") -> Optional[inkex.Group]:
    """
    Load one embroidery or SVG file into a named group suitable for the current document.

    Returns None when the file cannot be imported as a design (e.g. colour-only).
    """
    file_path = Path(path)
    extension = file_path.suffix.lstrip(".").lower()
    if extension in COLOR_ONLY_EXTENSIONS:
        return None
    if not file_path.is_file():
        raise FileNotFoundError(path)

    if extension == "svg":
        design = _load_svg_group(file_path)
    else:
        svg = generate_stitch_plan(str(file_path), import_commands)
        label = escape(file_path.name)
        search = f'.//*[@inkscape:label="{label}"]'
        matches = svg.xpath(search, namespaces=inkex.NSS)
        if not matches:
            matches = svg.xpath(".//svg:g", namespaces=inkex.NSS)
        if not matches:
            raise ValueError(_("No stitches found in {path}").format(path=path))
        design = matches[0]
        design.attrib.pop(INKSCAPE_GROUPMODE, None)
        design.label = file_path.name

    design.set("inkstitch:design_import", file_path.name)
    mark_elements_as_current(design)
    return design


def place_design_on_document(
    svg: inkex.SvgDocumentElement,
    design: inkex.Group,
    *,
    center: bool = True,
    offset_index: int = 0,
    offset_mm: float = 5.0,
) -> inkex.Group:
    """
    Insert a design group into the current layer, centred on the page (with a small stagger).

    Keeps each import as a named group so Color Order / Objects can treat it as one purchased design.
    """
    from .svg import PIXELS_PER_MM

    design.label = design.label or _("Imported design")
    design.set("transform", get_correction_transform(svg.get_current_layer(), child=True))
    svg.get_current_layer().append(design)
    ensure_symbol(svg, "trim")
    ensure_symbol(svg, "stop")

    if center:
        page = svg.get_page_bbox()
        bbox = design.bounding_box()
        if page is not None and bbox is not None:
            stagger = offset_index * offset_mm * PIXELS_PER_MM
            dx = page.center.x - bbox.center.x + stagger
            dy = page.center.y - bbox.center.y + stagger
            design.transform = (
                inkex.Transform(f"translate({dx},{dy})") @ inkex.Transform(design.get("transform"))
            )

    return design


def import_designs(
    svg: inkex.SvgDocumentElement,
    paths: Sequence[str],
    *,
    import_commands: str = "symbols",
    center: bool = True,
) -> Tuple[List[inkex.Group], List[str]]:
    """
    Import many design files.

    Returns (placed groups, skipped path messages).
    """
    placed: List[inkex.Group] = []
    skipped: List[str] = []
    for index, path in enumerate(paths):
        extension = Path(path).suffix.lstrip(".").lower()
        if extension in COLOR_ONLY_EXTENSIONS:
            skipped.append(_("{path}: colour files cannot be imported as designs").format(path=path))
            continue
        try:
            design = import_design_file(path, import_commands=import_commands)
        except Exception as exc:  # noqa: BLE001 - surface per-file failures to the user
            skipped.append(f"{path}: {exc}")
            continue
        if design is None:
            skipped.append(_("{path}: not a stitch design").format(path=path))
            continue
        placed.append(place_design_on_document(svg, design, center=center, offset_index=index))
    return placed, skipped
