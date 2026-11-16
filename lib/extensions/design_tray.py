# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Design Tray: merge purchased embroidery files into the current document."""

from inkex import Boolean, errormsg

from ..design_import import (filetype_filter, import_designs,
                             split_inkscape_paths)
from ..i18n import _
from .base import InkstitchExtension


class DesignTray(InkstitchExtension):
    """
    Import one or more embroidery designs onto the current canvas.

    Supports every stitch format pystitch can read (PES, DST, JEF, VP3, …) plus SVG.
    Each file becomes a named group so Color Order and Prepare for PE900 can treat it
    as one purchased design.
    """

    FILETYPES = filetype_filter()

    def __init__(self, *args, **kwargs):
        InkstitchExtension.__init__(self, *args, **kwargs)
        self.arg_parser.add_argument("--notebook")
        self.arg_parser.add_argument("-f", "--design-files", type=str, default="", dest="design_files")
        self.arg_parser.add_argument("-c", "--import-commands", type=str, default="symbols", dest="import_commands")
        self.arg_parser.add_argument("-n", "--center-in-hoop", type=Boolean, default=True, dest="center_in_hoop")

    def effect(self) -> None:
        paths = split_inkscape_paths(self.options.design_files)
        if not paths:
            errormsg(_("Select one or more embroidery files to import.\n\n"
                       "Supported formats: {formats}").format(formats=self.FILETYPES.replace(",", ", ")))
            return

        placed, skipped = import_designs(
            self.svg,
            paths,
            import_commands=self.options.import_commands,
            center=bool(self.options.center_in_hoop),
        )

        lines = []
        if placed:
            lines.append(_("Imported {count} design(s) into the document:").format(count=len(placed)))
            for group in placed:
                lines.append(f"  • {group.label}")
            lines.append("")
            lines.append(_("Next: arrange them in the hoop, then Extensions → Ink/Stitch → Edit → Color Order."))
        if skipped:
            lines.append("")
            lines.append(_("Skipped:"))
            lines.extend(f"  • {message}" for message in skipped)
        if not placed and not skipped:
            errormsg(_("No designs were imported."))
            return
        errormsg("\n".join(lines))
