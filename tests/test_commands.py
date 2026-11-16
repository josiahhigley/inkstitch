from inkex import PathElement
from inkex.tester import TestCase
from inkex.tester.svg import svg

from lib.commands import add_commands, is_command_symbol
from lib.elements import Stroke, iterate_nodes, nodes_to_elements
from lib.svg.tags import SVG_USE_TAG, XLINK_HREF

from .utils import element_count


class CommandSymbolTest(TestCase):
    def test_renamed_command_symbol_is_not_embroidered(self) -> None:
        root = svg()
        path = root.add(PathElement(attrib={
            "id": "stroke",
            "d": "M 0,0 100,0",
            "style": "stroke:#000000;stroke-width:1;fill:none"
        }))
        add_commands(Stroke(path), ["trim"])

        # Importing a design into a document which already has the same command symbol makes Inkscape
        # rename the symbol of the imported design and update the references to it.
        root.defs.findone("./*[@id='inkstitch_trim']").set("id", "inkstitch_trim-5")
        use = next(root.iterdescendants(SVG_USE_TAG))
        use.set(XLINK_HREF, "#inkstitch_trim-5")

        self.assertTrue(is_command_symbol(use))

        # the command marker is not an embroidery object, only the path is
        elements = nodes_to_elements(iterate_nodes(root))
        self.assertEqual(len(elements), element_count())
        self.assertEqual(elements[0].node, path)

        # and the command still applies to the path
        self.assertTrue(elements[0].has_command("trim"))
