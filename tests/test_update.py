from contextlib import redirect_stdout
from io import BytesIO, StringIO
from typing import List, Optional
from unittest.mock import patch

import pystitch
from inkex import BaseElement, Layer, PathElement, SvgDocumentElement, load_svg
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib import update
from lib.extensions.input import Input
from lib.svg.tags import (INKSCAPE_GROUPMODE, INKSTITCH_ATTRIBS, SVG_PATH_TAG,
                          SVG_USE_TAG)
from lib.update import (ELEMENT_VERSION, INKSTITCH_SVG_VERSION,
                        update_inkstitch_document)


class UnversionedDocumentTest(TestCase):
    """Ink/Stitch asks if a document without a version shall be updated.  Don't ask about freshly imported embroidery files."""

    def _embroidery_file(self) -> str:
        # two colors, and a trim to get a command symbol
        pattern = pystitch.EmbPattern()
        for index, color in enumerate((0xFF0000, 0x000000)):
            pattern.add_thread({"rgb": color})
            if index > 0:
                pattern.color_change()
            for patch_x in (0, 150):
                for row in range(0, 100, 10):
                    pattern.add_stitch_absolute(pystitch.STITCH, patch_x, row)
                    pattern.add_stitch_absolute(pystitch.STITCH, patch_x + 100, row + 5)
                if patch_x == 0:
                    pattern.trim()
        pattern.end()
        path = self.temp_file(suffix=".pes")
        pystitch.write_pes(pattern, path)
        return path

    def _open(self) -> SvgDocumentElement:
        """Open an embroidery file like Inkscape does: with the input extension."""
        output = StringIO()
        with redirect_stdout(output):
            Input().run([self._embroidery_file()])
        return load_svg(BytesIO(output.getvalue().encode("utf-8"))).getroot()

    def _import(self, document: SvgDocumentElement, ungroup: bool = False) -> List[BaseElement]:
        """Import an embroidery file like Inkscape's File > Import: the metadata of the imported document is left behind."""
        imported = self._open()
        for definition in list(imported.defs):
            document.defs.append(definition)

        imported_layer = imported.findone(".//svg:g[@inkscape:groupmode='layer']")
        assert imported_layer is not None
        paths = [path for path in imported_layer.iterdescendants(SVG_PATH_TAG) if INKSTITCH_ATTRIBS['stroke_method'] in path.attrib]
        layer = document.add(Layer())
        if ungroup:
            # Inkscape does this by itself to a design with a single color block
            for path in paths:
                layer.append(path)
        else:
            imported_layer.attrib.pop(INKSCAPE_GROUPMODE)
            layer.append(imported_layer)
        return paths

    def _old_object(self, document: SvgDocumentElement) -> PathElement:
        """An object with embroidery settings of which nobody knows the version."""
        return document.add(PathElement(attrib={
            "d": "M 0,0 100,0",
            "style": "stroke:#000000;stroke-width:1;fill:none",
            INKSTITCH_ATTRIBS['bean_stitch_repeats']: "1"
        }))

    def _update(self, document: SvgDocumentElement, answer_cancel: Optional[bool] = None) -> int:
        """Do what every extension does when it loads a document.  Returns how often the user has been asked."""
        questions = []

        class Answer:
            def __init__(self) -> None:
                assert answer_cancel is not None, "the user must not be asked"
                questions.append(self)
                self.cancelled = answer_cancel

        with patch.object(update, "RequestUpdate", Answer):
            update_inkstitch_document(document.getroottree())
        return len(questions)

    def _version(self, document: SvgDocumentElement) -> Optional[int]:
        # Looking at the metadata with InkStitchMetadata would add the default settings to the document,
        # and settings without a version are a sign of an old document.
        version = document.findone(".//inkstitch:inkstitch_svg_version")
        return None if version is None else int(version.text)

    def test_opened_embroidery_file(self) -> None:
        document = self._open()
        self.assertEqual(self._version(document), INKSTITCH_SVG_VERSION)

        # the stitches know their version, the command symbols don't need one
        paths = [path for path in document.iterdescendants(SVG_PATH_TAG) if INKSTITCH_ATTRIBS['stroke_method'] in path.attrib]
        self.assertEqual(len(paths), 4)
        for path in paths:
            self.assertEqual(path.get(ELEMENT_VERSION), str(INKSTITCH_SVG_VERSION))
        self.assertEqual(len(list(document.iterdescendants(SVG_USE_TAG))), 2)

        self.assertEqual(self._update(document), 0)

    def test_does_not_ask_about_imported_embroidery_files(self) -> None:
        document: SvgDocumentElement = svg()
        self._import(document)
        self._import(document)
        self.assertIsNone(self._version(document))

        self.assertEqual(self._update(document), 0)
        self.assertEqual(self._version(document), INKSTITCH_SVG_VERSION)

    def test_does_not_ask_when_inkscape_ungroups_the_import(self) -> None:
        document: SvgDocumentElement = svg()
        self._import(document, ungroup=True)

        self.assertEqual(self._update(document), 0)
        self.assertEqual(self._version(document), INKSTITCH_SVG_VERSION)

    def test_imported_embroidery_files_are_not_changed(self) -> None:
        document: SvgDocumentElement = svg()
        paths = self._import(document)
        before = [dict(path.attrib) for path in paths]

        self._update(document)

        self.assertEqual([dict(path.attrib) for path in paths], before)

    def test_new_objects_without_settings_are_not_changed(self) -> None:
        # she imports a design and draws something next to it
        document: SvgDocumentElement = svg()
        self._import(document)
        drawn = document.add(PathElement(attrib={"d": "M 0,0 100,0", "style": "stroke:#000000;stroke-width:1;fill:none"}))
        before = dict(drawn.attrib)

        self.assertEqual(self._update(document), 0)
        self.assertEqual(dict(drawn.attrib), before)

    def test_still_asks_about_other_objects_with_settings(self) -> None:
        document: SvgDocumentElement = svg()
        self._old_object(document)
        self.assertEqual(self._update(document, answer_cancel=True), 1)
        self.assertEqual(self._version(document), INKSTITCH_SVG_VERSION)

        # an imported design next to it doesn't change that
        document = svg()
        self._import(document)
        self._old_object(document)
        self.assertEqual(self._update(document, answer_cancel=True), 1)

    def test_update_leaves_imported_embroidery_files_alone(self) -> None:
        document: SvgDocumentElement = svg()
        paths = self._import(document)
        before = [dict(path.attrib) for path in paths]
        old_object = self._old_object(document)

        self.assertEqual(self._update(document, answer_cancel=False), 1)

        # the old object has been a zigzag stitch when it was made
        self.assertEqual(old_object.get(INKSTITCH_ATTRIBS['stroke_method']), "zigzag_stitch")
        self.assertEqual([dict(path.attrib) for path in paths], before)

    def test_asks_about_imports_of_an_other_version(self) -> None:
        document: SvgDocumentElement = svg()
        for path in self._import(document):
            path.set(ELEMENT_VERSION, str(INKSTITCH_SVG_VERSION - 1))

        self.assertEqual(self._update(document, answer_cancel=True), 1)
