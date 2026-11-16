# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

from typing import List, Optional

import wx
from wx.lib.agw import ultimatelistctrl as ulc

from ...exceptions import InkstitchException, format_uncaught_exception
from ...extensions.color_sort import ColorSort, ColorSortPlan, color_to_hex
from ...i18n import _
from ...stitch_plan import stitch_groups_to_stitch_plan
from ...utils.threading import ExitThread, check_stop_flag
from .. import WarningPanel, confirm_dialog, info_dialog


SWATCH_SIZE = 18


def _swatch_bitmap(hex_color: str, size: int = SWATCH_SIZE) -> wx.Bitmap:
    image = wx.Image(size, size)
    color = wx.Colour(hex_color)
    image.SetRGB(wx.Rect(0, 0, size, size), color.Red(), color.Green(), color.Blue())
    bitmap = image.ConvertToBitmap()
    dc = wx.MemoryDC(bitmap)
    dc.SetPen(wx.Pen(wx.Colour(80, 80, 80)))
    dc.SetBrush(wx.TRANSPARENT_BRUSH)
    dc.DrawRectangle(0, 0, size, size)
    dc.SelectObject(wx.NullBitmap)
    return bitmap


class ColorOrderPanel(wx.Panel):
    """Illustrator/Photoshop-style panel to reorder color runs before applying."""

    def __init__(self, parent, simulator, extension: ColorSort, plan: ColorSortPlan,
                 metadata=None, background_color='white'):
        super().__init__(parent, wx.ID_ANY)

        self.parent = parent
        self.simulator = simulator
        self.extension = extension
        self.plan = plan
        self.metadata = metadata or dict()
        self.background_color = background_color
        self.order: List[int] = list(plan.order)
        self._initial_order = list(plan.order)
        self._dragging_row: Optional[int] = None
        self._applied = False

        self.simulator.set_render_fn(self.render_stitch_plan)

        self.warning_panel = WarningPanel(self)
        self.warning_panel.Hide()

        self.help_text = wx.StaticText(
            self,
            label=_("Drag color runs to change stitch order, or use Auto Sort. "
                    "The simulator shows the proposed order; Apply restacks the document.")
        )
        self.help_text.Wrap(320)

        self.stats_label = wx.StaticText(self, label="")

        self.item_list = ulc.UltimateListCtrl(
            self,
            size=(320, 280),
            agwStyle=(ulc.ULC_REPORT | ulc.ULC_SINGLE_SEL | ulc.ULC_VRULES |
                      ulc.ULC_HRULES | ulc.ULC_HAS_VARIABLE_ROW_HEIGHT)
        )
        self.item_list.Bind(ulc.EVT_LIST_BEGIN_DRAG, self.on_begin_drag)
        self.item_list.Bind(ulc.EVT_LIST_END_DRAG, self.on_end_drag)

        self.move_up_button = wx.Button(self, label="↑", style=wx.BU_EXACTFIT)
        self.move_up_button.SetToolTip(_("Move selected color run earlier"))
        self.move_up_button.Bind(wx.EVT_BUTTON, self.on_move_up)
        self.move_down_button = wx.Button(self, label="↓", style=wx.BU_EXACTFIT)
        self.move_down_button.SetToolTip(_("Move selected color run later"))
        self.move_down_button.Bind(wx.EVT_BUTTON, self.on_move_down)

        self.auto_sort_button = wx.Button(self, label=_("Auto Sort"))
        self.auto_sort_button.SetToolTip(_("Find the order with the fewest thread changes"))
        self.auto_sort_button.Bind(wx.EVT_BUTTON, self.on_auto_sort)

        self.keep_overlap_checkbox = wx.CheckBox(self, label=_("Keep the order of overlapping objects"))
        self.keep_overlap_checkbox.SetValue(bool(self.extension.options.keep_overlap_order))
        self.keep_overlap_checkbox.SetToolTip(
            _("Objects stitched on top of each other keep their order, so the design still looks the same.")
        )
        self.keep_overlap_checkbox.Bind(wx.EVT_CHECKBOX, self.on_options_changed)

        self.add_trims_checkbox = wx.CheckBox(self, label=_("Trim the thread between distant objects"))
        self.add_trims_checkbox.SetValue(bool(self.extension.options.add_trims))
        self.add_trims_checkbox.SetToolTip(
            _("Objects of the same color which are far apart get a trim when they are stitched one after another.")
        )
        self.add_trims_checkbox.Bind(wx.EVT_CHECKBOX, self.on_trims_changed)

        self.selection_only_checkbox = wx.CheckBox(self, label=_("Sort only the selected objects"))
        self.selection_only_checkbox.SetValue(bool(self.extension.options.selection_only))
        self.selection_only_checkbox.SetToolTip(
            _("Leave everything which is not selected in place.")
        )
        self.selection_only_checkbox.Bind(wx.EVT_CHECKBOX, self.on_options_changed)

        self.cancel_button = wx.Button(self, wx.ID_ANY, _("Cancel"))
        self.cancel_button.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.apply_button = wx.Button(self, wx.ID_ANY, _("Apply"))
        self.apply_button.Bind(wx.EVT_BUTTON, self.on_apply)
        self.Bind(wx.EVT_CLOSE, self.on_cancel)

        self.__do_layout()
        self.refresh_list()
        self.update_stats()
        self.update_preview()

    def __do_layout(self) -> None:
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(self.warning_panel, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(self.help_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        main_sizer.Add(self.stats_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        main_sizer.Add(self.item_list, 1, wx.EXPAND | wx.ALL, 10)

        move_sizer = wx.BoxSizer(wx.HORIZONTAL)
        move_sizer.Add(self.move_up_button, 0, wx.RIGHT, 5)
        move_sizer.Add(self.move_down_button, 0, wx.RIGHT, 5)
        move_sizer.AddStretchSpacer(1)
        move_sizer.Add(self.auto_sort_button, 0)
        main_sizer.Add(move_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        main_sizer.Add(self.keep_overlap_checkbox, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        main_sizer.Add(self.add_trims_checkbox, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        main_sizer.Add(self.selection_only_checkbox, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons_sizer = wx.BoxSizer(wx.HORIZONTAL)
        buttons_sizer.Add(self.cancel_button, 0, wx.RIGHT, 5)
        buttons_sizer.Add(self.apply_button, 0, wx.BOTTOM, 5)
        main_sizer.Add(buttons_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        self.SetSizer(main_sizer)
        main_sizer.Fit(self)
        self.Layout()

    def refresh_list(self) -> None:
        selected = self.item_list.GetFirstSelected()
        self.item_list.Freeze()
        self.item_list.ClearAll()
        self.item_list.InsertColumn(0, "", width=SWATCH_SIZE + 12)
        self.item_list.InsertColumn(1, _("Color run"), width=ulc.ULC_AUTOSIZE_FILL)

        for row, item_index in enumerate(self.order):
            item = self.plan.block[item_index]
            first, last = item.colors
            self.item_list.InsertStringItem(row, "")
            self.item_list.SetItemWindow(row, 0, self._swatch_window(first, last), expand=True)
            self.item_list.SetStringItem(row, 1, item.label)

        # Extra disabled row so drag can reach the end of the list (Sew Stack pattern).
        end = len(self.order)
        self.item_list.InsertStringItem(end, "")
        self.item_list.EnableItem(end, enable=False)

        self.item_list.SetColumnWidth(0, SWATCH_SIZE + 12)
        self.item_list.SetColumnWidth(1, ulc.ULC_AUTOSIZE_FILL)
        self.item_list.Thaw()

        if 0 <= selected < len(self.order):
            self.item_list.Select(selected)

    def _swatch_window(self, first, last) -> wx.Panel:
        panel = wx.Panel(self.item_list, size=(SWATCH_SIZE + 4, SWATCH_SIZE + 4))
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        first_bitmap = wx.StaticBitmap(panel, bitmap=_swatch_bitmap(color_to_hex(first)))
        sizer.Add(first_bitmap, 0, wx.ALIGN_CENTER_VERTICAL)
        if first != last:
            second_bitmap = wx.StaticBitmap(panel, bitmap=_swatch_bitmap(color_to_hex(last)))
            sizer.Add(second_bitmap, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 2)
        panel.SetSizer(sizer)
        return panel

    def update_stats(self) -> None:
        if not self.plan.block:
            self.stats_label.SetLabel(_("Nothing to reorder."))
            self.apply_button.Enable(False)
            return

        before = self.plan.changes_before
        after = self.extension.changes_for_order(self.plan, self.order)
        text = _("Thread changes: {before} → {after}").format(before=before, after=after)
        if self.plan.left_in_place:
            text += "\n" + _("Left in place: {count}").format(count=self.plan.left_in_place)
        self.stats_label.SetLabel(text)
        self.apply_button.Enable(len(self.order) > 1)
        self.Layout()

    def move_item(self, from_index: int, to_index: int) -> bool:
        if from_index == to_index:
            return False
        if not (0 <= from_index < len(self.order) and 0 <= to_index < len(self.order)):
            return False
        item = self.order.pop(from_index)
        self.order.insert(to_index, item)
        self.refresh_list()
        self.item_list.Select(to_index)
        self.update_stats()
        self.update_preview()
        return True

    def on_begin_drag(self, event) -> None:
        self._dragging_row = event.Index

    def on_end_drag(self, event) -> None:
        if self._dragging_row is None:
            return
        destination = event.Index
        if destination > self._dragging_row:
            destination -= 1
        self.move_item(self._dragging_row, destination)
        self._dragging_row = None

    def on_move_up(self, event) -> None:
        index = self.item_list.GetFirstSelected()
        if index > 0:
            self.move_item(index, index - 1)

    def on_move_down(self, event) -> None:
        index = self.item_list.GetFirstSelected()
        if 0 <= index < len(self.order) - 1:
            self.move_item(index, index + 1)

    def on_auto_sort(self, event) -> None:
        self.order = list(self.extension.auto_order(self.plan))
        self._initial_order = list(self.order)
        self.refresh_list()
        self.update_stats()
        self.update_preview()

    def on_trims_changed(self, event) -> None:
        self.extension.options.add_trims = self.add_trims_checkbox.GetValue()

    def on_options_changed(self, event) -> None:
        plan = self.extension.prepare_plan(
            keep_overlap_order=self.keep_overlap_checkbox.GetValue(),
            selection_only=self.selection_only_checkbox.GetValue()
        )
        if plan is None:
            self.order = []
            self.refresh_list()
            self.update_stats()
            self.update_preview()
            self._show_warning(_("No embroiderable objects to sort."))
            return

        self.plan = plan
        self.order = list(plan.order)
        self._initial_order = list(self.order)
        self.refresh_list()
        self.update_stats()
        self.update_preview()

        if plan.selection_active and plan.item_count < 2:
            self._show_warning(_("Please select at least two objects to sort by color."))
        else:
            self._hide_warning()

    def update_preview(self, event=None) -> None:
        self.simulator.stop()
        self.simulator.render()

    def render_stitch_plan(self):
        try:
            self._hide_warning()
            if not self.plan.block:
                return None

            elements = self.extension.elements_for_order(self.plan, self.order)
            next_elements = [None]
            if len(elements) > 1:
                next_elements = elements[1:] + next_elements

            stitch_groups = []
            last_stitch_group = None
            for element, next_element in zip(elements, next_elements):
                check_stop_flag()
                stitch_groups.extend(element.embroider(last_stitch_group, next_element))
                if stitch_groups:
                    last_stitch_group = stitch_groups[-1]

            if stitch_groups:
                return stitch_groups_to_stitch_plan(
                    stitch_groups,
                    collapse_len=self.metadata['collapse_len_mm'],
                    min_stitch_len=self.metadata['min_stitch_len_mm']
                )
        except (SystemExit, ExitThread):
            raise
        except InkstitchException as exc:
            wx.CallAfter(self._show_warning, str(exc))
        except Exception:
            wx.CallAfter(self._show_warning, format_uncaught_exception())
        return None

    def _hide_warning(self) -> None:
        self.warning_panel.clear()
        self.warning_panel.Hide()
        self.Layout()

    def _show_warning(self, warning_text: str) -> None:
        self.warning_panel.set_warning_text(warning_text)
        self.warning_panel.Show()
        self.Layout()

    def confirm_close(self) -> bool:
        self.simulator.stop()
        if self._applied or self.order == self._initial_order:
            return True
        return confirm_dialog(self, _("Discard color order changes?"))

    def close(self) -> None:
        self.simulator.stop()
        wx.CallAfter(self.GetTopLevelParent().close)

    def on_cancel(self, event) -> None:
        self.simulator.stop()
        wx.CallAfter(self.GetTopLevelParent().cancel)

    def on_apply(self, event) -> None:
        if len(self.order) < 2:
            return
        self.extension.options.add_trims = self.add_trims_checkbox.GetValue()
        message = self.extension.apply_plan(self.plan, self.order, self.add_trims_checkbox.GetValue())
        self._applied = True
        info_dialog(self, message, caption=_("Color Order"))
        self.close()
