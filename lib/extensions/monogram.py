# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Monogram Tool: Lettering specialised for three-letter monograms."""

import sys

import inkex
import wx

from ..gui.lettering import MonogramPanel
from ..gui.simulator import SplitSimulatorWindow
from ..i18n import _
from ..svg import get_correction_transform
from ..svg.tags import INKSCAPE_LABEL
from ..utils.svg_data import get_pagecolor
from .lettering import Lettering


class Monogram(Lettering):
    """Open the lettering simulator with monogram slot controls.

    Reuses Lettering's group selection / creation and the existing font renderer.
    Center-glyph enlargement is a settings stub until a later Release 2 slice.
    """

    def create_group(self):
        group = inkex.Group(attrib={
            INKSCAPE_LABEL: _("Ink/Stitch Monogram"),
            "transform": get_correction_transform(self.get_current_layer(), child=True)
        })
        self.get_current_layer().append(group)
        return group

    def effect(self):
        metadata = self.get_inkstitch_metadata()
        background_color = get_pagecolor(self.svg.namedview)
        app = wx.App()
        frame = SplitSimulatorWindow(
            title=_("Ink/Stitch Monogram"),
            panel_class=MonogramPanel,
            group=self.get_or_create_group(),
            on_cancel=self.cancel,
            metadata=metadata,
            background_color=background_color,
            target_duration=1
        )

        frame.Show()
        app.MainLoop()

        if self.cancelled:
            # Prevent superclass SVG output after a cancelled DOM edit.
            sys.exit(0)
