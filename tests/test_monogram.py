from unittest import TestCase

from lib.lettering.monogram import (apply_monogram_to_settings,
                                    compose_monogram_text,
                                    default_monogram_settings,
                                    normalize_initial,
                                    parse_monogram_slots)


class MonogramHelpersTest(TestCase):
    def test_normalize_initial(self):
        self.assertEqual(normalize_initial("  j "), "j")
        self.assertEqual(normalize_initial("ABC"), "A")
        self.assertEqual(normalize_initial(""), "")
        self.assertEqual(normalize_initial(None), "")
        self.assertEqual(normalize_initial("AB", max_chars=2), "AB")

    def test_compose_monogram_text(self):
        self.assertEqual(compose_monogram_text("J", "M", "H"), "JMH")
        self.assertEqual(compose_monogram_text("j", "", "h"), "jh")
        self.assertEqual(compose_monogram_text("", "M", ""), "M")
        self.assertEqual(compose_monogram_text("  A ", " BB ", "C"), "ABC")

    def test_parse_monogram_slots(self):
        self.assertEqual(parse_monogram_slots(""), ("", "", ""))
        self.assertEqual(parse_monogram_slots("M"), ("", "M", ""))
        self.assertEqual(parse_monogram_slots("JH"), ("J", "", "H"))
        self.assertEqual(parse_monogram_slots("JMH"), ("J", "M", "H"))
        self.assertEqual(parse_monogram_slots("JMHX"), ("J", "M", "H"))

    def test_apply_monogram_to_settings(self):
        settings = default_monogram_settings()
        apply_monogram_to_settings(settings, "j", "m", "h", center_enlargement=True)
        self.assertTrue(settings["monogram_mode"])
        self.assertEqual(settings["monogram_left"], "j")
        self.assertEqual(settings["monogram_center"], "m")
        self.assertEqual(settings["monogram_right"], "h")
        self.assertEqual(settings["text"], "jmh")
        self.assertTrue(settings["monogram_center_enlargement"])
        self.assertEqual(settings["text_align"], 1)

    def test_center_enlargement_stub_default_off(self):
        settings = default_monogram_settings()
        self.assertFalse(settings["monogram_center_enlargement"])
