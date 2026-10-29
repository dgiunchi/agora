import unittest

from slugger import slugify


class SlugifyTests(unittest.TestCase):
    def test_lowercase_and_hyphens(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_strips_punctuation(self):
        self.assertEqual(slugify("Hello, World! (2026)"), "hello-world-2026")

    def test_collapses_separators(self):
        self.assertEqual(slugify("  a  --  b__c  "), "a-b-c")

    def test_transliterates_accents(self):
        self.assertEqual(slugify("Cr\u00e8me Br\u00fbl\u00e9e"), "creme-brulee")

    def test_empty(self):
        self.assertEqual(slugify(""), "")


if __name__ == "__main__":
    unittest.main()
