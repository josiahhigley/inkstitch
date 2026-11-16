# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Objects pane: find, select, and delete imported designs in a merged document."""

from __future__ import annotations

import sys

import wx

from ..gui.abort_message import AbortMessageApp
from ..gui.objects_pane import ObjectsPanePanel
from ..gui.simulator import SplitSimulatorWindow
from ..i18n import _
from ..utils.svg_data import get_pagecolor
from .base import InkstitchExtension


class ObjectsPane(InkstitchExtension):
    """
    Photoshop/Embrilliance-style object list for merged designs.

    Lists named imports (Design Tray) and other top-level embroidery groups so you
    can select the right slice without hunting overlapping stitches on the canvas.
    """

    def __init__(self, *args, **kwargs):
        self.cancelled = False
        InkstitchExtension.__init__(self, *args, **kwargs)

    def cancel(self):
        self.cancelled = True

    def effect(self) -> None:
        if not self.get_elements():
            app = AbortMessageApp(
                _("No embroidery objects found in this document."),
                None,
            )
            app.MainLoop()
            return

        metadata = self.get_inkstitch_metadata()
        background_color = get_pagecolor(self.svg.namedview)
        app = wx.App()
        frame = SplitSimulatorWindow(
            title=_("Objects"),
            panel_class=ObjectsPanePanel,
            extension=self,
            on_cancel=self.cancel,
            metadata=metadata,
            background_color=background_color,
            target_duration=1,
        )
        frame.Show()
        app.MainLoop()

        if self.cancelled:
            sys.exit(0)
