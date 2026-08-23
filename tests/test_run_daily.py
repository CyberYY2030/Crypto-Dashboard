import datetime as dt
import unittest
from unittest.mock import patch

import db as dbm
import run_daily


class RunDailyTests(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_collect_today_snapshot_returns_data_and_skips_soso_when_key_missing(self):
        config = {
            "display": {"fiat": "usd"},
            "symbols": {
                "binance_spot": {"btc_symbol": "BTCUSDT", "eth_symbol": "ETHUSDT"},
                "binance": {"btc_symbol": "BTCUSDT", "eth_symbol": "ETHUSDT"},
                "coingecko": {"btc_id": "bitcoin", "eth_id": "ethereum"},
            },
            "modules": {"binance_oi": True},
            "sosovalue": {"api_key": ""},
        }

        with patch("run_daily.binance_spot.get_24hr_ticker") as get_24hr_ticker, patch(
            "run_daily.binance_derivatives.get_open_interest"
        ) as get_open_interest, patch("run_daily.get_soso_key") as get_soso_key, patch(
            "run_daily.historical_inflow_chart"
        ) as historical_inflow_chart, patch("run_daily.current_etf_data_metrics") as current_etf_data_metrics:
            get_24hr_ticker.side_effect = [
                {"lastPrice": "80000", "priceChangePercent": "2.5", "quoteVolume": "100000000"},
                {"lastPrice": "3000", "priceChangePercent": "3.5", "quoteVolume": "50000000"},
            ]
            get_open_interest.side_effect = [100000.0, 200000.0]
            get_soso_key.return_value = None

            snapshot = run_daily.collect_today_snapshot(self.conn, config, dt.date(2026, 4, 16))

        self.assertEqual(snapshot["date"], "2026-04-16")
        self.assertEqual(snapshot["btc_price"], 80000.0)
        self.assertEqual(snapshot["eth_price"], 3000.0)
        historical_inflow_chart.assert_not_called()
        current_etf_data_metrics.assert_not_called()

    def test_main_still_collects_today_snapshot_when_backfill_fails(self):
        mock_conn = unittest.mock.Mock()
        config = {"db_path": ":memory:"}

        with patch("run_daily.load_config", return_value=config), patch(
            "run_daily.dbm.connect", return_value=mock_conn
        ), patch("run_daily.shanghai_today", return_value=dt.date(2026, 4, 16)), patch(
            "run_daily.ensure_price_history"
        ), patch("run_daily.backfill_fear_greed"), patch(
            "run_daily.backfill_defillama", side_effect=RuntimeError("timeout")
        ), patch("run_daily.compute_derived_metrics"), patch(
            "run_daily.collect_today_snapshot",
            return_value={"date": "2026-04-16", "btc_price": 80000.0, "btc_24h": 1.0, "eth_price": 3000.0, "eth_24h": 2.0},
        ) as collect_today_snapshot, patch("run_daily.maybe_send_alert", return_value=False):
            run_daily.main()

        collect_today_snapshot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
