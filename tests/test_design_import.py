import tempfile
from pathlib import Path

import pystitch
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib.design_import import (embroidery_reader_extensions, import_designs,
                               split_inkscape_paths)
from lib.extensions.design_tray import DesignTray
from lib.extensions.prepare_pe900 import PreparePe900
from lib.svg.tags import SVG_GROUP_TAG


def write_tiny_dst(path: Path, color_rgb=(255, 0, 0)) -> None:
    pattern = pystitch.EmbPattern()
    pattern.add_thread({"rgb": (color_rgb[0] << 16) | (color_rgb[1] << 8) | color_rgb[2]})
    for x, y in ((0, 0), (20, 0), (20, 20), (0, 20), (0, 0)):
        pattern.add_stitch_absolute(pystitch.STITCH, x, y)
    pattern.end()
    pystitch.write(pattern, str(path))


class DesignImportTest(TestCase):
    def test_reader_list_includes_common_machine_formats(self) -> None:
        extensions = embroidery_reader_extensions()
        for ext in ("pes", "dst", "jef", "vp3", "svg"):
            self.assertIn(ext, extensions)
        self.assertNotIn("edr", extensions)
        self.assertNotIn("col", extensions)

    def test_split_inkscape_paths(self) -> None:
        self.assertEqual(split_inkscape_paths("a.pes|b.dst|/tmp/c.jef"), ["a.pes", "b.dst", "/tmp/c.jef"])
        self.assertEqual(split_inkscape_paths(""), [])

    def test_imports_dst_as_named_group(self) -> None:
        root = svg()
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "flower.dst"
            write_tiny_dst(dst)
            placed, skipped = import_designs(root, [str(dst)])
        self.assertEqual(skipped, [])
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed[0].label, "flower.dst")
        self.assertEqual(placed[0].get("inkstitch:design_import"), "flower.dst")
        self.assertTrue(any(True for _ in placed[0].iterdescendants()))

    def test_skips_color_only_files(self) -> None:
        root = svg()
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "palette.edr"
            fake.write_text("not a design")
            placed, skipped = import_designs(root, [str(fake)])
        self.assertEqual(placed, [])
        self.assertEqual(len(skipped), 1)

    def test_design_tray_extension_imports_multiple(self) -> None:
        root = svg()
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.dst"
            b = Path(tmp) / "b.dst"
            write_tiny_dst(a, (255, 0, 0))
            write_tiny_dst(b, (0, 0, 255))
            extension = DesignTray()
            extension.parse_arguments([f"--design-files={a}|{b}", "--center-in-hoop=true"])
            extension.document = root.getroottree()
            extension.svg = root
            extension.effect()
        imports = [node for node in root.iterdescendants(SVG_GROUP_TAG)
                   if node.get("inkstitch:design_import")]
        self.assertEqual(len(imports), 2)


class PreparePe900Test(TestCase):
    def test_writes_pes_and_working_svg(self) -> None:
        root = svg()
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "mono.dst"
            write_tiny_dst(dst)
            placed, _ = import_designs(root, [str(dst)])
            self.assertTrue(placed)

            out = Path(tmp) / "out"
            out.mkdir()
            extension = PreparePe900()
            extension.parse_arguments([f"--output-dir={out}", "--base-name=monogram", "--also-dst=false"])
            extension.document = root.getroottree()
            extension.svg = root
            extension.effect()

            self.assertTrue((out / "monogram.pes").is_file())
            self.assertTrue((out / "monogram.inkstitch.svg").is_file())
            self.assertGreater((out / "monogram.pes").stat().st_size, 0)
