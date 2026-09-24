import unittest
import ucd


class Tests(unittest.TestCase):

    def test_simple(self):
        res = ucd.get_ucd(0x0041, "gc")
        self.assertEqual(res, "Lu")


if __name__ == "__main__":
    unittest.main()
