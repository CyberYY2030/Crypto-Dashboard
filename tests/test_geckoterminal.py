import unittest

from collectors import geckoterminal


class GeckoTerminalCollectorTests(unittest.TestCase):
    def test_pool_ref_to_network_and_address(self):
        network, pool = geckoterminal.split_pool_ref("ethereum/0xPool")
        self.assertEqual(network, "eth")
        self.assertEqual(pool, "0xPool")

    def test_extract_daily_volume_context_uses_latest_as_current(self):
        payload = {
            "data": {
                "attributes": {
                    "ohlcv_list": [
                        [1713052800, "1", "2", "0.5", "1.5", "1000"],
                        [1713139200, "1.5", "2.2", "1.4", "2.0", "2000"],
                        [1713225600, "2.0", "2.8", "1.9", "2.5", "5000"],
                    ]
                }
            }
        }
        current_volume, baseline = geckoterminal.extract_daily_volume_context(payload, baseline_days=2)
        self.assertEqual(current_volume, 5000.0)
        self.assertEqual(baseline, [2000.0, 1000.0])


if __name__ == "__main__":
    unittest.main()
