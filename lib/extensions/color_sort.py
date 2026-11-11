# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

from typing import Any, Hashable, List, Optional, Sequence, Set

from inkex import BaseElement, Boolean, Color, errormsg
from shapely.errors import GEOSException

from ..i18n import _
from ..svg import PIXELS_PER_MM, get_node_transform
from ..svg.tags import SVG_GROUP_TAG
from .base import InkstitchExtension

# Style properties a moved object would otherwise lose, when they were set on a group it is taken out of.
INHERITED_STYLES = ('fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin',
                    'fill-rule', 'fill-opacity', 'stroke-opacity')

# Objects closer together than this are treated as stitched on top of each other.
OVERLAP_TOLERANCE_MM = 0.5


def color_sorted_order(colors: Sequence[Hashable], must_follow: Sequence[Set[int]]) -> List[int]:
    """Order objects so that objects of the same color are stitched together.

    colors:      the color of each object, in the current stitching order
    must_follow: for each object the indices of the objects which have to be stitched before it

    Returns the object indices in their new stitching order.
    """
    remaining = [set(blockers) for blockers in must_follow]
    pending = list(range(len(colors)))
    order: List[int] = []
    current_color = None

    while pending:
        ready = [index for index in pending if not remaining[index]]
        if not ready:
            # can only happen if must_follow is circular; keep what is left in its original order
            order.extend(pending)
            break

        same_color = [index for index in ready if colors[index] == current_color]
        chosen = same_color[0] if same_color else ready[0]

        current_color = colors[chosen]
        order.append(chosen)
        pending.remove(chosen)
        for blockers in remaining:
            blockers.discard(chosen)

    return order


def count_color_changes(colors: Sequence[Hashable]) -> int:
    return sum(1 for previous, current in zip(colors, colors[1:]) if previous != current)


class ColorSort(InkstitchExtension):
    '''
    Re-stack objects so that objects of the same color are stitched one after another.
    This reduces the number of thread changes, for example after combining several designs.
    '''

    def __init__(self, *args, **kwargs):
        InkstitchExtension.__init__(self, *args, **kwargs)
        self.arg_parser.add_argument("--notebook")
        self.arg_parser.add_argument("--keep-overlap-order", type=Boolean, default=True, dest="keep_overlap_order")

    def effect(self):
        if not self.get_elements():
            return

        nodes: List[BaseElement] = []
        colors: List[Hashable] = []
        shapes: List[Any] = []
        for element in self.elements:
            # a node with fill and stroke results in more than one element
            if element.node in nodes:
                continue
            nodes.append(element.node)
            colors.append(self._color_key(element))
            shapes.append(self._shape(element))

        if len(nodes) < 2:
            errormsg(_("Please select at least two objects to sort by color."))
            return

        if self.options.keep_overlap_order:
            must_follow = self._overlaps(shapes)
        else:
            must_follow = [set() for _node in nodes]

        order = color_sorted_order(colors, must_follow)

        changes_before = count_color_changes(colors)
        changes_after = count_color_changes([colors[index] for index in order])

        if order != sorted(order):
            self._restack([nodes[index] for index in order])

        errormsg(_("Thread changes before sorting: {before}\nThread changes after sorting: {after}").format(
            before=changes_before, after=changes_after))

    def _color_key(self, element) -> Hashable:
        color = element.color
        try:
            return tuple(Color(str(color)).to_rgb())
        except Exception:
            return str(color)

    def _shape(self, element) -> Optional[Any]:
        try:
            return element.shape.buffer(OVERLAP_TOLERANCE_MM * PIXELS_PER_MM)
        except Exception:
            # Unknown geometry: it will be treated as overlapping everything, so it keeps its place.
            return None

    def _overlaps(self, shapes: List[Any]) -> List[Set[int]]:
        must_follow: List[Set[int]] = [set() for _shape in shapes]
        for later, later_shape in enumerate(shapes):
            for earlier in range(later):
                if self._intersects(shapes[earlier], later_shape):
                    must_follow[later].add(earlier)
        return must_follow

    def _intersects(self, shape1, shape2) -> bool:
        if shape1 is None or shape2 is None:
            return True
        if shape1.is_empty or shape2.is_empty:
            return False
        try:
            return bool(shape1.intersects(shape2))
        except GEOSException:
            return True

    def _restack(self, sorted_nodes: List[BaseElement]) -> None:
        # the first object in the document marks the place where the sorted objects are inserted
        node_set = set(sorted_nodes)
        anchor = next(node for node in self.svg.iter() if node in node_set)
        insert_parent = anchor.getparent()
        insert_parent_transform = get_node_transform(insert_parent)

        placeholder = insert_parent.makeelement(SVG_GROUP_TAG)
        insert_parent.insert(insert_parent.index(anchor), placeholder)

        old_parents = []
        for node in sorted_nodes:
            old_parent = node.getparent()
            if old_parent is not insert_parent:
                # keep position and appearance of objects which are moved out of their group
                node.set('transform', str((-insert_parent_transform) @ get_node_transform(node)))
                specified_style = node.specified_style()
                for style in INHERITED_STYLES:
                    if style in specified_style and node.style.get(style) is None:
                        node.style[style] = specified_style[style]
                old_parents.append(old_parent)
            old_parent.remove(node)

        position = insert_parent.index(placeholder)
        insert_parent[position:position + 1] = sorted_nodes

        for old_parent in old_parents:
            self._remove_if_empty(old_parent)

    def _remove_if_empty(self, group: BaseElement) -> None:
        while group is not None and group.tag == SVG_GROUP_TAG and len(group) == 0:
            parent = group.getparent()
            if parent is None:
                return
            parent.remove(group)
            group = parent


if __name__ == '__main__':
    ColorSort().run()
