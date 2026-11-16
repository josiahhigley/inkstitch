import os
from contextlib import redirect_stderr
from glob import glob
from io import StringIO
from typing import Optional

from inkex import Layer, Rectangle, SvgDocumentElement, load_svg
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib import output
from lib.elements import iterate_nodes, nodes_to_elements
from lib.extensions.install import Install
from lib.hoop import find_hoop, hoop_warning
from lib.metadata import InkStitchMetadata
from lib.stitch_plan import Stitch, StitchPlan
from lib.svg import PIXELS_PER_MM
from lib.svg.tags import INKSTITCH_HOOP
from lib.threads import ThreadColor
from lib.update import INKSTITCH_SVG_VERSION

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons", "templates")
PE900_TEMPLATE = os.path.join(TEMPLATES, "inkstitch-hoop-brother-pe900-5x7.svg")


def load_template() -> SvgDocumentElement:
    with open(PE900_TEMPLATE, "rb") as template:
        return load_svg(template).getroot()


def design(left_mm: float, top_mm: float, right_mm: float, bottom_mm: float) -> StitchPlan:
    """A stitch plan which covers this area of the canvas."""
    stitch_plan = StitchPlan()
    color_block = stitch_plan.new_color_block(ThreadColor("black"))
    for x, y in ((left_mm, top_mm), (right_mm, top_mm), (right_mm, bottom_mm), (left_mm, bottom_mm)):
        color_block.add_stitch(Stitch(x * PIXELS_PER_MM, y * PIXELS_PER_MM))
    return stitch_plan


class HoopTemplateTest(TestCase):
    def test_all_templates_have_a_hoop(self) -> None:
        templates = glob(os.path.join(TEMPLATES, "*.svg"))
        self.assertIn(PE900_TEMPLATE, templates)
        for path in templates:
            with open(path, "rb") as template:
                self.assertIsNotNone(find_hoop(load_svg(template).getroot()), path)

    def test_brother_pe900_stitch_area(self) -> None:
        hoop = find_hoop(load_template())
        assert hoop is not None
        self.assertEqual(hoop.name, "Brother PE900 5x7")
        self.assertAlmostEqual(hoop.width_mm, 130, places=3)
        self.assertAlmostEqual(hoop.height_mm, 180, places=3)
        self.assertEqual(hoop.description, "Brother PE900 5x7, stitch area 130 x 180 mm")

    def test_hoop_is_not_stitched(self) -> None:
        root = load_template()
        self.assertEqual(nodes_to_elements(iterate_nodes(root)), [])

    def test_new_objects_are_stitched(self) -> None:
        root = load_template()
        layer = root.getElementById(root.namedview.get("inkscape:current-layer"))
        self.assertEqual(layer.label, "Design")
        layer.add(Rectangle(attrib={"x": "10", "y": "10", "width": "20", "height": "20"}))
        self.assertEqual(len(iterate_nodes(root)), 1)

    def test_template_does_not_ask_for_an_update(self) -> None:
        # Otherwise every new document would start with the question whether it should be updated.
        self.assertEqual(InkStitchMetadata(load_template())["inkstitch_svg_version"], INKSTITCH_SVG_VERSION)

    def test_template_survives_remove_commands(self) -> None:
        # Remove Embroidery Settings deletes everything with an id which starts with "inkstitch_".
        for node in load_template().iter():
            self.assertFalse((node.get("id") or "").startswith("inkstitch_"))


class HoopWarningTest(TestCase):
    def _warning(self, stitch_plan: StitchPlan, rotation: Optional[float] = 0) -> Optional[str]:
        return hoop_warning(load_template(), stitch_plan, rotation)

    def test_design_in_the_hoop(self) -> None:
        self.assertIsNone(self._warning(design(10, 10, 120, 170)))
        # the whole stitch area can be used
        self.assertIsNone(self._warning(design(0, 0, 130, 180)))

    def test_design_is_too_large(self) -> None:
        warning = self._warning(design(0, 0, 190, 100))
        assert warning is not None
        self.assertIn("too large", warning)
        self.assertIn("Design: 190 x 100 mm", warning)
        self.assertIn("Hoop: Brother PE900 5x7, stitch area 130 x 180 mm", warning)
        self.assertNotIn("rotated", warning)

        warning = self._warning(design(0, 0, 100, 180.5))
        assert warning is not None
        self.assertIn("Design: 100 x 180.5 mm", warning)

    def test_design_fits_when_it_is_rotated(self) -> None:
        warning = self._warning(design(0, 0, 170, 120))
        assert warning is not None
        self.assertIn("too large", warning)
        self.assertIn("It fits when it is rotated by 90 degrees.", warning)

    def test_document_without_rotation_setting(self) -> None:
        # this is what the output extension passes on for a document which doesn't have the setting
        self.assertIsNone(self._warning(design(10, 10, 120, 170), rotation=None))
        self.assertIsNotNone(self._warning(design(10, 10, 150, 170), rotation=None))

    def test_design_is_rotated_on_export(self) -> None:
        self.assertIsNone(self._warning(design(0, 0, 170, 120), rotation=90))
        self.assertIsNone(self._warning(design(0, 0, 170, 120), rotation=270))

        warning = self._warning(design(0, 0, 120, 170), rotation=90)
        assert warning is not None
        self.assertIn("too large", warning)
        self.assertIn("Design: 170 x 120 mm (rotated on export)", warning)

    def test_small_design_outside_of_the_hoop(self) -> None:
        warning = self._warning(design(120, 170, 142.5, 184))
        assert warning is not None
        self.assertIn("partly outside of the hoop: 12.5 mm at the right, 4 mm at the bottom.", warning)
        self.assertIn("small enough", warning)

        warning = self._warning(design(-200, -3, -180, 20))
        assert warning is not None
        self.assertIn("200 mm at the left, 3 mm at the top", warning)

    def test_document_without_hoop(self) -> None:
        self.assertIsNone(hoop_warning(svg(), design(0, 0, 500, 500)))

    def test_empty_design(self) -> None:
        self.assertIsNone(self._warning(StitchPlan()))

    def test_hoop_in_other_documents(self) -> None:
        # a document which uses pixels, with the hoop in a layer which has been moved
        root: SvgDocumentElement = svg()
        layer = root.add(Layer(attrib={"transform": f"translate({10 * PIXELS_PER_MM}, {20 * PIXELS_PER_MM})"}))
        layer.add(Rectangle(attrib={
            "width": str(100 * PIXELS_PER_MM), "height": str(100 * PIXELS_PER_MM), INKSTITCH_HOOP: ""
        }))

        hoop = find_hoop(root)
        assert hoop is not None
        self.assertEqual(hoop.description, "stitch area 100 x 100 mm")
        self.assertIsNone(hoop_warning(root, design(10, 20, 110, 120)))
        warning = hoop_warning(root, design(5, 20, 105, 120))
        assert warning is not None
        self.assertIn("5 mm at the left", warning)


class HoopOutputTest(TestCase):
    def _write(self, root: SvgDocumentElement, stitch_plan: StitchPlan) -> str:
        """Write an embroidery file.  Returns the messages for the user."""
        messages = StringIO()
        with redirect_stderr(messages):
            output.write_embroidery_file(self.temp_file(suffix=".pes"), stitch_plan, root, settings={"rotate": None})
        return messages.getvalue()

    def setUp(self) -> None:
        super().setUp()
        output._warned_about_hoop = False

    def tearDown(self) -> None:
        output._warned_about_hoop = False
        super().tearDown()

    def test_warns_once_when_embroidery_files_are_written(self) -> None:
        root = load_template()
        too_large = design(0, 0, 140, 100)

        messages = self._write(root, too_large)
        self.assertIn("The design is too large for the hoop.", messages)
        self.assertIn("The embroidery file has been saved nevertheless.", messages)

        # an export of several file formats warns only once
        self.assertEqual(self._write(root, too_large), "")

    def test_no_warning_when_the_design_fits(self) -> None:
        self.assertEqual(self._write(load_template(), design(10, 10, 120, 170)), "")


class InstallTemplatesTest(TestCase):
    def test_install_templates(self) -> None:
        extension = Install()
        extension.inkscape_config_path = self.tempdir
        self.assertTrue(extension.install_templates())
        self.assertTrue(os.path.isfile(os.path.join(self.tempdir, "templates", os.path.basename(PE900_TEMPLATE))))
