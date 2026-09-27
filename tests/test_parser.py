"""
Unit tests for Kamino account parsers.
"""

import unittest
from kamino_sentinel.parser import KaminoAccountParser


class TestParser(unittest.TestCase):
    def test_empty_account_payload(self):
        res = KaminoAccountParser.parse_reserve_account("test_pubkey", b"short")
        self.assertIsNone(res)

        ob = KaminoAccountParser.parse_obligation_account("test_pubkey", b"short")
        self.assertIsNone(ob)


if __name__ == "__main__":
    unittest.main()
