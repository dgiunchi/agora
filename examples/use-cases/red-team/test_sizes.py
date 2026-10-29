import unittest

from sizes import parse_size


class ParseSizeTests(unittest.TestCase):
    def test_bare_number_is_bytes(self):
        self.assertEqual(parse_size("512"), 512)
        self.assertEqual(parse_size("512B"), 512)

    def test_decimal_units_are_powers_of_1000(self):
        self.assertEqual(parse_size("1KB"), 1_000)
        self.assertEqual(parse_size("10MB"), 10_000_000)
        self.assertEqual(parse_size("2GB"), 2_000_000_000)
        self.assertEqual(parse_size("1TB"), 1_000_000_000_000)

    def test_binary_units_are_powers_of_1024(self):
        self.assertEqual(parse_size("1KiB"), 1_024)
        self.assertEqual(parse_size("1MiB"), 1_048_576)
        self.assertEqual(parse_size("1.5 GiB"), 1_610_612_736)

    def test_case_and_whitespace(self):
        self.assertEqual(parse_size("  2 gb "), 2_000_000_000)
        self.assertEqual(parse_size("3mib"), 3_145_728)

    def test_fractions_round_down(self):
        self.assertEqual(parse_size("1.5KB"), 1_500)
        self.assertEqual(parse_size("0.5B"), 0)
        self.assertEqual(parse_size("1.9999KiB"), 2_047)

    def test_rejects_garbage(self):
        for bad in ("", "   ", "abc", "MB", "10XB", "1.2.3MB", "10 M B", "1,000KB"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_size(bad)

    def test_rejects_negative_and_signs(self):
        for bad in ("-1KB", "-0", "+5MB"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_size(bad)

    def test_rejects_non_string(self):
        for bad in (None, 10, 1.5, b"10MB"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_size(bad)


if __name__ == "__main__":
    unittest.main()
