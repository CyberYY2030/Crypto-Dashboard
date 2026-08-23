import datetime as dt
import unittest
from unittest.mock import patch

import alpha_logic
import alpha_pipeline
import db as dbm
from collectors import dexscreener, geckoterminal
import yaml


def pair(pair_id, quote="USDT", liquidity=1000, volume=100, price="2", cap=10000):
    return {"pairAddress": pair_id, "baseToken": {"address": "0xtoken", "symbol": "ABC"}, "quoteToken": {"address": "0xquote", "symbol": quote}, "liquidity": {"usd": liquidity}, "volume": {"h24": volume}, "priceUsd": price, "marketCap": cap}


def candle(day, high, low, close, volume):
    return [int(dt.datetime(2026, 8, day, tzinfo=dt.timezone.utc).timestamp()), close, high, low, close, volume]


class AlphaDataContractTests(unittest.TestCase):
    def test_stable_quote_is_preferred_deterministically(self):
        selected = dexscreener.choose_primary_pool([pair("z", "ETH", 2000, 500), pair("a", "USDT", 1000, 100)], "0xtoken", ["USDT"])
        self.assertEqual("a", selected["pairAddress"])

    def test_invalid_pool_is_rejected(self):
        self.assertIsNone(dexscreener.choose_primary_pool([pair("", "USDT"), pair("x", "USDT", 0, 1)], "0xtoken"))

    def test_existing_pool_does_not_rotate(self):
        conn = dbm.connect(":memory:")
        dbm.upsert_alpha_universe(conn, {"token_key": "base:0xtoken", "symbol": "ABC", "name": "ABC", "chain": "base", "contract_address": "0xtoken", "alpha_symbol": "ABCUSDT", "futures_symbol": "ABCUSDT", "primary_pool_id": "base/old", "is_active": 1})
        conn.commit()
        raw = {"symbol": "ABC", "tokenId": "ABC", "chain": "base", "contractAddress": "0xtoken", "name": "ABC"}
        with patch("alpha_pipeline.binance_alpha.fetch_token_list", return_value=[raw]), patch("alpha_pipeline.binance_derivatives.list_futures_symbols", return_value={"ABCUSDT"}), patch("alpha_pipeline.dexscreener.get_token_pairs_batch", return_value={"0xtoken": [pair("new", "USDT")] }):
            alpha_pipeline.sync_alpha_universe(conn, {"alpha": {"stable_quote_symbols": ["USDT"]}})
        self.assertEqual("base/old", dbm.fetch_alpha_universe(conn)[0]["primary_pool_id"])
        conn.close()

    def test_current_utc_day_is_excluded(self):
        payload = {"data": {"attributes": {"ohlcv_list": [candle(19, 3, 1, 2, 10), candle(20, 4, 2, 3, 99)]}}}
        features = geckoterminal.compute_completed_daily_features(payload, 1, 1, 1, dt.datetime(2026, 8, 20, 12, tzinfo=dt.timezone.utc))
        self.assertEqual(1, features["completed_candle_count"])
        self.assertEqual(10.0, features["baseline_volume_median"])

    def test_median_baseline_and_breakout_high(self):
        rows = [candle(day, day + 2, 1, 2, volume) for day, volume in zip(range(1, 5), [10, 100, 20, 30])]
        features = geckoterminal.compute_completed_daily_features({"data": {"attributes": {"ohlcv_list": rows}}}, 3, 3, 1, dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc))
        self.assertEqual(30.0, features["baseline_volume_median"])
        self.assertEqual(6.0, features["previous_high"])

    def test_compression_formula(self):
        rows = [candle(1, 3, 1, 2, 1), candle(2, 3, 1, 2, 1), candle(3, 2.2, 1.8, 2, 1), candle(4, 2.2, 1.8, 2, 1)]
        features = geckoterminal.compute_completed_daily_features({"data": {"attributes": {"ohlcv_list": rows}}}, 2, 2, 2, dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc))
        self.assertAlmostEqual(0.8, features["compression_score"])

    def test_drawdown_is_never_negative(self):
        self.assertEqual(0.0, alpha_logic.pct_drawdown(2, 1))

    def test_non_passed_signal_is_watch(self):
        conn = dbm.connect(":memory:")
        config = {"alpha": {"market_cap_limit_usd": 100_000_000, "drawdown_threshold_pct": 90, "volume_expansion_ratio_min": 1.5, "volume_min_usd": 5_000_000}}
        token = {"token_key": "base:x", "symbol": "ABC", "chain": "base", "contract_address": "x", "alpha_symbol": "ABCUSDT", "futures_symbol": "ABCUSDT", "primary_pool_id": "base/p"}
        inputs = {"ready": False, "price_usd": 2, "listing_reference_open_price": 3, "ath_price": 3, "market_cap_usd": 10, "market_cap_confidence": "verified", "current_volume": 1, "baseline_volumes": [1], "price_above_range": True, "compression_score": 1, "provenance": {}, "failure_reason": "missing_market_cap"}
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=[token]), patch("alpha_pipeline.fetch_current_pool_contexts", return_value={"base:x": {}}), patch("alpha_pipeline.warm_alpha_reference_cache", return_value=({"base:x": {"payload": {}}}, 0)), patch("alpha_pipeline.fetch_screen_inputs", return_value=inputs):
            alpha_pipeline.refresh_alpha(conn, config, "2026-08-20T00:00:00")
        self.assertEqual("incomplete", dbm.latest_alpha_run(conn)["status"])
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0])
        conn.close()

    def test_screen_inputs_uses_only_matched_pool_and_no_coingecko_volume_chart(self):
        rows = [candle(day, 3, 1, 2, 10 + day) for day in range(1, 15)]
        token = {"chain": "base", "contract_address": "0xtoken", "alpha_symbol": "ABCUSDT", "primary_pool_id": "base/fixed", "extra_json": '{"alpha_raw":{"marketCap":999,"listingTime":1728000000000},"pool_snapshot":{"pool_ref":"base/fixed","price_usd":4,"volume_24h":100,"liquidity_usd":50,"market_cap_usd":20}}'}
        config = {"alpha": {"volume_baseline_days": 7, "breakout_lookback_days": 7, "compression_lookback_days": 7}}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value={"price": 2, "open_time_ms": 1728086400000, "listing_day_offset_days": 1.0, "source": "binance_web3_dex_contract_kline_ai"}), patch("alpha_pipeline.coingecko.get_contract_coin", side_effect=RuntimeError("unavailable")), patch("alpha_pipeline.coingecko.get_contract_market_chart_range") as chart, patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={"data":{"attributes":{"ohlcv_list":rows}}}), patch("alpha_pipeline.dexscreener.get_pair_by_ref") as direct:
            inputs = alpha_pipeline.fetch_screen_inputs(token, config)
        direct.assert_not_called(); chart.assert_not_called()
        self.assertEqual(4.0, inputs["price_usd"]); self.assertTrue(inputs["price_above_range"])

    def test_missing_cap_fails_closed(self):
        token = {"chain": "base", "contract_address": "0xtoken", "alpha_symbol": "ABCUSDT", "primary_pool_id": "base/fixed", "extra_json": '{"alpha_raw":{"listingTime":1728000000000},"pool_snapshot":{"pool_ref":"base/fixed","price_usd":4,"volume_24h":100,"liquidity_usd":50}}'}
        config = {"alpha": {"volume_baseline_days": 1, "breakout_lookback_days": 1, "compression_lookback_days": 1}}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value={"price": 2, "open_time_ms": 1728086400000, "listing_day_offset_days": 1.0, "source": "binance_web3_dex_contract_kline_ai"}), patch("alpha_pipeline.coingecko.get_contract_coin", side_effect=RuntimeError("unavailable")), patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={"data":{"attributes":{"ohlcv_list":[candle(1,3,1,2,10)]}}}):
            inputs = alpha_pipeline.fetch_screen_inputs(token, config)
        self.assertFalse(inputs["ready"]); self.assertIn("missing_market_cap", inputs["failure_reason"])

    def test_dead_pool_and_zero_baseline_fail_closed(self):
        token = {"chain": "base", "contract_address": "0xtoken", "alpha_symbol": "ABCUSDT", "primary_pool_id": "base/fixed", "extra_json": '{"alpha_raw":{"listingTime":1728000000000},"pool_snapshot":{"pool_ref":"base/fixed","price_usd":0,"volume_24h":-1,"liquidity_usd":0,"market_cap_usd":0}}'}
        config = {"alpha": {"volume_baseline_days": 1, "breakout_lookback_days": 1, "compression_lookback_days": 1}}
        payload = {"data": {"attributes": {"ohlcv_list": [candle(1, 3, 1, 2, 0)]}}}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=None), patch("alpha_pipeline.coingecko.get_contract_coin", side_effect=RuntimeError("unavailable")), patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value=payload):
            inputs = alpha_pipeline.fetch_screen_inputs(token, config)
        self.assertFalse(inputs["ready"])
        for reason in ["invalid_price_non_positive", "invalid_current_volume_non_positive", "invalid_liquidity_non_positive", "invalid_baseline_volume_non_positive", "missing_listing_reference_open", "invalid_market_cap_non_positive"]:
            self.assertIn(reason, inputs["failure_reason"])

    def test_config_has_one_merged_symbols_mapping(self):
        with open("config.example.yaml", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        self.assertIn("binance", config["symbols"]); self.assertIn("binance_spot", config["symbols"])


if __name__ == "__main__":
    unittest.main()
