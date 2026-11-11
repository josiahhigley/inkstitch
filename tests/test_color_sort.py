from inkex import Group, Rectangle
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib.extensions.color_sort import (ColorSort, color_sorted_order,
                                       count_color_changes)


class ColorSortOrderTest(TestCase):
    def test_groups_colors_without_restrictions(self) -> None:
        colors = ["red", "blue", "red", "green", "blue"]
        order = color_sorted_order(colors, [set() for _color in colors])
        self.assertEqual(order, [0, 2, 1, 4, 3])
        self.assertEqual(count_color_changes([colors[i] for i in order]), 2)

    def test_overlapping_objects_keep_their_order(self) -> None:
        # the second red object lies on top of the blue object
        colors = ["red", "blue", "red"]
        order = color_sorted_order(colors, [set(), set(), {1}])
        self.assertEqual(order, [0, 1, 2])

    def test_sorted_input_is_unchanged(self) -> None:
        colors = ["red", "red", "blue"]
        self.assertEqual(color_sorted_order(colors, [set(), {0}, set()]), [0, 1, 2])


class ColorSortExtensionTest(TestCase):
    def _rect(self, parent, x, color):
        return parent.add(Rectangle(attrib={
            "x": str(x), "y": "0", "width": "10", "height": "10", "style": f"fill:{color}"
        }))

    def _run(self, root, keep_overlap_order=True):
        extension = ColorSort()
        extension.parse_arguments([f"--keep-overlap-order={str(keep_overlap_order).lower()}"])
        extension.document = root.getroottree()
        extension.svg = root
        extension.effect()

    def test_sorts_across_groups(self) -> None:
        root = svg()
        design1 = root.add(Group(attrib={"transform": "translate(100, 0)"}))
        design2 = root.add(Group())
        red1 = self._rect(design1, 0, "red")
        blue1 = self._rect(design1, 50, "blue")
        red2 = self._rect(design2, 0, "red")
        blue2 = self._rect(design2, 50, "blue")

        self._run(root)

        self.assertEqual(list(design1), [red1, red2, blue1, blue2])
        # the emptied group is removed
        self.assertIsNone(design2.getparent())
        # objects stay where they were on the canvas
        self.assertAlmostEqual(red2.bounding_box(design1.transform).left, 0)
        self.assertAlmostEqual(red1.bounding_box(design1.transform).left, 100)

    def test_overlapping_objects_are_not_reordered(self) -> None:
        root = svg()
        red1 = self._rect(root, 0, "red")
        blue = self._rect(root, 5, "blue")
        red2 = self._rect(root, 8, "red")

        self._run(root)
        self.assertEqual(list(root), [red1, blue, red2])

        self._run(root, keep_overlap_order=False)
        self.assertEqual(list(root), [red1, red2, blue])
