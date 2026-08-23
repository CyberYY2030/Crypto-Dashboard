import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db as dbm
import run_daily


class CollectionRunContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "contract.sqlite")
        self.conn = dbm.connect(self.db_path)
        self.date = dt.date(2026, 8, 20)
        self.config = {
            "display": {"fiat": "usd"},
            "symbols": {
                "binance_spot": {"btc_symbol": "BTCUSDT", "eth_symbol": "ETHUSDT"},
                "binance": {"btc_symbol": "BTCUSDT", "eth_symbol": "ETHUSDT"},
            },
            "modules": {"binance_oi": False},
            "sosovalue": {"api_key": ""},
        }

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def _run(self, btc_ticker, eth_ticker):
        with patch("run_daily.binance_spot.backfill_daily_closes", return_value=[]), patch(
            "run_daily.feargreed.get_history", return_value=[]
        ), patch("run_daily.defillama.get_defi_tvl_history", return_value=[]), patch(
            "run_daily.defillama.get_stablecoin_charts_all", return_value=[]
        ), patch("run_daily.get_soso_key", return_value=None), patch(
            "run_daily.binance_spot.get_24hr_ticker", side_effect=[btc_ticker, eth_ticker]
        ):
            return run_daily.run_daily_collection(self.conn, self.config, self.date, entrypoint="contract-test")

    def test_schema_is_idempotent(self):
        second = dbm.connect(self.db_path)
        try:
            tables = {row[0] for row in second.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            second.close()
        self.assertIn("collection_runs", tables)

    def test_complete_publish_marks_latest_complete(self):
        result = self._run(
            {"lastPrice": "80000", "priceChangePercent": "1.25", "quoteVolume": "1"},
            {"lastPrice": "3000", "priceChangePercent": "2.5", "quoteVolume": "1"},
        )

        truth = dbm.get_latest_metrics_date(self.conn)
        self.assertEqual("complete", result["status"])
        self.assertEqual(self.date.isoformat(), truth["date"])
        self.assertFalse(truth["legacy_unverified"])
        self.assertEqual(4, len(truth["run"]["actual_core_metrics"]))

    def test_missing_core_rolls_back_and_does_not_publish(self):
        result = self._run(
            {"lastPrice": "80000", "priceChangePercent": None, "quoteVolume": "1"},
            {"lastPrice": "3000", "priceChangePercent": "2.5", "quoteVolume": "1"},
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual({}, dbm.fetch_metrics_for_date(self.conn, self.date.isoformat()))
        self.assertIsNone(dbm.get_latest_metrics_date(self.conn)["date"])

    def test_same_day_failed_rerun_preserves_last_complete_truth(self):
        self._run(
            {"lastPrice": "80000", "priceChangePercent": "1.25", "quoteVolume": "1"},
            {"lastPrice": "3000", "priceChangePercent": "2.5", "quoteVolume": "1"},
        )
        self._run(
            {"lastPrice": "80001", "priceChangePercent": None, "quoteVolume": "1"},
            {"lastPrice": "3001", "priceChangePercent": "2.6", "quoteVolume": "1"},
        )

        truth = dbm.get_latest_metrics_date(self.conn)
        self.assertEqual(self.date.isoformat(), truth["date"])
        self.assertEqual(80000.0, dbm.fetch_metrics_for_date(self.conn, self.date.isoformat())["btc_price"])
        self.assertEqual("failed", dbm.get_latest_collection_run(self.conn, "daily_macro")["status"])

    def test_legacy_fallback_is_explicitly_unverified(self):
        dbm.upsert_metric(self.conn, "2026-08-19", "btc_price", 79000.0, "legacy")
        self.conn.commit()

        truth = dbm.get_latest_metrics_date(self.conn)
        self.assertEqual("2026-08-19", truth["date"])
        self.assertTrue(truth["legacy_unverified"])
        self.assertIsNone(truth["run"])


if __name__ == "__main__":
    unittest.main()
