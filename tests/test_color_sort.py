import os
import tempfile
from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
from typing import Hashable, List, Optional, Sequence, Set, Tuple, Union

import pystitch
from inkex import (BaseElement, BoundingBox, Group, Layer, Rectangle,
                   SvgDocumentElement, Transform)
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib.commands import add_commands
from lib.elements import FillStitch, iterate_nodes, nodes_to_elements
from lib.extensions.color_sort import (ColorSort, color_sorted_order,
                                       count_color_changes)
from lib.stitch_plan import (StitchPlan, generate_stitch_plan,
                             stitch_groups_to_stitch_plan)
from lib.svg.tags import (CONNECTION_END, CONNECTION_START,
                          INKSCAPE_GROUPMODE, INKSTITCH_ATTRIBS,
                          INKSTITCH_LETTERING, SVG_DEFS_TAG, SVG_METADATA_TAG,
                          SVG_RECT_TAG, SVG_USE_TAG, XLINK_HREF)

Color = Union[str, Tuple[str, str]]


def sort(colors: Sequence[Color], must_follow: Optional[List[Set[int]]] = None,
         previous_color: Optional[Hashable] = None, next_color: Optional[Hashable] = None, **kwargs) -> List[int]:
    """colors: the color of each item, or a tuple with its first and its last color"""
    pairs = [color if isinstance(color, tuple) else (color, color) for color in colors]
    if must_follow is None:
        must_follow = [set() for _color in colors]
    return color_sorted_order(pairs, must_follow, previous_color, next_color, **kwargs)


def thread_changes(colors: Sequence[Color], order: List[int]) -> int:
    threads: List[str] = []
    for index in order:
        color = colors[index]
        threads.extend(color if isinstance(color, tuple) else [color])
    return count_color_changes(threads)


class ColorSortOrderTest(TestCase):
    # two designs which have been stitched one after the other, each object lies on top of the previous one of its design
    TWO_DESIGNS = ["green", "red", "yellow", "black", "black", "red", "yellow", "black"]
    TWO_DESIGNS_MUST_FOLLOW: List[Set[int]] = [set(), {0}, {1}, {2}, set(), {4}, {5}, {6}]

    def test_groups_colors_without_restrictions(self) -> None:
        colors = ["red", "blue", "red", "green", "blue"]
        order = sort(colors)
        self.assertEqual(order, [0, 2, 1, 4, 3])
        self.assertEqual(thread_changes(colors, order), 2)

    def test_overlapping_objects_keep_their_order(self) -> None:
        # The second red object lies on top of the blue object.  Both reds can still be stitched together: after the blue object.
        self.assertEqual(sort(["red", "blue", "red"], [set(), set(), {1}]), [1, 0, 2])
        # the blue object lies on top of the first red object as well
        self.assertEqual(sort(["red", "blue", "red"], [set(), {0}, {1}]), [0, 1, 2])

    def test_sorted_input_is_unchanged(self) -> None:
        self.assertEqual(sort(["red", "red", "blue"], [set(), {0}, set()]), [0, 1, 2])

    def test_finds_the_fewest_thread_changes(self) -> None:
        # Taking the first object which is possible would start with green, red, yellow and never get below 6 thread changes.
        # The black base of the second design has to be stitched early to bring the reds and the yellows together.
        order = sort(self.TWO_DESIGNS, self.TWO_DESIGNS_MUST_FOLLOW)
        self.assertEqual(thread_changes(self.TWO_DESIGNS, list(range(8))), 6)
        self.assertEqual(order, [0, 4, 1, 5, 2, 6, 3, 7])
        self.assertEqual(thread_changes(self.TWO_DESIGNS, order), 4)

    def test_objects_with_two_colors(self) -> None:
        # the first object has a red fill and a black outline
        colors: List[Color] = [("red", "black"), "black", "red"]
        self.assertEqual(thread_changes(colors, [0, 1, 2]), 2)
        order = sort(colors)
        self.assertEqual(order, [2, 0, 1])
        self.assertEqual(thread_changes(colors, order), 1)

    def test_considers_what_is_stitched_before_and_afterwards(self) -> None:
        self.assertEqual(sort(["red", "blue"]), [0, 1])
        self.assertEqual(sort(["red", "blue"], previous_color="blue"), [1, 0])
        self.assertEqual(sort(["red", "blue"], next_color="red"), [1, 0])

    def test_order_is_valid_when_the_search_is_cut_short(self) -> None:
        order = sort(self.TWO_DESIGNS, self.TWO_DESIGNS_MUST_FOLLOW, max_states=0)
        self.assertEqual(sorted(order), list(range(8)))
        for index, blockers in enumerate(self.TWO_DESIGNS_MUST_FOLLOW):
            for blocker in blockers:
                self.assertLess(order.index(blocker), order.index(index))

    def test_circular_restrictions_do_not_lose_objects(self) -> None:
        self.assertEqual(sort(["red", "blue"], [{1}, {0}]), [0, 1])


def objects(parent: BaseElement) -> List[BaseElement]:
    """The objects in a group or document, without the document's metadata and definitions"""
    return [child for child in parent if child.tag not in (SVG_DEFS_TAG, SVG_METADATA_TAG)]


def bounding_box(node: BaseElement, parent_transform: Optional[Transform] = None) -> BoundingBox:
    box = node.bounding_box(parent_transform)
    assert box is not None
    return box


def first_layer(document: BaseElement) -> BaseElement:
    layer = document.findone(".//svg:g[@inkscape:groupmode='layer']")
    assert layer is not None
    return layer


class ColorSortExtensionTest(TestCase):
    def _rect(self, parent: BaseElement, x: int, color: str, stroke: Optional[str] = None) -> Rectangle:
        style = f"fill:{color}"
        if stroke:
            style += f";stroke:{stroke};stroke-width:1"
        return parent.add(Rectangle(attrib={
            "x": str(x), "y": "0", "width": "10", "height": "10", "style": style
        }))

    def _run(self, root, keep_overlap_order: bool = True, add_trims: bool = True, selection_only: bool = False) -> str:
        """Sort the design.  Returns the message for the user."""
        extension = ColorSort()
        extension.parse_arguments([
            f"--keep-overlap-order={str(keep_overlap_order).lower()}",
            f"--add-trims={str(add_trims).lower()}",
            f"--selection-only={str(selection_only).lower()}"
        ])
        extension.document = root.getroottree()
        extension.svg = root
        message = StringIO()
        with redirect_stderr(message):
            extension.effect()
        return message.getvalue()

    def test_sorts_across_groups(self) -> None:
        root = svg()
        design1 = root.add(Group(attrib={"transform": "translate(100, 0)"}))
        design2 = root.add(Group())
        red1 = self._rect(design1, 0, "red")
        blue1 = self._rect(design1, 50, "blue")
        red2 = self._rect(design2, 0, "red")
        blue2 = self._rect(design2, 50, "blue")

        message = self._run(root)

        self.assertIn("Thread changes before sorting: 3", message)
        self.assertIn("Thread changes after sorting: 1", message)

        # the objects are in the innermost group which contained all of them, the emptied groups are removed
        self.assertEqual(objects(root), [red1, red2, blue1, blue2])
        self.assertIsNone(design1.getparent())
        self.assertIsNone(design2.getparent())
        # objects stay where they were on the canvas
        self.assertAlmostEqual(bounding_box(red1).left, 100)
        self.assertAlmostEqual(bounding_box(blue1).left, 150)
        self.assertAlmostEqual(bounding_box(red2).left, 0)

    def test_overlapping_objects_are_not_reordered(self) -> None:
        root = svg()
        red1 = self._rect(root, 0, "red")
        blue = self._rect(root, 5, "blue")
        red2 = self._rect(root, 8, "red")

        self._run(root)
        self.assertEqual(objects(root), [red1, blue, red2])

        self._run(root, keep_overlap_order=False)
        self.assertEqual(objects(root), [red1, red2, blue])

    def test_groups_of_one_color_stay_together(self) -> None:
        root = svg()
        layer = root.add(Layer())
        design1 = layer.add(Group())
        design2 = layer.add(Group(attrib={"transform": "translate(0, 100)"}))
        green1 = design1.add(Group())
        self._rect(green1, 0, "green")
        black1 = design1.add(Group())
        self._rect(black1, 20, "black")
        black2 = design2.add(Group())
        black2_objects = [self._rect(black2, 0, "black"), self._rect(black2, 20, "black")]
        green2 = design2.add(Group())
        green2_object = self._rect(green2, 40, "green")

        self._run(root)

        self.assertEqual(list(layer), [green1, green2, black1, black2])
        self.assertEqual(list(black2), black2_objects)
        self.assertAlmostEqual(bounding_box(green2_object, green2.transform).top, 100)

    def test_layers_are_not_moved(self) -> None:
        root = svg()
        layer1 = root.add(Layer())
        layer2 = root.add(Layer())
        red1 = self._rect(layer1, 0, "red")
        blue = self._rect(layer1, 50, "blue")
        red2 = self._rect(layer2, 100, "red")
        green = self._rect(layer2, 150, "green")

        self._run(root)

        # the objects of the second layer are moved to the layer of the first object
        self.assertEqual(objects(root), [layer1])
        self.assertEqual(list(layer1), [red1, red2, blue, green])

    def test_commands_stay_with_their_object(self) -> None:
        root = svg()
        layer = root.add(Layer())
        red1 = self._rect(layer, 0, "red")
        group = layer.add(Group(attrib={"transform": "translate(0, 50)"}))
        blue = self._rect(group, 50, "blue")
        red2 = self._rect(group, 100, "red")
        add_commands(FillStitch(red2), ["trim"])
        command_group = red2.getnext()
        assert command_group is not None
        symbol = next(command_group.iterdescendants(SVG_USE_TAG))
        symbol_position = bounding_box(symbol, command_group.composed_transform()).center

        self._run(root)

        self.assertEqual(list(layer), [red1, red2, command_group, blue])
        self.assertTrue(FillStitch(red2).has_command("trim"))
        new_symbol_position = bounding_box(symbol, command_group.composed_transform()).center
        self.assertAlmostEqual(new_symbol_position.x, symbol_position.x)
        self.assertAlmostEqual(new_symbol_position.y, symbol_position.y)

    def test_objects_which_are_not_selected_stay_in_place(self) -> None:
        root = svg()
        red1 = self._rect(root, 0, "red")
        blue = self._rect(root, 30, "blue")
        # lies on top of the blue object
        red2 = self._rect(root, 35, "red")
        green = self._rect(root, 60, "green")
        red3 = self._rect(root, 90, "red")
        root.selection.set(red1, red2, green, red3)

        message = self._run(root, selection_only=True)

        # The last red object is stitched earlier now.  The one on top of the blue object can't, because the blue object stays.
        self.assertEqual(objects(root), [red1, red3, green, blue, red2])
        self.assertIn("Only the selected objects have been sorted.", message)

    def _two_designs_with_a_selection(self) -> SvgDocumentElement:
        # Inkscape selects a design which has just been imported: here the second one.
        root: SvgDocumentElement = svg()
        design1 = root.add(Group())
        design2 = root.add(Group(attrib={"transform": "translate(0, 100)"}))
        self._rect(design1, 0, "black")
        self._rect(design1, 50, "red")
        self._rect(design2, 0, "green")
        # lies on top of the green object
        self._rect(design2, 5, "red")
        root.selection.set(design2)
        return root

    def test_sorts_the_whole_design_whatever_is_selected(self) -> None:
        root = self._two_designs_with_a_selection()
        black, red1, green, red2 = list(root.iterdescendants(SVG_RECT_TAG))

        message = self._run(root)

        self.assertIn("Thread changes before sorting: 3", message)
        self.assertIn("Thread changes after sorting: 2", message)
        self.assertNotIn("selected", message)
        self.assertEqual(objects(root), [black, green, red1, red2])

    def test_sorting_the_selected_design_alone_saves_nothing(self) -> None:
        root = self._two_designs_with_a_selection()
        before = deepcopy(root)

        message = self._run(root, selection_only=True)

        self.assertIn("Thread changes before sorting: 3", message)
        self.assertIn("Thread changes after sorting: 3", message)
        self.assertEqual(root.tostring(), before.tostring())

    def test_trims_between_distant_objects_of_the_same_color(self) -> None:
        trim_after = INKSTITCH_ATTRIBS['trim_after']

        def sorted_design(distance: int, add_trims: bool) -> List[Rectangle]:
            root = svg()
            rects = [self._rect(root, 0, "red"), self._rect(root, 50, "blue"), self._rect(root, 10 + distance, "red")]
            self._run(root, add_trims=add_trims)
            self.assertEqual(objects(root), [rects[0], rects[2], rects[1]])
            return rects

        # 20 px are more than 5 mm: without a trim the thread would be dragged along
        self.assertEqual(sorted_design(20, True)[0].get(trim_after), "true")
        self.assertIsNone(sorted_design(20, False)[0].get(trim_after))
        # 4 px are about 1 mm: the objects are connected with a normal stitch
        self.assertIsNone(sorted_design(4, True)[0].get(trim_after))

    def test_lettering_in_one_color_stays_in_one_piece(self) -> None:
        root = svg()
        layer = root.add(Layer())
        lettering = layer.add(Group(attrib={INKSTITCH_LETTERING: '{"text": "JH"}'}))
        glyphs = [lettering.add(Group()), lettering.add(Group())]
        self._rect(glyphs[0], 0, "black")
        self._rect(glyphs[1], 20, "black")
        red = self._rect(layer, 100, "red")
        black = self._rect(layer, 150, "black")

        message = self._run(root)

        self.assertEqual(list(layer), [lettering, black, red])
        self.assertEqual(list(lettering), glyphs)
        self.assertNotIn("Lettering", message)

    def test_lettering_with_several_colors_is_split_up(self) -> None:
        root = svg()
        layer = root.add(Layer())
        lettering = layer.add(Group(attrib={INKSTITCH_LETTERING: '{"text": "JH"}'}))
        rects = []
        for x in (0, 30):
            glyph = lettering.add(Group())
            # each letter has a black outline on top of a purple fill
            rects += [self._rect(glyph, x, "purple"), self._rect(glyph, x + 2, "black")]
        black = self._rect(layer, 100, "black")

        message = self._run(root)

        self.assertEqual(list(layer), [rects[0], rects[2], rects[1], rects[3], black])
        self.assertIn("Lettering with several colors has been split up", message)

    def test_object_with_fill_and_stroke(self) -> None:
        root = svg()
        red_and_black = self._rect(root, 0, "red", stroke="black")
        black = self._rect(root, 50, "black")
        red = self._rect(root, 100, "red")

        self._run(root)

        self.assertEqual(objects(root), [red, red_and_black, black])

    def test_design_is_left_alone_if_sorting_does_not_help(self) -> None:
        root = svg()
        group = root.add(Group())
        rects = [self._rect(group, 0, "red"), self._rect(group, 50, "blue"), self._rect(root, 100, "green")]
        before = deepcopy(root)

        self._run(root)

        self.assertEqual(list(group), rects[:2])
        self.assertEqual(root.tostring(), before.tostring())


class ColorSortImportedDesignsTest(TestCase):
    """Combine two designs which have been imported from embroidery files, like Inkscape's File > Import does."""

    # each color block is stitched on top of the previous one, the second design starts and ends with black
    DESIGN1 = [0x008000, 0xFF0000, 0xFFFF00, 0x000000]
    DESIGN2 = [0x000000, 0xFF0000, 0xFFFF00, 0x000000]

    def _import(self, directory: str, name: str, colors: List[int]) -> SvgDocumentElement:
        pattern = pystitch.EmbPattern()
        for index, color in enumerate(colors):
            pattern.add_thread({"rgb": color})
            if index > 0:
                pattern.color_change()
            # two patches of zigzag stitches with a trim in between
            for patch in (0, 150):
                for row in range(0, 100, 10):
                    pattern.add_stitch_absolute(pystitch.STITCH, patch, row)
                    pattern.add_stitch_absolute(pystitch.STITCH, patch + 100, row + 5)
                if patch == 0:
                    pattern.trim()
        pattern.end()
        path = os.path.join(directory, name)
        pystitch.write_pes(pattern, path)
        return generate_stitch_plan(path)

    def _combine(self, document: SvgDocumentElement, imported: SvgDocumentElement, x: int) -> None:
        # Inkscape renames the ids of the imported document which are in use already and updates the references to them
        ids = {node.get("id") for node in document.iter()}
        renamed = {}
        for number, node in enumerate(imported.iter()):
            if node.get("id") is not None and node.get("id") in ids:
                renamed["#" + node.get("id")] = f"#{node.get('id')}-{number}"
                node.set("id", f"{node.get('id')}-{number}")
        for node in imported.iter():
            for attribute in (XLINK_HREF, CONNECTION_START, CONNECTION_END):
                if node.get(attribute) in renamed:
                    node.set(attribute, renamed[node.get(attribute)])

        # the layer of the imported document becomes a group in the current layer
        imported_layer = first_layer(imported)
        imported_layer.attrib.pop(INKSCAPE_GROUPMODE)
        imported_layer.set("transform", f"translate({x}, 0)")
        for definition in list(imported.defs):
            document.defs.append(definition)
        first_layer(document).append(imported_layer)

    def _stitch_plan(self, extension: ColorSort) -> StitchPlan:
        elements = nodes_to_elements(iterate_nodes(extension.svg))
        return stitch_groups_to_stitch_plan(extension.elements_to_stitch_groups(elements), collapse_len=3.0)

    def _stitch_positions(self, stitch_plan: StitchPlan) -> List[Tuple[float, float]]:
        return sorted((round(stitch.x, 3), round(stitch.y, 3)) for color_block in stitch_plan for stitch in color_block
                      if not stitch.jump and not stitch.trim and not stitch.color_change)

    def test_two_imported_designs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._import(directory, "design1.pes", self.DESIGN1)
            self._combine(document, self._import(directory, "design2.pes", self.DESIGN2), 400)

        extension = ColorSort()
        extension.parse_arguments([])
        extension.document = document.getroottree()
        extension.svg = document

        layer = first_layer(document)
        color_blocks1 = list(layer)[:4]
        color_blocks2 = list(layer[4])
        before = self._stitch_plan(extension)
        self.assertEqual(len(before), 7)

        extension.effect()

        # the color blocks of the imported designs are still groups, in their new order
        self.assertEqual(list(layer), [color_blocks1[0], color_blocks2[0], color_blocks1[1], color_blocks2[1],
                                       color_blocks1[2], color_blocks2[2], color_blocks1[3], color_blocks2[3]])

        # 4 thread changes instead of 6, and the same stitches as before
        after = self._stitch_plan(extension)
        self.assertEqual(len(after), 5)
        self.assertEqual(self._stitch_positions(after), self._stitch_positions(before))

        # The thread is cut where it would travel from one design to the other: between the reds, the yellows and the blacks.
        self.assertEqual(after.num_trims, before.num_trims + 3)
