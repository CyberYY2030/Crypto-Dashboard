import unittest
from unittest.mock import patch

import requests

from collectors import dexscreener


class DexScreenerTests(unittest.TestCase):
    def test_get_token_pairs_batch_falls_back_to_single_fetch_when_batch_request_fails(self):
        pair_a = {
            "baseToken": {"address": "0xaaa"},
            "quoteToken": {"address": "0xusdt"},
            "pairAddress": "0xpaira",
        }
        pair_b = {
            "baseToken": {"address": "0xbbb"},
            "quoteToken": {"address": "0xusdt"},
            "pairAddress": "0xpairb",
        }

        with patch("collectors.dexscreener.requests.get") as requests_get, patch(
            "collectors.dexscreener.get_token_pairs"
        ) as get_token_pairs:
            requests_get.side_effect = requests.exceptions.SSLError("EOF occurred in violation of protocol")
            get_token_pairs.side_effect = [[pair_a], [pair_b]]

            grouped = dexscreener.get_token_pairs_batch("base", ["0xaaa", "0xbbb"], batch_size=2)

        self.assertEqual(grouped["0xaaa"], [pair_a])
        self.assertEqual(grouped["0xbbb"], [pair_b])


if __name__ == "__main__":
    unittest.main()
