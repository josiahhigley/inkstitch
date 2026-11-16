# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

from typing import List, Optional

import wx
from wx.lib.agw import ultimatelistctrl as ulc

from ...elements import iterate_nodes, nodes_to_elements
from ...exceptions import InkstitchException, format_uncaught_exception
from ...i18n import _
from ...stitch_plan import stitch_groups_to_stitch_plan
from ...svg.tags import INKSCAPE_GROUPMODE, SVG_GROUP_TAG
from ...utils.threading import ExitThread, check_stop_flag
from .. import WarningPanel, confirm_dialog


class DesignEntry:
    def __init__(self, root, label: str, kind: str):
        self.root = root
        self.label = label
        self.kind = kind


class ObjectsPanePanel(wx.Panel):
    """List imported designs and groups; select or delete them."""

    def __init__(self, parent, simulator, extension, metadata=None, background_color='white'):
        super().__init__(parent, wx.ID_ANY)
        self.parent = parent
        self.simulator = simulator
        self.extension = extension
        self.metadata = metadata or dict()
        self.background_color = background_color
        self.entries: List[DesignEntry] = []
        self._preview_roots = None

        self.simulator.set_render_fn(self.render_stitch_plan)

        self.warning_panel = WarningPanel(self)
        self.warning_panel.Hide()

        self.help = wx.StaticText(
            self,
            label=_("Click a design to select it on the canvas. "
                    "This is the Objects list for merged purchased files.")
        )
        self.help.Wrap(320)

        self.item_list = ulc.UltimateListCtrl(
            self,
            size=(320, 320),
            agwStyle=ulc.ULC_REPORT | ulc.ULC_SINGLE_SEL | ulc.ULC_VRULES | ulc.ULC_HRULES,
        )
        self.item_list.InsertColumn(0, _("Design"))
        self.item_list.InsertColumn(1, _("Kind"))
        self.item_list.SetColumnWidth(0, 200)
        self.item_list.SetColumnWidth(1, 100)
        self.item_list.Bind(ulc.EVT_LIST_ITEM_SELECTED, self.on_select)
        self.item_list.Bind(ulc.EVT_LIST_ITEM_ACTIVATED, self.on_select)

        self.select_button = wx.Button(self, label=_("Select on canvas"))
        self.select_button.Bind(wx.EVT_BUTTON, self.on_select)
        self.delete_button = wx.Button(self, label=_("Delete"))
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.refresh_button = wx.Button(self, label=_("Refresh"))
        self.refresh_button.Bind(wx.EVT_BUTTON, lambda _e: self.refresh_list())

        self.close_button = wx.Button(self, label=_("Close"))
        self.close_button.Bind(wx.EVT_BUTTON, self.on_close)

        self.__do_layout()
        self.refresh_list()
        self.update_preview()

    def __do_layout(self) -> None:
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.warning_panel, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.help, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.item_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.select_button, 0, wx.RIGHT, 4)
        row.Add(self.delete_button, 0, wx.RIGHT, 4)
        row.Add(self.refresh_button, 0)
        sizer.Add(row, 0, wx.ALL, 8)
        sizer.Add(self.close_button, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(sizer)

    def refresh_list(self) -> None:
        self.entries = self._collect_entries()
        self.item_list.Freeze()
        self.item_list.DeleteAllItems()
        for index, entry in enumerate(self.entries):
            self.item_list.InsertStringItem(index, entry.label)
            self.item_list.SetStringItem(index, 1, entry.kind)
        self.item_list.Thaw()
        self._preview_roots = None
        self.update_preview()

    def _collect_entries(self) -> List[DesignEntry]:
        entries: List[DesignEntry] = []
        svg = self.extension.svg
        for node in svg.iterdescendants(SVG_GROUP_TAG):
            if node.get(INKSCAPE_GROUPMODE) == "layer":
                continue
            design_name = node.get("inkstitch:design_import")
            if design_name:
                entries.append(DesignEntry(node, node.label or design_name, _("Imported")))
                continue
            # Top-level groups under a layer that contain embroidery
            parent = node.getparent()
            if parent is not None and parent.get(INKSCAPE_GROUPMODE) == "layer":
                if any(True for _ in iterate_nodes(node)):
                    entries.append(DesignEntry(node, node.label or node.get_id() or _("Group"), _("Group")))
        return entries

    def _selected_entry(self) -> Optional[DesignEntry]:
        index = self.item_list.GetFirstSelected()
        if index < 0 or index >= len(self.entries):
            return None
        return self.entries[index]

    def on_select(self, _event=None) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self.extension.svg.selection.set(entry.root)
        self._preview_roots = [entry.root]
        self.update_preview()

    def on_delete(self, _event=None) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if not confirm_dialog(self, _("Delete “{name}” from the document?").format(name=entry.label)):
            return
        parent = entry.root.getparent()
        if parent is not None:
            parent.remove(entry.root)
        self.refresh_list()

    def update_preview(self) -> None:
        self.simulator.stop()
        self.simulator.render()

    def render_stitch_plan(self):
        try:
            wx.CallAfter(self._hide_warning)
            if self._preview_roots:
                nodes = []
                for root in self._preview_roots:
                    nodes.extend(iterate_nodes(root))
            else:
                nodes = iterate_nodes(self.extension.svg)
            elements = nodes_to_elements(nodes)
            stitch_groups = []
            last = None
            nxt = elements[1:] + [None] if elements else []
            for element, next_element in zip(elements, nxt):
                check_stop_flag()
                stitch_groups.extend(element.embroider(last, next_element))
                if stitch_groups:
                    last = stitch_groups[-1]
            if stitch_groups:
                return stitch_groups_to_stitch_plan(
                    stitch_groups,
                    collapse_len=self.metadata['collapse_len_mm'],
                    min_stitch_len=self.metadata['min_stitch_len_mm'],
                )
        except (SystemExit, ExitThread):
            raise
        except InkstitchException as exc:
            wx.CallAfter(self._show_warning, str(exc))
        except Exception:
            wx.CallAfter(self._show_warning, format_uncaught_exception())
        return None

    def _hide_warning(self):
        self.warning_panel.clear()
        self.warning_panel.Hide()
        self.Layout()

    def _show_warning(self, text):
        self.warning_panel.set_warning_text(text)
        self.warning_panel.Show()
        self.Layout()

    def on_close(self, _event=None):
        wx.CallAfter(self.GetTopLevelParent().close)

    def confirm_close(self):
        return True
