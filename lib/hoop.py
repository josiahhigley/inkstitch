# Authors: see git history
#
# Copyright (c) 2026 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

from typing import List, Optional, Tuple

import inkex

from .i18n import _
from .stitch_plan import StitchPlan
from .svg import PIXELS_PER_MM, get_node_transform
from .svg.tags import INKSTITCH_HOOP

# Don't warn about stitches which are less than this outside of the hoop.
TOLERANCE_MM = 0.05


class Hoop:
    """The stitch area of an embroidery hoop.

    It is marked in the document with a shape which has the inkstitch:hoop attribute. Its value is the name of the hoop.
    The hoop templates which can be installed with the Install Addons extension contain such a shape.
    """

    def __init__(self, node: inkex.BaseElement):
        self.name = node.get(INKSTITCH_HOOP, "")

        parent = node.getparent()
        assert parent is not None, "This should be part of a tree and therefore have a parent"
        bounding_box = node.bounding_box(inkex.Transform(get_node_transform(parent)))
        assert bounding_box is not None

        # in pixels, like the stitches of a stitch plan
        self.left: float = bounding_box.left
        self.top: float = bounding_box.top
        self.right: float = bounding_box.right
        self.bottom: float = bounding_box.bottom

    @property
    def width_mm(self) -> float:
        return (self.right - self.left) / PIXELS_PER_MM

    @property
    def height_mm(self) -> float:
        return (self.bottom - self.top) / PIXELS_PER_MM

    @property
    def description(self) -> str:
        size = _("stitch area {width} x {height} mm").format(width=_format_mm(self.width_mm), height=_format_mm(self.height_mm))
        if self.name:
            return f"{self.name}, {size}"
        return size


def find_hoop(svg: inkex.SvgDocumentElement) -> Optional[Hoop]:
    """Find the hoop of this document."""
    for node in svg.iterdescendants():
        if INKSTITCH_HOOP in node.attrib:
            try:
                return Hoop(node)
            except (AssertionError, AttributeError, ValueError):
                # this is not a shape
                pass
    return None


def hoop_warning(svg: inkex.SvgDocumentElement, stitch_plan: StitchPlan, rotation: Optional[float] = 0) -> Optional[str]:
    """Find out if a design fits into the hoop of its document.

    rotation: the angle by which the design is rotated when it is exported (None: it isn't)

    Returns a message for the user if the design doesn't fit or if it is outside of the hoop. Otherwise None.
    """
    hoop = find_hoop(svg)
    if hoop is None or not len(stitch_plan):
        return None

    left, top, right, bottom = stitch_plan.bounding_box
    width_mm = (right - left) / PIXELS_PER_MM
    height_mm = (bottom - top) / PIXELS_PER_MM
    rotated = round(rotation or 0) % 180 == 90
    if rotated:
        width_mm, height_mm = height_mm, width_mm

    design = _("Design: {width} x {height} mm").format(width=_format_mm(width_mm), height=_format_mm(height_mm))
    if rotated:
        design += " " + _("(rotated on export)")
    details = "\n\n" + design + "\n" + _("Hoop: {hoop}").format(hoop=hoop.description)

    if width_mm > hoop.width_mm + TOLERANCE_MM or height_mm > hoop.height_mm + TOLERANCE_MM:
        message = _("The design is too large for the hoop.") + details
        if height_mm <= hoop.width_mm + TOLERANCE_MM and width_mm <= hoop.height_mm + TOLERANCE_MM:
            message += "\n\n" + _("It fits when it is rotated by 90 degrees.")
        return message

    if rotated:
        # the place of the design on the canvas tells nothing about a rotated design
        return None

    outside = _outside(hoop, (left, top, right, bottom))
    if outside:
        return _("The design is partly outside of the hoop: {sides}.").format(sides=", ".join(outside)) + details + "\n\n" + \
            _("It is small enough for the hoop. Move it into the hoop, or look for objects which are not meant to be stitched.")

    return None


def _outside(hoop: Hoop, bounding_box: Tuple[float, float, float, float]) -> List[str]:
    left, top, right, bottom = bounding_box
    sides = [
        (hoop.left - left, _("{distance} mm at the left")),
        (right - hoop.right, _("{distance} mm at the right")),
        (hoop.top - top, _("{distance} mm at the top")),
        (bottom - hoop.bottom, _("{distance} mm at the bottom")),
    ]
    return [text.format(distance=_format_mm(distance / PIXELS_PER_MM)) for distance, text in sides
            if distance / PIXELS_PER_MM > TOLERANCE_MM]


def _format_mm(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")
