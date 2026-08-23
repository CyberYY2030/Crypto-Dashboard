import unittest
from unittest.mock import patch

import alpha_pipeline
import db as dbm


class AlphaPipelineTests(unittest.TestCase):
    def setUp(self):
        self.current_pool_patcher = patch(
            "alpha_pipeline.fetch_current_pool_contexts",
            side_effect=lambda tokens: {
                token["token_key"]: {
                    "pool_ref": token["primary_pool_id"],
                    "price_usd": 0.1,
                    "volume_24h": 6_000_000,
                    "liquidity_usd": 1_000_000,
                }
                for token in tokens
            },
        )
        self.reference_patcher = patch(
            "alpha_pipeline.warm_alpha_reference_cache",
            side_effect=lambda conn, tokens, config, now: (
                {token["token_key"]: {"payload": {}} for token in tokens},
                0,
            ),
        )
        self.current_pool_patcher.start()
        self.reference_patcher.start()

    def tearDown(self):
        self.current_pool_patcher.stop()
        self.reference_patcher.stop()

    def test_refresh_alpha_uses_fixed_pool_inputs_when_raw_volume_is_low(self):
        conn = dbm.connect(":memory:")
        config = {
            "alpha": {
                "market_cap_limit_usd": 100_000_000,
                "drawdown_threshold_pct": 90.0,
                "volume_expansion_ratio_min": 1.5,
                "volume_min_usd": 5_000_000,
            }
        }

        with patch("alpha_pipeline.sync_alpha_universe") as sync_alpha_universe, patch(
            "alpha_pipeline.fetch_alpha_rows"
        ) as alpha_rows, patch("alpha_pipeline.fetch_screen_inputs") as screen_inputs, patch(
            "alpha_pipeline.binance_derivatives.get_funding_rate", return_value=0.01
        ), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch(
            "alpha_pipeline.binance_derivatives.get_mark_price", return_value=3
        ):
            sync_alpha_universe.return_value = None
            alpha_rows.return_value = [
                {
                    "token_key": "base:0xlow",
                    "symbol": "LOW",
                    "chain": "base",
                    "contract_address": "0xlow",
                    "alpha_symbol": "LOWUSDT",
                    "futures_symbol": "LOWUSDT",
                    "primary_pool_id": "base/0xpair",
                    "extra_json": "{\"volume24h\": 4000000, \"marketCap\": 50000000}",
                }
            ]
            screen_inputs.return_value = {
                "ready": True,
                "price_usd": 0.10,
                "listing_reference_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 6_000_000,
                "baseline_volumes": [3_000_000, 3_000_000, 3_000_000],
                "price_above_range": True,
                "compression_score": 0.2,
                "provenance": {"pool_ref": "base/0xpair"},
                "failure_reason": None,
            }

            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-15T14:00:00")

        screen_inputs.assert_called_once()
        count = conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()

    def test_refresh_alpha_universe_persists_screened_candidate(self):
        conn = dbm.connect(":memory:")
        config = {
            "alpha": {
                "market_cap_limit_usd": 100_000_000,
                "drawdown_threshold_pct": 90.0,
                "volume_expansion_ratio_min": 1.5,
                "volume_min_usd": 5_000_000,
            }
        }

        with patch("alpha_pipeline.sync_alpha_universe") as sync_alpha_universe, patch(
            "alpha_pipeline.fetch_alpha_rows"
        ) as alpha_rows, patch("alpha_pipeline.fetch_screen_inputs") as screen_inputs, patch(
            "alpha_pipeline.binance_derivatives.get_funding_rate", return_value=0.01
        ), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch(
            "alpha_pipeline.binance_derivatives.get_mark_price", return_value=3
        ):
            sync_alpha_universe.return_value = None
            alpha_rows.return_value = [
                {
                    "token_key": "base:0xabc",
                    "symbol": "ABC",
                    "chain": "base",
                    "contract_address": "0xabc",
                    "alpha_symbol": "ABCUSDT",
                    "futures_symbol": "ABCUSDT",
                    "primary_pool_id": "base/0xpair",
                }
            ]
            screen_inputs.return_value = {
                "price_usd": 0.10,
                "ready": True,
                "listing_reference_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 6_000_000,
                "baseline_volumes": [3_000_000],
                "price_above_range": True,
                "compression_score": 0.2,
            }

            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-14T10:00:00")

        rows = conn.execute("SELECT token_key, passed_layer1 FROM alpha_screen_snapshots").fetchall()
        self.assertEqual(rows, [("base:0xabc", 1)])
        conn.close()

    def test_refresh_alpha_persists_holder_snapshot_for_passed_tokens(self):
        conn = dbm.connect(":memory:")
        config = {
            "alpha": {
                "market_cap_limit_usd": 100_000_000,
                "drawdown_threshold_pct": 90.0,
                "volume_expansion_ratio_min": 1.5,
                "volume_min_usd": 5_000_000,
                "moralis_api_key": "test-key",
            }
        }

        with patch("alpha_pipeline.sync_alpha_universe") as sync_alpha_universe, patch(
            "alpha_pipeline.fetch_alpha_rows"
        ) as alpha_rows, patch("alpha_pipeline.fetch_screen_inputs") as screen_inputs, patch(
            "alpha_pipeline.fetch_holder_rows"
        ) as holder_rows, patch(
            "alpha_pipeline.binance_derivatives.get_funding_rate", return_value=0.01
        ), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch(
            "alpha_pipeline.binance_derivatives.get_mark_price", return_value=3
        ):
            sync_alpha_universe.return_value = None
            alpha_rows.return_value = [
                {
                    "token_key": "base:0xabc",
                    "symbol": "ABC",
                    "chain": "base",
                    "contract_address": "0xabc",
                    "alpha_symbol": "ABCUSDT",
                    "futures_symbol": "ABCUSDT",
                    "primary_pool_id": "base/0xpair",
                }
            ]
            screen_inputs.return_value = {
                "price_usd": 0.10,
                "ready": True,
                "listing_reference_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 6_000_000,
                "baseline_volumes": [3_000_000],
                "price_above_range": True,
                "compression_score": 0.2,
            }
            holder_rows.return_value = [
                {
                    "address": "0x111",
                    "balance": 1000.0,
                    "pct_supply": 5.0,
                    "address_label": "Coinbase 1",
                    "entity_name": "Coinbase",
                    "holder_type": "exchange",
                    "is_excluded": 1,
                }
            ]

            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-14T10:00:00")

        count = conn.execute("SELECT COUNT(*) FROM alpha_holder_snapshots").fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
