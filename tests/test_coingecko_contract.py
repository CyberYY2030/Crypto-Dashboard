import unittest

from collectors import coingecko


class CoinGeckoContractTests(unittest.TestCase):
    def test_extract_contract_coin_context(self):
        payload = {
            "market_data": {
                "ath": {"usd": 12.5},
                "market_cap": {"usd": 88_000_000},
            }
        }
        ctx = coingecko.extract_contract_coin_context(payload)
        self.assertEqual(ctx["ath_price"], 12.5)
        self.assertEqual(ctx["market_cap_usd"], 88_000_000.0)
        self.assertEqual(ctx["market_cap_confidence"], "verified")

    def test_extract_contract_market_context(self):
        payload = {
            "prices": [
                [1711843200000, 1.0],
                [1711929600000, 1.5],
                [1712016000000, 0.9],
            ],
            "total_volumes": [
                [1711843200000, 1000000],
                [1711929600000, 2000000],
                [1712016000000, 4000000],
            ],
        }
        ctx = coingecko.extract_contract_market_context(payload, baseline_days=2)
        self.assertEqual(ctx["ath_price"], 1.5)
        self.assertEqual(ctx["current_volume"], 4000000.0)
        self.assertEqual(ctx["baseline_volumes"], [2000000.0, 1000000.0])


if __name__ == "__main__":
    unittest.main()
