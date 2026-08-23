import unittest
from unittest.mock import patch

from collectors import binance_alpha


class BinanceAlphaCollectorTests(unittest.TestCase):
    def test_extract_day1_open_from_earliest_kline(self):
        payload = [
            [1713052800000, "1.25", "1.40", "1.20", "1.30", "1000"],
            [1713139200000, "1.30", "1.50", "1.28", "1.48", "1200"],
        ]
        self.assertEqual(binance_alpha.extract_day1_open(payload), 1.25)

    def test_build_token_key(self):
        self.assertEqual(
            binance_alpha.make_token_key("base", "0xAbC"),
            "base:0xabc",
        )

    def test_fetch_alpha_day1_open_uses_earliest_kline(self):
        with patch("collectors.binance_alpha.fetch_klines") as fetch_klines:
            fetch_klines.return_value = [
                [1713139200000, "1.30", "1.50", "1.28", "1.48", "1200"],
                [1713052800000, "1.25", "1.40", "1.20", "1.30", "1000"],
            ]
            self.assertEqual(binance_alpha.fetch_alpha_day1_open("ABCUSDT"), 1.25)

    def test_normalize_token_entry_keeps_evm_token_fields(self):
        row = binance_alpha.normalize_token_entry(
            {
                "symbol": "ABC",
                "tokenId": "ALPHA_175",
                "chain": "Base",
                "contractAddress": "0xAbC",
                "name": "Alpha Beta Coin",
            }
        )
        self.assertEqual(
            row,
            {
                "symbol": "ABC",
                "name": "Alpha Beta Coin",
                "chain": "base",
                "contract_address": "0xabc",
                "alpha_symbol": "ALPHA_175USDT",
            },
        )

    def test_listing_reference_open_uses_contract_kline_params_headers_and_first_available_candle(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "code": "000000",
                    "success": True,
                    "data": {"klineInfos": [[1739577600000, "1.5"], [1739491200000, "1.25"]]},
                }

        with patch("collectors.binance_alpha.requests.get", return_value=Response()) as request:
            result = binance_alpha.fetch_listing_reference_open(
                "base", "0xabc", 1739438880000
            )
        self.assertEqual(
            {
                "price": 1.25,
                "open_time_ms": 1739491200000,
                "listing_day_offset_days": 1.0,
                "source": "binance_web3_dex_contract_kline_ai",
            },
            result,
        )
        self.assertEqual(
            {
                "chainId": 8453,
                "contractAddress": "0xabc",
                "interval": "1d",
                "limit": 3,
                "startTime": 1739404800000,
            },
            request.call_args.kwargs["params"],
        )
        self.assertEqual(
            {"Accept-Encoding": "identity", "User-Agent": "binance-web3/1.1 (Skill)"},
            request.call_args.kwargs["headers"],
        )

    def test_listing_reference_open_supports_bsc_and_fails_closed_for_bad_data(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"code": "000000", "success": True, "data": {"klineInfos": [[1776124800000, "2.5"], [1776038400000, "NaN"], "bad"]}}

        with patch("collectors.binance_alpha.requests.get", return_value=Response()) as request:
            result = binance_alpha.fetch_listing_reference_open("bsc", "0xdef", 1776094200000)
        self.assertEqual(2.5, result["price"])
        self.assertEqual(1.0, result["listing_day_offset_days"])
        self.assertEqual(56, request.call_args.kwargs["params"]["chainId"])
        self.assertIsNone(binance_alpha.fetch_listing_reference_open("unknown", "0xdef", 1))

    def test_listing_reference_open_rejects_empty_non_finite_and_malformed_rows(self):
        class Response:
            def __init__(self, rows):
                self.rows = rows

            def raise_for_status(self):
                return None

            def json(self):
                return {"code": "000000", "success": True, "data": {"klineInfos": self.rows}}

        for rows in ([], [[1728000000000, "NaN"]], [[1728000000000, "Infinity"]], ["bad", [1728000000000]]):
            with self.subTest(rows=rows), patch("collectors.binance_alpha.requests.get", return_value=Response(rows)):
                self.assertIsNone(binance_alpha.fetch_listing_reference_open("base", "0xabc", 1728000000000))

        with patch("collectors.binance_alpha.requests.get", return_value=Response("not-a-dict")):
            self.assertIsNone(binance_alpha.fetch_listing_reference_open("base", "0xabc", 1728000000000))

    def test_listing_reference_open_rejects_http_success_with_invalid_top_level_contract(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        valid_rows = {"data": {"klineInfos": [[1728000000000, "1.0"]]}}
        invalid_payloads = (
            {"code": "100001", "success": True, **valid_rows},
            {"code": "000000", "success": False, **valid_rows},
            {"code": "000000", **valid_rows},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), patch(
                "collectors.binance_alpha.requests.get", return_value=Response(payload)
            ):
                self.assertIsNone(binance_alpha.fetch_listing_reference_open("base", "0xabc", 1728000000000))


if __name__ == "__main__":
    unittest.main()
