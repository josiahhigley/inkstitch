# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Prepare for PE900: write PES + editable SVG with a sew-order receipt."""

from __future__ import annotations

from pathlib import Path

from inkex import Boolean, errormsg
from lxml import etree

from ..hoop import find_hoop, hoop_warning
from ..i18n import _
from ..output import write_embroidery_file
from ..stitch_plan import stitch_groups_to_stitch_plan
from ..threads import ThreadCatalog
from .base import InkstitchExtension


class PreparePe900(InkstitchExtension):
    """
    One-click export for a Brother PE900 workflow.

    Writes:
      - <name>.pes  — machine file
      - <name>.inkstitch.svg — editable working copy of this document

    Also runs hoop / colour-change pre-flight and prints a receipt.
    """

    def __init__(self, *args, **kwargs):
        InkstitchExtension.__init__(self, *args, **kwargs)
        self.arg_parser.add_argument("--notebook")
        self.arg_parser.add_argument("-d", "--output-dir", type=str, default="", dest="output_dir")
        self.arg_parser.add_argument("-n", "--base-name", type=str, default="", dest="base_name")
        self.arg_parser.add_argument("-f", "--also-dst", type=Boolean, default=False, dest="also_dst")

    def effect(self) -> None:
        if not self.get_elements():
            return

        output_dir = self.options.output_dir.strip()
        if not output_dir:
            errormsg(_("Choose an output folder for the PES and editable SVG files."))
            return
        out = Path(output_dir)
        if not out.is_dir():
            errormsg(_("Output folder does not exist: {path}").format(path=output_dir))
            return

        base = self.options.base_name.strip() or self._default_base_name()
        base = Path(base).stem

        metadata = self.get_inkstitch_metadata()
        stitch_groups = self.elements_to_stitch_groups(self.elements)
        stitch_plan = stitch_groups_to_stitch_plan(
            stitch_groups,
            collapse_len=metadata['collapse_len_mm'],
            min_stitch_len=metadata['min_stitch_len_mm'],
        )
        ThreadCatalog().match_and_apply_palette(stitch_plan, metadata['thread-palette'])

        settings = {}
        rotate = metadata.get('rotate_on_export')
        if rotate not in (None, '', '0', 0, False):
            # preferences store left/right/none style values; pass through when numeric
            try:
                settings['rotate'] = float(rotate)
            except (TypeError, ValueError):
                pass

        pes_path = out / f"{base}.pes"
        svg_path = out / f"{base}.inkstitch.svg"
        written = []

        write_embroidery_file(str(pes_path), stitch_plan, self.svg, settings)
        written.append(str(pes_path))

        if self.options.also_dst:
            dst_path = out / f"{base}.dst"
            write_embroidery_file(str(dst_path), stitch_plan, self.svg, settings)
            written.append(str(dst_path))

        self._write_working_svg(svg_path)
        written.append(str(svg_path))

        errormsg(self._receipt(stitch_plan, written, settings.get('rotate', 0)))

    def _default_base_name(self) -> str:
        name = getattr(self.svg, "name", None) or "design"
        return Path(str(name)).stem or "design"

    def _write_working_svg(self, path: Path) -> None:
        # Preserve the live document so next week's monogram edit does not start from a flat PES.
        tree = self.document
        path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="utf-8", pretty_print=True))

    def _receipt(self, stitch_plan, written, rotation) -> str:
        color_blocks = list(stitch_plan)
        thread_changes = max(len(color_blocks) - 1, 0)
        stitches = sum(len(block) for block in color_blocks)
        lines = [
            _("Prepare for PE900 — ready"),
            "",
            _("Stitches: {count}").format(count=stitches),
            _("Colour stops: {count}").format(count=len(color_blocks)),
            _("Thread changes: {count}").format(count=thread_changes),
        ]

        hoop = find_hoop(self.svg)
        if hoop:
            lines.append(_("Hoop: {hoop}").format(hoop=hoop.description))
        warning = hoop_warning(self.svg, stitch_plan, rotation)
        if warning:
            lines.append("")
            lines.append(_("Hoop check:"))
            lines.append(warning)
        elif hoop:
            lines.append(_("Hoop check: design fits."))

        lines.append("")
        lines.append(_("Files written:"))
        for file_path in written:
            lines.append(f"  • {file_path}")
        lines.append("")
        lines.append(_("Tip: keep the .inkstitch.svg — that is your editable working file. "
                       "Send only the .pes to the PE900."))
        return "\n".join(lines)
