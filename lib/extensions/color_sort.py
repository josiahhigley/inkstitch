# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

from collections import defaultdict
from heapq import heappop, heappush
from time import monotonic
from typing import (Dict, Hashable, Iterator, List, Optional, Sequence, Set,
                    Tuple, cast)

from dataclasses import dataclass

from inkex import BaseElement, Boolean, errormsg
from shapely import STRtree, box, distance, dwithin, make_valid
from shapely.errors import GEOSException, UnsupportedGEOSVersionError
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..commands import find_commands, is_command, is_command_symbol
from ..elements import (Clone, EmbroideryElement, FillStitch, SatinColumn,
                        Stroke, iterate_nodes, node_to_elements)
from ..i18n import _
from ..svg import PIXELS_PER_MM, get_node_transform
from ..svg.tags import (INKSCAPE_GROUPMODE, INKSCAPE_LABEL, INKSTITCH_ATTRIBS,
                        INKSTITCH_LETTERING, SVG_GROUP_TAG)
from ..threads import ThreadColor
from .base import InkstitchExtension

# Style properties a moved object would otherwise lose, when they were set on a group it is taken out of.
INHERITED_STYLES = ('fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin',
                    'fill-rule', 'fill-opacity', 'stroke-opacity')

# Objects closer together than this are treated as stitched on top of each other.
OVERLAP_TOLERANCE_MM = 0.5

# The search for the best order gives up being exact after this many steps or this much time.
MAX_SEARCH_STATES = 20000
MAX_SEARCH_SECONDS = 5.0

ColorPair = Tuple[Hashable, Hashable]


def count_color_changes(colors: Sequence[Hashable]) -> int:
    return sum(1 for previous, current in zip(colors, colors[1:]) if previous != current)


def color_sorted_order(colors: Sequence[ColorPair],  # noqa: C901
                       must_follow: Sequence[Set[int]],
                       previous_color: Optional[Hashable] = None,
                       next_color: Optional[Hashable] = None,
                       max_states: int = MAX_SEARCH_STATES) -> List[int]:
    """Find the stitching order with the fewest thread changes.

    colors:         the first and the last thread color of each item, in the current stitching order
    must_follow:    for each item the indices of the items which have to be stitched before it
    previous_color: the thread which is in use before the first item (None: nothing is stitched before)
    next_color:     the thread which is needed after the last item (None: nothing is stitched afterwards)

    Returns the item indices in their new stitching order.

    Stitching everything which is possible with the current thread before changing it never costs a thread
    change.  So the search only has to decide which thread comes next (A* search over the sets of stitched
    items).  Amongst the best orders it prefers the one which stays closest to the current order.
    """
    count = len(colors)
    bits = [1 << index for index in range(count)]
    everything = (1 << count) - 1
    blockers = [sum(bits[blocker] for blocker in must_follow[index]) for index in range(count)]

    # the items with a single color by color, and the items with more than one color
    plain: Dict[Hashable, List[int]] = defaultdict(list)
    mixed: List[int] = []
    for index, (first, last) in enumerate(colors):
        if first == last:
            plain[first].append(index)
        else:
            mixed.append(index)
    plain_bits = {color: sum(bits[index] for index in indices) for color, indices in plain.items()}
    mixed_bits_by_last_color: Dict[Hashable, int] = defaultdict(int)
    for index in mixed:
        mixed_bits_by_last_color[colors[index][1]] |= bits[index]

    def stitch_run(done: int, color: Optional[Hashable], order: Tuple[int, ...]) -> Tuple[int, Tuple[int, ...]]:
        # stitch everything which doesn't need a thread change
        indices = plain.get(color)
        if indices:
            run = []
            progress = True
            while progress:
                progress = False
                for index in indices:
                    if not done & bits[index] and not blockers[index] & ~done:
                        done |= bits[index]
                        run.append(index)
                        progress = True
            order += tuple(run)
        return done, order

    def next_steps(done: int, color: Optional[Hashable], order: Tuple[int, ...]) -> Iterator[Tuple[int, int, Hashable, Tuple[int, ...]]]:
        # carry on with an other thread color ...
        for first, indices in plain.items():
            if plain_bits[first] & ~done:
                for index in indices:
                    if not done & bits[index] and not blockers[index] & ~done:
                        new_done, new_order = stitch_run(done, first, order)
                        yield 0 if color is None or color == first else 1, new_done, first, new_order
                        break
        # ... or with an item which uses more than one color
        for index in mixed:
            if not done & bits[index] and not blockers[index] & ~done:
                first, last = colors[index]
                new_done, new_order = stitch_run(done | bits[index], last, order + (index,))
                yield 0 if color is None or color == first else 1, new_done, last, new_order

    def estimate(done: int, color: Optional[Hashable]) -> int:
        # Each remaining color needs at least one more thread change,
        # unless an item with more than one color ends with it.
        changes = sum(1 for plain_color, color_bits in plain_bits.items()
                      if color_bits & ~done and not mixed_bits_by_last_color.get(plain_color, 0) & ~done)
        if color is None:
            changes -= 1
        return max(changes, 0)

    def final_cost(color: Optional[Hashable]) -> int:
        return 0 if next_color is None or color is None or color == next_color else 1

    def finish_quickly(cost: int, done: int, color: Optional[Hashable], order: Tuple[int, ...]) -> Tuple[int, ...]:
        # always take the step which looks best
        while done != everything:
            steps = [(cost + step_cost + estimate(new_done, new_color), new_order, cost + step_cost, new_done, new_color)
                     for step_cost, new_done, new_color, new_order in next_steps(done, color, order)]
            if not steps:
                break
            _total, order, cost, done, color = min(steps, key=lambda step: step[:2])
        return order

    start_done, start_order = stitch_run(0, previous_color, ())
    queue: List[Tuple[int, Tuple[int, ...], int, int, Optional[Hashable]]] = [
        (estimate(start_done, previous_color), start_order, 0, start_done, previous_color)
    ]
    visited: Set[Tuple[int, Optional[Hashable]]] = set()
    best_order = start_order
    deadline = monotonic() + MAX_SEARCH_SECONDS

    while queue:
        _total, order, cost, done, color = heappop(queue)
        if done == everything:
            return list(order)
        if (done, color) in visited:
            continue
        visited.add((done, color))
        if len(order) > len(best_order):
            best_order = order

        if len(visited) > max_states or (len(visited) % 256 == 0 and monotonic() > deadline):
            best_order = finish_quickly(cost, done, color, order)
            break

        for step_cost, new_done, new_color, new_order in next_steps(done, color, order):
            new_cost = cost + step_cost
            if new_done == everything:
                new_cost += final_cost(new_color)
            heappush(queue, (new_cost + estimate(new_done, new_color), new_order, new_cost, new_done, new_color))

    # Either the search has been cut short, or must_follow is circular.  Keep what is left in its current order.
    stitched = set(best_order)
    return list(best_order) + [index for index in range(count) if index not in stitched]


class StitchedNode:
    """An object in the design which gets stitched."""

    def __init__(self, position: int, node: BaseElement, elements: List[EmbroideryElement], colors: Tuple[Hashable, ...]):
        # the place in the stitching order of the whole design
        self.position = position
        self.node = node
        self.elements = elements
        # the thread colors of this object in stitching order, usually just one
        self.colors = colors
        self._geometry: Optional[BaseGeometry] = None
        self._has_geometry = False

    @property
    def geometry(self) -> Optional[BaseGeometry]:
        """The area this object covers.  None if it is unknown."""
        if not self._has_geometry:
            self._has_geometry = True
            try:
                geometries = [_element_geometry(element) for element in self.elements]
                self._geometry = unary_union(geometries) if len(geometries) > 1 else geometries[0]
            except Exception:
                self._geometry = None
        return self._geometry


def color_to_hex(color: Hashable) -> str:
    """Hex string for a color stored as an RGB tuple or any ThreadColor input."""
    if isinstance(color, tuple) and len(color) == 3:
        try:
            return "#%02X%02X%02X" % tuple(int(channel) for channel in color)
        except (TypeError, ValueError):
            pass
    return ThreadColor(color).to_hex_str()


class SortItem:
    """What is moved in one piece: a single object, or a group in which everything has the same color."""

    def __init__(self, root: BaseElement, nodes: List[StitchedNode]):
        self.root = root
        self.nodes = nodes

    @property
    def colors(self) -> ColorPair:
        return self.nodes[0].colors[0], self.nodes[-1].colors[-1]

    @property
    def has_stop(self) -> bool:
        """True when this piece ends with a machine stop (applique phase barrier)."""
        for stitched_node in self.nodes:
            for element in stitched_node.elements:
                if getattr(element, "stop_after", False) or element.has_command("stop"):
                    return True
        return False

    @property
    def label(self) -> str:
        name = self.root.get(INKSCAPE_LABEL) or self.root.get('id')
        first, last = self.colors
        if first == last:
            color_text = color_to_hex(first)
        else:
            color_text = f"{color_to_hex(first)} → {color_to_hex(last)}"
        if self.has_stop:
            color_text = f"{color_text} · STOP"
        if name:
            return f"{name} ({color_text})"
        return color_text


@dataclass
class ColorSortPlan:
    """Everything needed to preview and apply a color order."""

    stitched: List[StitchedNode]
    block: List[SortItem]
    left_in_place: int
    first: int
    staying: List[StitchedNode]
    before: Optional[StitchedNode]
    after: Optional[StitchedNode]
    order: List[int]
    changes_before: int
    only_selection: bool
    selection_active: bool
    item_count: int
    must_follow: List[Set[int]]


def _element_geometry(element: EmbroideryElement) -> BaseGeometry:
    if isinstance(element, SatinColumn):
        rails = element.line_string_rails
        if len(rails) == 2:
            return make_valid(Polygon(list(rails[0].coords) + list(reversed(rails[1].coords))))
    elif isinstance(element, Stroke):
        # the shape of a stroke is its convex hull, which covers a lot more than the stitches do
        return element.as_multi_line_string()
    return cast(BaseGeometry, element.shape)


def _near(geometry1: Optional[BaseGeometry], geometry2: Optional[BaseGeometry], max_distance: float) -> bool:
    """Are these two closer together than max_distance?  When in doubt they are."""
    if geometry1 is None or geometry2 is None:
        return True
    if geometry1.is_empty or geometry2.is_empty:
        return False
    try:
        return bool(dwithin(geometry1, geometry2, max_distance))
    except UnsupportedGEOSVersionError:
        return bool(distance(geometry1, geometry2) <= max_distance)
    except GEOSException:
        return True


def _is_layer(node: BaseElement) -> bool:
    return node.tag == SVG_GROUP_TAG and node.get(INKSCAPE_GROUPMODE) == "layer"


def _parent(node: BaseElement) -> BaseElement:
    parent = node.getparent()
    assert parent is not None, "This should be part of a tree and therefore have a parent"
    return parent


class ColorSort(InkstitchExtension):
    '''
    Re-stack objects so that objects of the same color are stitched one after another.
    This reduces the number of thread changes, for example after combining several designs.
    '''

    def __init__(self, *args, **kwargs):
        InkstitchExtension.__init__(self, *args, **kwargs)
        self.cancelled = False
        self.arg_parser.add_argument("--notebook")
        self.arg_parser.add_argument("--keep-overlap-order", type=Boolean, default=True, dest="keep_overlap_order")
        self.arg_parser.add_argument("--add-trims", type=Boolean, default=True, dest="add_trims")
        self.arg_parser.add_argument("--selection-only", type=Boolean, default=False, dest="selection_only")

    def cancel(self) -> None:
        self.cancelled = True

    def effect(self) -> None:
        """Open the Color Order panel with a live stitch preview."""
        import sys

        self.tolerance = OVERLAP_TOLERANCE_MM * PIXELS_PER_MM
        plan = self.prepare_plan()
        if plan is None:
            self.no_elements_error()
            return
        if plan.selection_active and plan.item_count < 2:
            errormsg(_("Please select at least two objects to sort by color."))
            return

        # Lazy-import wx/gui so headless tests can call apply_sort() without wx.
        import wx

        from ..gui.color_order import ColorOrderPanel
        from ..gui.simulator import SplitSimulatorWindow
        from ..utils.svg_data import get_pagecolor

        metadata = self.get_inkstitch_metadata()
        background_color = get_pagecolor(self.svg.namedview)
        app = wx.App()
        frame = SplitSimulatorWindow(
            title=_("Ink/Stitch Color Order"),
            panel_class=ColorOrderPanel,
            extension=self,
            plan=plan,
            on_cancel=self.cancel,
            metadata=metadata,
            background_color=background_color,
            target_duration=5
        )
        frame.Show()
        app.MainLoop()

        if self.cancelled:
            # Prevent writing SVG when the user cancelled; the DOM was not applied.
            sys.exit(0)

    def apply_sort(self) -> None:
        """Apply color sorting without opening the Color Order panel (used by tests)."""
        self.tolerance = OVERLAP_TOLERANCE_MM * PIXELS_PER_MM
        plan = self.prepare_plan()
        if plan is None:
            self.no_elements_error()
            return
        if plan.selection_active and plan.item_count < 2:
            errormsg(_("Please select at least two objects to sort by color."))
            return

        # Don't touch the design if auto-sort does not reduce thread changes.
        if len(plan.block) > 1 and self.changes_for_order(plan, plan.order) < plan.changes_before:
            errormsg(self.apply_plan(plan, plan.order, self.options.add_trims))
            return

        errormsg(self._status_message(
            plan.changes_before,
            self._count_thread_changes(self._stitched_nodes()),
            plan.left_in_place,
            0,
            False,
            plan.only_selection
        ))

    def prepare_plan(self, keep_overlap_order: Optional[bool] = None, selection_only: Optional[bool] = None) -> Optional[ColorSortPlan]:
        """Build a plan for previewing and applying a color order.  None if nothing can be moved."""
        if not hasattr(self, 'tolerance'):
            self.tolerance = OVERLAP_TOLERANCE_MM * PIXELS_PER_MM
        if keep_overlap_order is not None:
            self.options.keep_overlap_order = keep_overlap_order
        if selection_only is not None:
            self.options.selection_only = selection_only

        stitched = self._stitched_nodes()
        selected = self._selected_nodes()
        movable = {stitched_node.node for stitched_node in stitched if selected is None or stitched_node.node in selected}
        if not movable:
            return None

        items = self._items(stitched, movable)
        selection_active = selected is not None
        only_selection = selection_active and len(movable) < len(stitched)
        changes_before = self._count_thread_changes(stitched)
        block, left_in_place = self._sortable_items(items, stitched)

        if not block:
            return ColorSortPlan(
                stitched=stitched,
                block=[],
                left_in_place=left_in_place,
                first=0,
                staying=list(stitched),
                before=None,
                after=None,
                order=[],
                changes_before=changes_before,
                only_selection=only_selection,
                selection_active=selection_active,
                item_count=len(items),
                must_follow=[],
            )

        block_nodes = {stitched_node.node for item in block for stitched_node in item.nodes}
        first = block[0].nodes[0].position
        staying = [stitched_node for stitched_node in stitched[first:] if stitched_node.node not in block_nodes]
        before = stitched[first - 1] if first > 0 else None
        after = staying[0] if staying else None
        must_follow = self._must_follow(block)
        plan = ColorSortPlan(
            stitched=stitched,
            block=block,
            left_in_place=left_in_place,
            first=first,
            staying=staying,
            before=before,
            after=after,
            order=list(range(len(block))),
            changes_before=changes_before,
            only_selection=only_selection,
            selection_active=selection_active,
            item_count=len(items),
            must_follow=must_follow,
        )
        self.auto_order(plan)
        return plan

    def auto_order(self, plan: ColorSortPlan) -> List[int]:
        """Set plan.order to the order with the fewest thread changes."""
        if len(plan.block) < 2:
            plan.order = list(range(len(plan.block)))
            return plan.order
        plan.order = color_sorted_order(
            [item.colors for item in plan.block],
            plan.must_follow,
            plan.before.colors[-1] if plan.before else None,
            plan.after.colors[0] if plan.after else None
        )
        return plan.order

    def nodes_for_order(self, plan: ColorSortPlan, order: Sequence[int]) -> List[StitchedNode]:
        """Stitched nodes in the order they would sew after applying this order."""
        sorted_nodes = [stitched_node for index in order for stitched_node in plan.block[index].nodes]
        return plan.stitched[:plan.first] + sorted_nodes + plan.staying

    def changes_for_order(self, plan: ColorSortPlan, order: Sequence[int]) -> int:
        """How many thread changes the whole design would have with this order."""
        return self._count_thread_changes(self.nodes_for_order(plan, order))

    def elements_for_order(self, plan: ColorSortPlan, order: Sequence[int]) -> List[EmbroideryElement]:
        """Embroidery elements in preview order, without changing the document."""
        return [element for stitched_node in self.nodes_for_order(plan, order) for element in stitched_node.elements]

    def apply_plan(self, plan: ColorSortPlan, order: Sequence[int], add_trims: Optional[bool] = None) -> str:
        """Restack to the given order and return a status message for the user."""
        if add_trims is None:
            add_trims = self.options.add_trims

        trims = 0
        lettering_split = False
        if len(plan.block) > 1:
            sorted_nodes = [stitched_node for index in order for stitched_node in plan.block[index].nodes]
            if add_trims:
                trims = self._add_trims(sorted_nodes + plan.staying[:1])
            block_nodes = {stitched_node.node for item in plan.block for stitched_node in item.nodes}
            lettering_split = self._restack(
                plan.block, list(order), {stitched_node.node for stitched_node in plan.stitched} - block_nodes
            )

        return self._status_message(
            plan.changes_before,
            self._count_thread_changes(self._stitched_nodes()),
            plan.left_in_place,
            trims,
            lettering_split,
            plan.only_selection
        )

    def _status_message(self, changes_before: int, changes_after: int, left_in_place: int, trims: int,
                        lettering_split: bool, only_selection: bool) -> str:
        message = _("Thread changes before sorting: {before}\nThread changes after sorting: {after}").format(
            before=changes_before, after=changes_after)
        if only_selection:
            message += "\n\n" + _("Only the selected objects have been sorted.")
        if lettering_split:
            message += "\n\n" + _("Lettering with several colors has been split up. It can no longer be edited as text.")
        if trims:
            message += "\n\n" + _("Trims added between objects of the same color which are far apart: {trims}").format(trims=trims)
        if left_in_place:
            message += "\n\n" + _("Objects left in place, because they overlap objects which are not selected "
                                  "or because they are in a clipped group: {count}").format(count=left_in_place)
        return message

    def _report(self, changes_before: int, changes_after: int, left_in_place: int, trims: int, lettering_split: bool,
                only_selection: bool) -> None:
        errormsg(self._status_message(changes_before, changes_after, left_in_place, trims, lettering_split, only_selection))

    def _stitched_nodes(self) -> List[StitchedNode]:
        stitched: List[StitchedNode] = []
        for node in iterate_nodes(self.svg):
            elements: List[EmbroideryElement] = [element for element in node_to_elements(node)
                                                 if isinstance(element, (FillStitch, Stroke, SatinColumn, Clone))]
            if elements:
                stitched.append(StitchedNode(len(stitched), node, elements, self._colors(elements)))
        return stitched

    def _selected_nodes(self) -> Optional[Set[BaseElement]]:
        # Sort the whole design unless the user asks for the selection.  Inkscape selects what has just been imported
        # or moved, so after combining designs there usually is a selection which nobody thinks of.
        if not self.options.selection_only or not self.svg.selection:
            return None
        return set(iterate_nodes(self.svg, selection=list(self.svg.selection)))

    def _colors(self, elements: List[EmbroideryElement]) -> Tuple[Hashable, ...]:
        colors: List[Hashable] = []
        for element in elements:
            element_colors = [element.color]
            if isinstance(element, Clone):
                # a clone of a group may have more than one color
                first_and_last = [cloned.color for cloned in element.first_and_last_element() if cloned is not None]
                element_colors = first_and_last or element_colors
            for color in element_colors:
                # this is how the stitch plan decides if a thread change is necessary
                rgb = ThreadColor(color).rgb
                if not colors or colors[-1] != rgb:
                    colors.append(rgb)
        return tuple(colors)

    def _count_thread_changes(self, stitched: List[StitchedNode]) -> int:
        return count_color_changes([color for stitched_node in stitched for color in stitched_node.colors])

    def _items(self, stitched: List[StitchedNode], movable: Set[BaseElement]) -> List[SortItem]:
        """Split the objects which may be moved into the pieces which are moved.

        A group in which everything has the same color stays together.  This keeps the document tidy and
        for example lettering editable.  Layers are never moved.
        """
        members: Dict[BaseElement, List[StitchedNode]] = defaultdict(list)
        for stitched_node in stitched:
            for ancestor in stitched_node.node.iterancestors():
                members[ancestor].append(stitched_node)

        stays_together: Dict[BaseElement, bool] = {}

        def moves_in_one_piece(group: BaseElement) -> bool:
            if group not in stays_together:
                colors = members[group][0].colors
                stays_together[group] = (
                    group.tag == SVG_GROUP_TAG and not _is_layer(group) and len(colors) == 1 and
                    all(stitched_node.node in movable and stitched_node.colors == colors for stitched_node in members[group])
                )
            return stays_together[group]

        items: List[SortItem] = []
        for stitched_node in stitched:
            if stitched_node.node not in movable:
                continue
            root = stitched_node.node
            parent = root.getparent()
            while parent is not None and moves_in_one_piece(parent):
                root = parent
                parent = root.getparent()
            if items and items[-1].root is root:
                items[-1].nodes.append(stitched_node)
            else:
                items.append(SortItem(root, [stitched_node]))
        return items

    def _sortable_items(self, items: List[SortItem], stitched: List[StitchedNode]) -> Tuple[List[SortItem], int]:
        """Leave out the items which have to stay where they are.

        Sorted items end up in one place: where the first of them is now.  On their way there they must not
        pass an object which stays in place and which they overlap, and they can't leave a clipped group.

        Returns the items to sort and the number of items which have been left out.
        """
        target = self._target([item.root for item in items])
        candidates = [item for item in items if not self._leaves_clip(item.root, target)]
        if not candidates or not self.options.keep_overlap_order:
            return candidates, len(items) - len(candidates)

        candidate_nodes = {stitched_node.node for item in candidates for stitched_node in item.nodes}
        stays = [stitched_node.node not in candidate_nodes for stitched_node in stitched]
        first = candidates[0].nodes[0].position

        block = []
        for item in candidates:
            start = item.nodes[0].position
            passed = [stitched[index] for index in range(first, start) if stays[index]]
            if any(_near(stitched_node.geometry, other.geometry, self.tolerance) for other in passed for stitched_node in item.nodes):
                for index in range(start, start + len(item.nodes)):
                    stays[index] = True
            else:
                block.append(item)
        return block, len(items) - len(block)

    def _leaves_clip(self, root: BaseElement, target: BaseElement) -> bool:
        for ancestor in root.iterancestors():
            if ancestor is target:
                return False
            if getattr(ancestor, "clip", None) is not None:
                return True
        return False

    def _target(self, roots: List[BaseElement]) -> BaseElement:
        """The innermost group or layer which contains all of these."""
        ancestors = [list(root.iterancestors()) for root in roots]
        common = set(ancestors[0]).intersection(*ancestors[1:])
        target = next(ancestor for ancestor in ancestors[0] if ancestor in common)

        if not _is_layer(target) and not any(_is_layer(ancestor) for ancestor in target.iterancestors()):
            # don't move objects out of all layers, use the layer of the first object instead
            layers = [ancestor for ancestor in ancestors[0] if _is_layer(ancestor)]
            if layers:
                target = layers[-1]
        return target

    def _must_follow(self, block: List[SortItem]) -> List[Set[int]]:
        """Items which lie on top of other items have to be stitched after them."""
        must_follow: List[Set[int]] = [set() for _item in block]
        if not self.options.keep_overlap_order:
            return must_follow

        geometries = []
        owners = []
        for index, item in enumerate(block):
            for stitched_node in item.nodes:
                geometry = stitched_node.geometry
                if geometry is None:
                    # Unknown geometry: treat it as overlapping everything, so it keeps its place.
                    must_follow[index].update(range(index))
                    for later in range(index + 1, len(block)):
                        must_follow[later].add(index)
                elif not geometry.is_empty:
                    geometries.append(geometry)
                    owners.append(index)

        tree = STRtree(geometries)
        for geometry, owner in zip(geometries, owners):
            min_x, min_y, max_x, max_y = geometry.bounds
            surroundings = box(min_x - self.tolerance, min_y - self.tolerance, max_x + self.tolerance, max_y + self.tolerance)
            for candidate in tree.query(surroundings):
                other_owner = owners[candidate]
                if other_owner < owner and other_owner not in must_follow[owner] and _near(geometry, geometries[candidate], self.tolerance):
                    must_follow[owner].add(other_owner)
        return must_follow

    def _add_trims(self, sorted_nodes: List[StitchedNode]) -> int:
        """Trim the thread where the new order makes it travel a long way without a thread change.

        Before sorting the thread has been cut in these places, because the color changed.
        """
        min_jump = float(self.get_inkstitch_metadata()['collapse_len_mm'] or 3.0) * PIXELS_PER_MM

        trims = 0
        for first_node, second_node in zip(sorted_nodes, sorted_nodes[1:]):
            element = first_node.elements[-1]
            if second_node.position == first_node.position + 1:
                # these two have been stitched one after another before
                continue
            if first_node.colors[-1] != second_node.colors[0]:
                continue
            if element.trim_after or element.stop_after or element.has_command("trim") or element.has_command("stop"):
                continue
            if first_node.geometry is None or second_node.geometry is None:
                continue
            if not _near(first_node.geometry, second_node.geometry, min_jump):
                first_node.node.set(INKSTITCH_ATTRIBS['trim_after'], 'true')
                trims += 1
        return trims

    def _restack(self, block: List[SortItem], order: List[int], staying: Set[BaseElement]) -> bool:
        """Move the items into their new order.  Returns True if this takes objects out of lettering."""
        roots = [item.root for item in block]
        target = self._target(roots)
        lettering = {ancestor for root in roots for ancestor in root.iterancestors() if INKSTITCH_LETTERING in ancestor.attrib}

        # what has to be known about the items before they are taken out of the document
        command_groups = {root: self._command_groups(root) for root in roots}
        moved = roots + [group for root in roots for group in command_groups[root]]
        old_parents = {node: _parent(node) for node in moved}
        transforms = {node: get_node_transform(node) for node in moved}
        styles = {root: root.specified_style() for root in roots}

        # the first item marks the place where the sorted items are inserted
        placeholder = old_parents[roots[0]].makeelement(SVG_GROUP_TAG)
        roots[0].addprevious(placeholder)
        for node in moved:
            old_parents[node].remove(node)

        # Insert them into the group which contains all of them, if nothing which stays in place is stitched before.
        insert_parent = _parent(placeholder)
        while insert_parent is not target and not self._stitched_before(placeholder, staying):
            insert_parent.remove(placeholder)
            insert_parent.addprevious(placeholder)
            insert_parent = _parent(placeholder)
        insert_parent_transform = get_node_transform(insert_parent)

        sorted_nodes = []
        for index in order:
            root = roots[index]
            if old_parents[root] is not insert_parent:
                # keep the appearance of items which are moved out of their group
                for style in INHERITED_STYLES:
                    if style in styles[root] and root.style.get(style) is None:
                        root.style[style] = styles[root][style]
            sorted_nodes.append(root)
            # commands stay with their object
            sorted_nodes.extend(command_groups[root])

        for node in sorted_nodes:
            if old_parents[node] is not insert_parent:
                # keep the position on the canvas
                node.set('transform', str((-insert_parent_transform) @ transforms[node]))

        position = insert_parent.index(placeholder)
        insert_parent[position:position + 1] = sorted_nodes

        for group in old_parents.values():
            self._remove_if_empty(group)

        return bool(lettering - {insert_parent, *insert_parent.iterancestors()})

    def _stitched_before(self, placeholder: BaseElement, staying: Set[BaseElement]) -> bool:
        """Is something which stays in place stitched before the placeholder within its group?"""
        return any(node in staying
                   for sibling in placeholder.itersiblings(preceding=True)
                   for node in sibling.iter())

    def _command_groups(self, node: BaseElement) -> List[BaseElement]:
        """The groups with the command symbols of an object (trim, stop, starting point, ...)"""
        groups: List[BaseElement] = []
        if node.tag == SVG_GROUP_TAG:
            # the commands of the objects in a group are usually in the group
            return groups
        for command in find_commands(node):
            group = command.connector.getparent()
            if group is not None and group.tag == SVG_GROUP_TAG and group not in groups and \
                    all(is_command(child) or is_command_symbol(child) for child in group if isinstance(child.tag, str)):
                groups.append(group)
        return groups

    def _remove_if_empty(self, group: Optional[BaseElement]) -> None:
        while group is not None and group.tag == SVG_GROUP_TAG and len(group) == 0:
            parent = group.getparent()
            if parent is None:
                return
            parent.remove(group)
            group = parent


if __name__ == '__main__':
    ColorSort().run()
