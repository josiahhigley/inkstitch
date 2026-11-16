# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Monogram panel: thin LetteringPanel wrapper with three initial slots."""

import inkex
import wx

from ...i18n import _
from ...lettering.monogram import (apply_monogram_to_settings,
                                   default_monogram_settings,
                                   parse_monogram_slots)
from ...svg.tags import INKSTITCH_LETTERING
from .main_panel import LetteringPanel

# How much larger the center initial is drawn when enlargement is on.
CENTER_ENLARGEMENT_FACTOR = 1.35


class MonogramPanel(LetteringPanel):
    """Lettering UI specialised for three-letter monograms.

    Reuses font selection, sizing, embroidery options, and render_text via
    LetteringPanel. Adds Left / Center / Right slots and a center-enlargement
    toggle stub; slot changes rewrite settings.text for the existing renderer.
    """

    def __init__(self, parent, simulator, group, metadata=None, background_color='white'):
        super().__init__(parent, simulator, group, metadata=metadata, background_color=background_color)
        self._install_monogram_controls()
        self._sync_slots_from_settings()

    def load_settings(self):
        super().load_settings()
        for key, value in default_monogram_settings().items():
            self.settings.setdefault(key, value)

        # New monogram groups default to centered alignment.
        if INKSTITCH_LETTERING not in self.group.attrib:
            self.settings.text_align = 1

        left = self.settings.get("monogram_left", "")
        center = self.settings.get("monogram_center", "")
        right = self.settings.get("monogram_right", "")
        if not (left or center or right) and self.settings.get("text"):
            left, center, right = parse_monogram_slots(self.settings.text)
        apply_monogram_to_settings(
            self.settings,
            left,
            center,
            right,
            center_enlargement=bool(self.settings.get("monogram_center_enlargement", False)),
        )

    def apply_settings(self):
        super().apply_settings()
        self._sync_slots_from_settings()

    def _install_monogram_controls(self):
        options = self.options_panel
        sizer = options.GetSizer()
        if sizer is None:
            return

        box = wx.StaticBox(options, wx.ID_ANY, label=_("Monogram"))
        monogram_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.monogram_left = self._make_slot(options, _("Left"))
        self.monogram_center = self._make_slot(options, _("Center"))
        self.monogram_right = self._make_slot(options, _("Right"))
        for ctrl in (self.monogram_left, self.monogram_center, self.monogram_right):
            row.Add(ctrl["sizer"], 1, wx.ALL, 5)

        self.center_enlargement = wx.CheckBox(options, label=_("Enlarge center initial"))
        self.center_enlargement.SetToolTip(_(
            "Draw the center initial larger than the side letters (classic monogram look)."
        ))
        self.center_enlargement.Bind(wx.EVT_CHECKBOX, self._on_monogram_changed)

        monogram_sizer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 5)
        monogram_sizer.Add(self.center_enlargement, 0, wx.ALL, 10)

        # Place monogram controls above the free-text editor.
        insert_at = max(0, sizer.GetItemCount() - 1)
        sizer.Insert(insert_at, monogram_sizer, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 10)

        # Keep the underlying lettering text field in sync but discourage free typing.
        options.text_editor.SetEditable(False)
        options.text_editor.SetToolTip(_("Filled from the Left / Center / Right initials above."))
        options.Layout()
        self.Layout()

    def _make_slot(self, parent, label):
        column = wx.BoxSizer(wx.VERTICAL)
        caption = wx.StaticText(parent, wx.ID_ANY, label)
        field = wx.TextCtrl(parent, wx.ID_ANY, size=(48, -1))
        field.SetMaxLength(1)
        field.Bind(wx.EVT_TEXT, self._on_monogram_changed)
        column.Add(caption, 0, wx.BOTTOM, 4)
        column.Add(field, 0, wx.EXPAND, 0)
        return {"sizer": column, "field": field}

    def _sync_slots_from_settings(self):
        if not hasattr(self, "monogram_left"):
            return
        left = self.settings.get("monogram_left", "")
        center = self.settings.get("monogram_center", "")
        right = self.settings.get("monogram_right", "")
        for field, value in (
            (self.monogram_left["field"], left),
            (self.monogram_center["field"], center),
            (self.monogram_right["field"], right),
        ):
            if field.GetValue() != value:
                field.ChangeValue(value)
        enlargement = bool(self.settings.get("monogram_center_enlargement", False))
        if self.center_enlargement.GetValue() != enlargement:
            self.center_enlargement.SetValue(enlargement)
        composed = self.settings.get("text", "")
        if self.options_panel.text_editor.GetValue() != composed:
            self.options_panel.text_editor.ChangeValue(composed)

    def _on_monogram_changed(self, event=None):
        apply_monogram_to_settings(
            self.settings,
            self.monogram_left["field"].GetValue(),
            self.monogram_center["field"].GetValue(),
            self.monogram_right["field"].GetValue(),
            center_enlargement=self.center_enlargement.GetValue(),
        )
        composed = self.settings.text
        if self.options_panel.text_editor.GetValue() != composed:
            self.options_panel.text_editor.ChangeValue(composed)
        if self.options_panel.font_glyph_filter.GetValue():
            self.on_filter_changed()
        self.update_preview()

    def update_lettering(self, raise_error=False):
        super().update_lettering(raise_error=raise_error)
        if self.settings.get("monogram_center_enlargement"):
            self._enlarge_center_glyph()

    def _enlarge_center_glyph(self) -> None:
        """Scale the middle glyph of a 3-letter monogram around its own center."""
        glyphs = [
            node for node in self.group.iterdescendants()
            if node.get("inkstitch:letter-group") == "glyph"
        ]
        if len(glyphs) < 3:
            # One or two initials: enlarge the “center” slot glyph when present.
            if len(glyphs) == 1 and self.settings.get("monogram_center"):
                self._scale_glyph(glyphs[0], CENTER_ENLARGEMENT_FACTOR)
            return
        self._scale_glyph(glyphs[1], CENTER_ENLARGEMENT_FACTOR)

    @staticmethod
    def _scale_glyph(glyph_node, factor: float) -> None:
        bbox = glyph_node.bounding_box()
        if bbox is None:
            return
        cx, cy = bbox.center.x, bbox.center.y
        scale = inkex.Transform(f"translate({cx},{cy}) scale({factor}) translate({-cx},{-cy})")
        glyph_node.transform = scale @ inkex.Transform(glyph_node.get("transform"))

    def get_preset_suite_name(self):
        return "monogram"
