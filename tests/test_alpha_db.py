import unittest

import db as dbm


class AlphaDbSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _complete_run(self, run_id="run-1", ts="2026-04-15T06:38:12"):
        dbm.start_alpha_screen_run(self.conn, run_id, ts, "test", 1, False)
        dbm.finish_alpha_screen_run(self.conn, run_id, "complete", {"eligible": 1, "snapshot": 1, "ready": 1, "passed": 0}, [])
        return run_id

    def test_alpha_tables_exist(self):
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("alpha_universe", names)
        self.assertIn("alpha_screen_snapshots", names)
        self.assertIn("alpha_holder_snapshots", names)

    def test_upsert_alpha_universe_round_trip(self):
        dbm.upsert_alpha_universe(
            self.conn,
            {
                "token_key": "base:0xabc",
                "symbol": "ABC",
                "chain": "base",
                "contract_address": "0xabc",
                "alpha_symbol": "ALPHA_175USDT",
                "futures_symbol": "ABCUSDT",
                "market_cap_confidence": "estimated",
            },
        )
        row = dbm.fetch_alpha_universe(self.conn)[0]
        self.assertEqual(row["token_key"], "base:0xabc")
        self.assertEqual(row["market_cap_confidence"], "estimated")

    def test_fetch_latest_alpha_candidates_returns_ranked_rows(self):
        dbm.upsert_alpha_universe(
            self.conn,
            {
                "token_key": "base:0xabc",
                "symbol": "ABC",
                "chain": "base",
                "contract_address": "0xabc",
                "alpha_symbol": "ALPHA_175USDT",
                "futures_symbol": "ABCUSDT",
                "market_cap_confidence": "verified",
            },
        )
        run_id = self._complete_run("run-candidate", "2026-04-14T10:00:00")
        dbm.insert_alpha_screen_snapshot(
            self.conn,
            {
                "ts": "2026-04-14T10:00:00",
                "token_key": "base:0xabc",
                "signal_label": "first_volume_breakout",
                "score": 88.5,
                "price_usd": 0.10,
                "volume_24h": 500000,
                "volume_expansion_ratio": 3.2,
                "liquidity_usd": 250000,
                "market_cap_usd": 50000000,
                "market_cap_confidence": "verified",
                "drawdown_from_alpha_open_pct": 95.0,
                "drawdown_from_listing_reference_pct": 94.0,
                "drawdown_from_ath_pct": 96.0,
                "passed_layer1": 1,
                "run_id": run_id,
            },
        )
        rows = dbm.fetch_latest_alpha_candidates(self.conn)
        self.assertEqual(rows[0]["token_key"], "base:0xabc")
        self.assertEqual(94.0, rows[0]["drawdown_from_listing_reference_pct"])

    def test_fetch_latest_alpha_snapshot_returns_rows_even_when_none_passed(self):
        dbm.upsert_alpha_universe(
            self.conn,
            {
                "token_key": "base:0xdef",
                "symbol": "DEF",
                "chain": "base",
                "contract_address": "0xdef",
                "alpha_symbol": "ALPHA_200USDT",
                "futures_symbol": "DEFUSDT",
                "market_cap_confidence": "estimated",
            },
        )
        run_id = self._complete_run("run-watch")
        dbm.insert_alpha_screen_snapshot(
            self.conn,
            {
                "ts": "2026-04-15T06:38:12",
                "token_key": "base:0xdef",
                "signal_label": "watch",
                "score": 42.0,
                "price_usd": 0.05,
                "volume_24h": 10000000,
                "volume_expansion_ratio": 1.48,
                "liquidity_usd": 500000,
                "market_cap_usd": 20000000,
                "market_cap_confidence": "estimated",
                "drawdown_from_alpha_open_pct": 80.0,
                "drawdown_from_listing_reference_pct": 79.0,
                "drawdown_from_ath_pct": 99.0,
                "passed_layer1": 0,
                "run_id": run_id,
            },
        )
        rows = dbm.fetch_latest_alpha_snapshot(self.conn)
        self.assertEqual(rows[0]["token_key"], "base:0xdef")
        self.assertEqual(79.0, rows[0]["drawdown_from_listing_reference_pct"])

    def test_legacy_failed_and_running_rows_are_not_verified_candidates(self):
        dbm.upsert_alpha_universe(self.conn, {"token_key":"base:legacy","symbol":"LEG","chain":"base","contract_address":"legacy","alpha_symbol":"LEGUSDT","futures_symbol":"LEGUSDT"})
        for index, (run_id, status) in enumerate([(None, None), ("failed", "failed"), ("running", "running")]):
            if status:
                dbm.start_alpha_screen_run(self.conn, run_id, "2026-08-20T00:00:00", "test", 1, False)
                if status == "failed": dbm.finish_alpha_screen_run(self.conn, run_id, "failed", {}, [])
            dbm.insert_alpha_screen_snapshot(self.conn, {"ts":f"2026-08-20T00:00:0{index}","token_key":"base:legacy","signal_label":"first_volume_breakout","passed_layer1":1,"run_id":run_id})
        self.assertEqual([], dbm.fetch_latest_alpha_snapshot(self.conn))
        self.assertEqual([], dbm.fetch_latest_alpha_candidates(self.conn))


if __name__ == "__main__":
    unittest.main()
