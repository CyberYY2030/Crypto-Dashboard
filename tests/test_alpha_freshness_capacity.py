import datetime as dt
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alpha_pipeline
import db as dbm
import run_alpha
import requests
from collectors import dexscreener


def token(number, chain="base"):
    return {
        "token_key": f"{chain}:{number}",
        "chain": chain,
        "contract_address": str(number),
        "alpha_symbol": f"T{number}USDT",
        "futures_symbol": f"T{number}USDT",
        "primary_pool_id": f"{chain}/fixed-{number}",
    }


def reference_payload():
    return {
        "listing_reference_open_price": 2.0,
        "listing_reference_open_time_ms": 1728000000000,
        "listing_reference_day_offset_days": 1.0,
        "ath_price": 3.0,
        "market_cap_usd": 10.0,
        "market_cap_confidence": "verified",
        "features": {
            "baseline_volumes": [3.0],
            "baseline_volume_median": 3.0,
            "previous_high": 0.05,
            "compression_score": 0.0,
        },
        "provenance": {
            "completed_candle_count": 7,
            "listing_reference_source": "binance_web3_dex_contract_kline_ai",
            "listing_reference_semantic": "first_available_daily_candle_open",
        },
    }


class AlphaFreshnessCapacityTests(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")
        self.config = {
            "alpha": {
                "refresh_minutes": 30,
                "stale_run_minutes": 60,
                "universe_refresh_hours": 24,
                "reference_refresh_batch_size": 20,
                "reference_request_interval_seconds": 0,
                "reference_refresh_hours": 24,
                "reference_max_age_hours": 36,
                "reference_failure_retry_hours": 6,
                "min_current_pool_ratio": 0.8,
                "min_ready_ratio": 0.8,
                "market_cap_limit_usd": 100000000,
                "drawdown_threshold_pct": 90,
                "volume_min_usd": 1,
                "volume_expansion_ratio_min": 1.5,
            }
        }

    def tearDown(self):
        self.conn.close()

    def test_fixed_pool_batch_is_fresh_each_force_run_and_does_not_rotate(self):
        item = token("a")
        pair_calls = []

        def pairs(chain, addresses, **kwargs):
            pair_calls.append(kwargs)
            price = 0.1 if len(pair_calls) == 1 else 0.2
            return {
                "a": [
                    {"pairAddress": "new", "priceUsd": "9", "volume": {"h24": 9}, "liquidity": {"usd": 9}},
                    {"pairAddress": "fixed-a", "priceUsd": str(price), "volume": {"h24": 6}, "liquidity": {"usd": 1}},
                ]
            }

        refs = {"base:a": {"payload": reference_payload()}}
        with patch("alpha_pipeline.sync_alpha_universe") as sync, patch("alpha_pipeline.fetch_alpha_rows", return_value=[item]), patch("alpha_pipeline.dexscreener.get_token_pairs_batch", side_effect=pairs), patch("alpha_pipeline.warm_alpha_reference_cache", return_value=(refs, 0)), patch("alpha_pipeline.binance_derivatives.get_funding_rate", return_value=.01), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch("alpha_pipeline.binance_derivatives.get_mark_price", return_value=3):
            first = alpha_pipeline.refresh_alpha(self.conn, self.config, "first", force=True)
            second = alpha_pipeline.refresh_alpha(self.conn, self.config, "second", force=True)
        self.assertEqual("complete", first["status"])
        self.assertEqual("complete", second["status"])
        self.assertEqual(1, sync.call_count)
        self.assertEqual(2, len(pair_calls))
        self.assertTrue(all(call["allow_individual_fallback"] is False for call in pair_calls))
        prices = self.conn.execute("SELECT price_usd FROM alpha_screen_snapshots ORDER BY ts").fetchall()
        self.assertEqual([(0.1,), (0.2,)], prices)
        drawdowns = self.conn.execute(
            "SELECT drawdown_from_alpha_open_pct,drawdown_from_listing_reference_pct "
            "FROM alpha_screen_snapshots ORDER BY ts"
        ).fetchall()
        self.assertEqual([(None, 95.0), (None, 90.0)], drawdowns)

    def test_missing_or_failed_batch_never_calls_individual_pair_fallback(self):
        item = token("a")
        new_only = {"a": [{"pairAddress": "new", "priceUsd": "9", "volume": {"h24": 9}, "liquidity": {"usd": 9}}]}
        with patch("alpha_pipeline.dexscreener.get_token_pairs_batch", return_value=new_only), patch(
            "alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={"new": new_only["a"][0]}
        ), patch("alpha_pipeline.dexscreener.get_token_pairs") as individual:
            missing_context = alpha_pipeline.fetch_current_pool_contexts([item])
        self.assertEqual("fixed_pool_missing_from_batch", missing_context["base:a"]["failure_reason"])
        individual.assert_not_called()
        with patch("alpha_pipeline.dexscreener.get_token_pairs_batch", side_effect=RuntimeError("down")), patch(
            "alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={}
        ), patch("alpha_pipeline.dexscreener.get_token_pairs") as individual:
            contexts = alpha_pipeline.fetch_current_pool_contexts([item])
        self.assertEqual("current_pool_batch:RuntimeError", contexts["base:a"]["failure_reason"])
        individual.assert_not_called()

    def test_202_token_distribution_uses_ten_bounded_dex_batches(self):
        distribution = {"bsc": 163, "base": 20, "ethereum": 17, "linea": 1, "arbitrum": 1}
        tokens = [token(f"{chain}-{index}", chain) for chain, count in distribution.items() for index in range(count)]

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return []

        with patch("collectors.dexscreener.requests.get", return_value=Response()) as request, patch(
            "alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={}
        ):
            alpha_pipeline.fetch_current_pool_contexts(tokens)
        self.assertEqual(10, request.call_count)
        for call in request.call_args_list:
            addresses = call.args[0].rsplit("/", 1)[1].split(",")
            self.assertLessEqual(len(addresses), 30)

    def test_reference_cache_respects_batch_and_failure_retry_without_losing_payload(self):
        tokens = [token("a"), token("b"), token("c")]
        config = {"alpha": {**self.config["alpha"], "reference_refresh_batch_size": 2}}
        reference = {"price": 2, "open_time_ms": 1728000000000, "listing_day_offset_days": 1.0, "source": "binance_web3_dex_contract_kline_ai"}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=reference) as listing_open, patch("alpha_pipeline.coingecko.get_contract_coin", return_value={}) as cg, patch("alpha_pipeline.coingecko.extract_contract_coin_context", return_value={}), patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={}) as gt, patch("alpha_pipeline.geckoterminal.compute_completed_daily_features", return_value=reference_payload()["features"]):
            cache, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens, config, dt.datetime.utcnow())
        self.assertEqual(2, refreshed)
        self.assertEqual(2, listing_open.call_count)
        self.assertLessEqual(cg.call_count, 2)
        self.assertLessEqual(gt.call_count, 2)
        self.assertEqual(2, len(cache))
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open") as fresh_listing, patch("alpha_pipeline.coingecko.get_contract_coin") as fresh_cg, patch("alpha_pipeline.geckoterminal.get_pool_ohlcv") as fresh_gt:
            alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens[:2], config, dt.datetime.utcnow())
        fresh_listing.assert_not_called()
        fresh_cg.assert_not_called()
        fresh_gt.assert_not_called()
        old_payload = cache["base:a"]["payload"]
        self.conn.execute("UPDATE alpha_reference_cache SET refreshed_at=? WHERE token_key='base:a'", ((dt.datetime.utcnow() - dt.timedelta(days=2)).isoformat(),))
        self.conn.commit()
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", side_effect=RuntimeError("down")) as failed_open:
            alpha_pipeline.warm_alpha_reference_cache(self.conn, [tokens[0]], config, dt.datetime.utcnow())
        row = dbm.fetch_alpha_reference_cache(self.conn, ["base:a"])["base:a"]
        self.assertEqual("failed", row["outcome"])
        self.assertEqual(old_payload, row["payload"])
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open") as blocked_open:
            alpha_pipeline.warm_alpha_reference_cache(self.conn, [tokens[0]], config, dt.datetime.utcnow())
        self.assertEqual(1, failed_open.call_count)
        blocked_open.assert_not_called()

    def test_reference_attempt_pacing_uses_start_to_start_interval(self):
        tokens = [token("paced-a"), token("paced-b"), token("paced-c")]
        config = {"alpha": {**self.config["alpha"]}}
        config["alpha"].pop("reference_request_interval_seconds")
        with patch("alpha_pipeline._reference_payload", return_value=reference_payload()), patch(
            "alpha_pipeline.time.monotonic", side_effect=[0.0, 0.5, 15.1, 15.6, 30.2]
        ), patch("alpha_pipeline.time.sleep") as sleep:
            _, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens, config, dt.datetime.utcnow())
        self.assertEqual(3, refreshed)
        self.assertEqual([14.6, 14.6], [call.args[0] for call in sleep.call_args_list])

    def test_gt_429_records_failure_then_stops_batch_without_more_attempts(self):
        tokens = [token("stop-a"), token("stop-b"), token("stop-c"), token("stop-d")]
        config = {"alpha": {**self.config["alpha"]}}
        config["alpha"].pop("reference_request_interval_seconds")
        with patch(
            "alpha_pipeline._reference_payload",
            side_effect=[
                reference_payload(),
                alpha_pipeline._ReferenceValidationError("gt_ohlcv_http_429"),
                reference_payload(),
                reference_payload(),
            ],
        ) as payload, patch("alpha_pipeline.time.monotonic", side_effect=[0.0, 0.5, 15.1]), patch(
            "alpha_pipeline.time.sleep"
        ) as sleep:
            _, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens, config, dt.datetime.utcnow())
        self.assertEqual(1, refreshed)
        self.assertEqual(2, payload.call_count)
        self.assertEqual([14.6], [call.args[0] for call in sleep.call_args_list])
        entries = dbm.fetch_alpha_reference_cache(self.conn, [item["token_key"] for item in tokens])
        self.assertEqual("success", entries["base:stop-a"]["outcome"])
        self.assertEqual("gt_ohlcv_http_429", entries["base:stop-b"]["error_summary"])
        self.assertNotIn("base:stop-c", entries)
        self.assertNotIn("base:stop-d", entries)

    def test_non_429_reference_failure_continues_to_next_candidate(self):
        tokens = [token("continue-a"), token("continue-b")]
        with patch(
            "alpha_pipeline._reference_payload",
            side_effect=[alpha_pipeline._ReferenceValidationError("gt_features_RuntimeError"), reference_payload()],
        ) as payload:
            _, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens, self.config, dt.datetime.utcnow())
        self.assertEqual(1, refreshed)
        self.assertEqual(2, payload.call_count)
        entries = dbm.fetch_alpha_reference_cache(self.conn, [item["token_key"] for item in tokens])
        self.assertEqual("gt_features_RuntimeError", entries["base:continue-a"]["error_summary"])
        self.assertEqual("success", entries["base:continue-b"]["outcome"])

    def test_reference_attempt_interval_zero_and_cache_hit_do_not_sleep(self):
        tokens = [token("zero-a"), token("zero-b")]
        with patch("alpha_pipeline._reference_payload", return_value=reference_payload()), patch(
            "alpha_pipeline.time.sleep"
        ) as sleep:
            _, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, tokens, self.config, dt.datetime.utcnow())
        self.assertEqual(2, refreshed)
        sleep.assert_not_called()

        now = dt.datetime.utcnow()
        cached = token("cache-hit")
        dbm.upsert_alpha_reference_cache(
            self.conn,
            cached["token_key"],
            reference_payload(),
            now.isoformat(),
            now.isoformat(),
            "success",
        )
        self.conn.commit()
        config = {"alpha": {**self.config["alpha"]}}
        config["alpha"].pop("reference_request_interval_seconds")
        with patch("alpha_pipeline.time.monotonic") as monotonic, patch("alpha_pipeline.time.sleep") as sleep:
            alpha_pipeline.warm_alpha_reference_cache(self.conn, [cached], config, now)
        monotonic.assert_not_called()
        sleep.assert_not_called()

    def test_gt_errors_use_safe_stage_codes_and_preserve_old_payload(self):
        http_error = requests.HTTPError("https://secret.example/response")
        http_error.response = type("Response", (), {"status_code": 429})()
        cases = [
            ("http", "pool", http_error, "gt_ohlcv_http_429"),
            ("timeout", "pool", requests.Timeout("https://secret.example/timeout"), "gt_ohlcv_Timeout"),
            ("features", "features", RuntimeError("https://secret.example/features"), "gt_features_RuntimeError"),
        ]
        listing = {
            "price": 2,
            "open_time_ms": 1728000000000,
            "listing_day_offset_days": 1,
            "source": "binance_web3_dex_contract_kline_ai",
        }
        old_timestamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
        for suffix, stage, error, expected in cases:
            with self.subTest(stage=stage):
                item = {**token(f"gt-{suffix}"), "extra_json": '{"alpha_raw":{"listingTime":1728000000000}}'}
                old_payload = reference_payload()
                dbm.upsert_alpha_reference_cache(
                    self.conn,
                    item["token_key"],
                    old_payload,
                    old_timestamp,
                    old_timestamp,
                    "success",
                )
                self.conn.commit()
                with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=listing):
                    if stage == "features":
                        with patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={}), patch(
                            "alpha_pipeline.geckoterminal.compute_completed_daily_features", side_effect=error
                        ):
                            alpha_pipeline.warm_alpha_reference_cache(self.conn, [item], self.config, dt.datetime.utcnow())
                    else:
                        with patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", side_effect=error):
                            alpha_pipeline.warm_alpha_reference_cache(self.conn, [item], self.config, dt.datetime.utcnow())
                entry = dbm.fetch_alpha_reference_cache(self.conn, [item["token_key"]])[item["token_key"]]
                self.assertEqual("failed", entry["outcome"])
                self.assertEqual(expected, entry["error_summary"])
                self.assertEqual(old_payload, entry["payload"])
                self.assertNotIn("secret.example", entry["error_summary"])

    def test_listing_reference_missing_short_circuits_gt_and_coingecko(self):
        item = {**token("listing-missing"), "extra_json": '{"alpha_raw":{"listingTime":1728000000000}}'}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=None), patch(
            "alpha_pipeline.geckoterminal.get_pool_ohlcv"
        ) as gt, patch("alpha_pipeline.coingecko.get_contract_coin") as cg:
            with self.assertRaises(alpha_pipeline._ReferenceValidationError) as raised:
                alpha_pipeline._reference_payload(item, self.config)
        self.assertEqual("listing_reference_open_missing_or_invalid", str(raised.exception))
        gt.assert_not_called()
        cg.assert_not_called()

    def test_old_alpha_open_cache_is_refreshed_and_new_listing_payload_is_usable(self):
        item = {**token("old-cache"), "extra_json": '{"alpha_raw":{"listingTime":1728000000000}}'}
        now = dt.datetime.utcnow()
        dbm.upsert_alpha_reference_cache(
            self.conn,
            item["token_key"],
            {"alpha_open_price": 2, "features": reference_payload()["features"], "provenance": {}},
            now.isoformat(),
            now.isoformat(),
            "success",
        )
        listing = {"price": 2, "open_time_ms": 1728086400000, "listing_day_offset_days": 1.0, "source": "binance_web3_dex_contract_kline_ai"}
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=listing) as fetch, patch(
            "alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={}
        ), patch("alpha_pipeline.geckoterminal.compute_completed_daily_features", return_value=reference_payload()["features"]), patch(
            "alpha_pipeline.coingecko.get_contract_coin", return_value={}
        ), patch("alpha_pipeline.coingecko.extract_contract_coin_context", return_value={}):
            usable, refreshed = alpha_pipeline.warm_alpha_reference_cache(self.conn, [item], self.config, now)
        self.assertEqual(1, fetch.call_count)
        self.assertEqual(1, refreshed)
        self.assertIn(item["token_key"], usable)
        self.assertIn("listing_reference_open_price", usable[item["token_key"]]["payload"])

    def test_coverage_gate_preserves_previous_complete_then_publishes_at_eighty_percent(self):
        tokens = [token(str(index)) for index in range(100)]
        dbm.upsert_alpha_universe(self.conn, {**token("old"), "symbol": "OLD"})
        dbm.start_alpha_screen_run(self.conn, "previous", "old", "test", 1, False)
        dbm.finish_alpha_screen_run(self.conn, "previous", "complete", {"eligible": 1, "snapshot": 1}, [])
        dbm.insert_alpha_screen_snapshot(self.conn, {"ts": "old", "token_key": "base:old", "signal_label": "watch", "passed_layer1": 0, "run_id": "previous"})
        self.conn.commit()
        contexts = {
            item["token_key"]: {
                "pool_ref": item["primary_pool_id"],
                "price_usd": 0.1,
                "volume_24h": 6,
                "liquidity_usd": 1,
            }
            for item in tokens
        }
        refs = {item["token_key"]: {"payload": reference_payload()} for item in tokens}

        def inputs(item, config, current, reference):
            ready = int(item["contract_address"]) < self.ready_count
            return {"ready": ready, "failure_reason": None if ready else "missing_reference_cache", "price_usd": .1, "listing_reference_open_price": 2, "ath_price": 3, "market_cap_usd": 10, "market_cap_confidence": "verified", "current_volume": 6, "baseline_volumes": [3], "liquidity_usd": 1, "price_above_range": True, "compression_score": 0, "provenance": {}}

        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=tokens), patch("alpha_pipeline.fetch_current_pool_contexts", return_value=contexts), patch("alpha_pipeline.warm_alpha_reference_cache", return_value=(refs, 0)), patch("alpha_pipeline.fetch_screen_inputs", side_effect=inputs), patch("alpha_pipeline.binance_derivatives.get_funding_rate", return_value=.01), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch("alpha_pipeline.binance_derivatives.get_mark_price", return_value=3):
            self.ready_count = 79
            incomplete = alpha_pipeline.refresh_alpha(self.conn, self.config, "79", force=True)
            published_after_incomplete = dbm.fetch_latest_alpha_snapshot(self.conn)
            self.ready_count = 80
            complete = alpha_pipeline.refresh_alpha(self.conn, self.config, "80", force=True)
        self.assertEqual("incomplete", incomplete["status"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots WHERE run_id=?", (incomplete["run_id"],)).fetchone()[0])
        self.assertEqual("old", published_after_incomplete[0]["ts"])
        self.assertEqual("complete", complete["status"])
        self.assertEqual(100, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots WHERE run_id=?", (complete["run_id"],)).fetchone()[0])

    def test_warmed_references_survive_incomplete_publication(self):
        items = [token("a"), token("b")]
        contexts = {
            "base:a": {"pool_ref": "base/fixed-a", "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1},
            "base:b": {"pool_ref": "base/fixed-b", "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1},
        }
        reference = {"price": 2, "open_time_ms": 1728000000000, "listing_day_offset_days": 1.0, "source": "binance_web3_dex_contract_kline_ai"}
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=items), patch("alpha_pipeline.fetch_current_pool_contexts", return_value=contexts), patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=reference), patch("alpha_pipeline.coingecko.get_contract_coin", return_value={}), patch("alpha_pipeline.coingecko.extract_contract_coin_context", return_value={}), patch("alpha_pipeline.geckoterminal.get_pool_ohlcv", return_value={}), patch("alpha_pipeline.geckoterminal.compute_completed_daily_features", return_value=reference_payload()["features"]), patch("alpha_pipeline.fetch_screen_inputs", return_value={"ready": False, "failure_reason": "missing_reference_cache", "price_usd": .1, "listing_reference_open_price": 2, "ath_price": 3, "market_cap_usd": 10, "market_cap_confidence": "verified", "current_volume": 6, "baseline_volumes": [3], "liquidity_usd": 1, "price_above_range": False, "compression_score": None, "provenance": {}}), patch("alpha_pipeline.binance_derivatives.get_funding_rate", return_value=.01), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch("alpha_pipeline.binance_derivatives.get_mark_price", return_value=3):
            result = alpha_pipeline.refresh_alpha(self.conn, self.config, "incomplete", force=True)
        self.assertEqual("incomplete", result["status"])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM alpha_reference_cache").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots WHERE run_id=?", (result["run_id"],)).fetchone()[0])

    def test_reference_warm_exception_marks_claim_failed_without_snapshot_or_running_run(self):
        item = token("warm-error")
        context = {"pool_ref": item["primary_pool_id"], "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1}
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=[item]), patch("alpha_pipeline.fetch_current_pool_contexts", return_value={item["token_key"]: context}), patch("alpha_pipeline.warm_alpha_reference_cache", side_effect=RuntimeError("provider state")):
            result = alpha_pipeline.refresh_alpha(self.conn, self.config, "warm-error", force=True)
        self.assertEqual(("failed", "RuntimeError"), (result["status"], result["reason"]))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_runs WHERE status='running'").fetchone()[0])

    def test_fresh_inputs_fail_closed_for_non_finite_core_values_and_baselines(self):
        item = token("finite")
        current = {"pool_ref": item["primary_pool_id"], "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1}
        reference = reference_payload()
        invalid_sources = (
            ("price_usd", current),
            ("volume_24h", current),
            ("liquidity_usd", current),
            ("baseline_volume_median", reference["features"]),
            ("listing_reference_open_price", reference),
            ("market_cap_usd", reference),
        )
        for field, container in invalid_sources:
            with self.subTest(field=field):
                changed_current = dict(current)
                changed_reference = {**reference, "features": dict(reference["features"])}
                changed_container = changed_current if container is current else changed_reference["features"] if container is reference["features"] else changed_reference
                changed_container[field] = float("nan")
                inputs = alpha_pipeline._screen_inputs_from_fresh_data(item, changed_current, changed_reference)
                self.assertFalse(inputs["ready"])
                self.assertTrue(all(math.isfinite(value) for value in inputs["baseline_volumes"]))
                self.assertFalse(any(isinstance(value, float) and math.isnan(value) for value in inputs.values() if value is not None))
        changed_reference = {**reference, "features": {**reference["features"], "baseline_volumes": [3, float("inf")]}, "ath_price": float("nan")}
        inputs = alpha_pipeline._screen_inputs_from_fresh_data(item, current, changed_reference)
        self.assertFalse(inputs["ready"])
        self.assertEqual([], inputs["baseline_volumes"])
        self.assertIsNone(inputs["ath_price"])

    def test_no_eligible_tokens_fails_and_cli_override_is_bounded(self):
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=[]):
            result = alpha_pipeline.refresh_alpha(self.conn, self.config, "empty", force=True)
        self.assertEqual(("failed", "no_eligible_tokens"), (result["status"], result["reason"]))
        self.assertEqual(3, run_alpha.reference_batch_size("3"))
        with self.assertRaises(Exception):
            run_alpha.reference_batch_size("31")

    def test_reference_schema_migrates_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.db")
            raw = sqlite3.connect(path)
            raw.execute("CREATE TABLE alpha_screen_runs(run_id TEXT PRIMARY KEY,snapshot_ts TEXT,started_at TEXT,status TEXT)")
            raw.execute("INSERT INTO alpha_screen_runs VALUES('old','t','t','complete')")
            raw.execute("CREATE TABLE alpha_screen_snapshots(ts TEXT,token_key TEXT,PRIMARY KEY(ts,token_key))")
            raw.execute("CREATE TABLE alpha_holder_snapshots(ts TEXT,token_key TEXT,address TEXT,PRIMARY KEY(ts,token_key,address))")
            raw.commit()
            raw.close()
            first = dbm.connect(path)
            first.close()
            second = dbm.connect(path)
            self.assertEqual(("old", "complete"), second.execute("SELECT run_id,status FROM alpha_screen_runs").fetchone())
            columns = {row[1] for row in second.execute("PRAGMA table_info(alpha_screen_runs)")}
            self.assertTrue({"current_pool_count", "target_count", "reference_ready_count", "reference_refreshed_count"}.issubset(columns))
            tables = {row[0] for row in second.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("alpha_reference_cache", tables)
            second.close()

    def test_exact_pair_batch_recovers_only_the_persisted_fixed_pool(self):
        item = token("a")
        fixed = {"pairAddress": "fixed-a", "priceUsd": ".1", "volume": {"h24": 6}, "liquidity": {"usd": 1}}
        unrelated = {"pairAddress": "new-pool", "priceUsd": "9", "volume": {"h24": 9}, "liquidity": {"usd": 9}}
        with patch("alpha_pipeline.dexscreener.get_token_pairs_batch", return_value={"a": [unrelated]}), patch(
            "alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={"fixed-a": fixed, "new-pool": unrelated}
        ):
            contexts = alpha_pipeline.fetch_current_pool_contexts([item])
        context = contexts[item["token_key"]]
        self.assertEqual("base/fixed-a", context["pool_ref"])
        self.assertEqual("dexscreener_exact_pair_batch", context["source"])
        self.assertEqual(.1, context["price_usd"])

    def test_exact_pair_recovery_is_bounded_and_never_uses_individual_fallback(self):
        distribution = {"bsc": 44, "base": 10, "ethereum": 5, "linea": 1}
        tokens = [token(f"{chain}-{index}", chain) for chain, count in distribution.items() for index in range(count)]
        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"pairs": []}

        with patch("alpha_pipeline.dexscreener.get_token_pairs_batch", return_value={}) as token_batch, patch(
            "collectors.dexscreener.requests.get", return_value=Response()
        ) as exact_request, patch("alpha_pipeline.dexscreener.get_token_pairs") as individual:
            contexts = alpha_pipeline.fetch_current_pool_contexts(tokens)
        self.assertEqual(5, exact_request.call_count)
        for call in exact_request.call_args_list:
            pair_ids = call.args[0].rsplit("/", 1)[1].split(",")
            self.assertLessEqual(len(pair_ids), 30)
        self.assertLessEqual(token_batch.call_count + exact_request.call_count, 15)
        self.assertTrue(all(context["failure_reason"] == "fixed_pool_missing_from_batch" for context in contexts.values()))
        individual.assert_not_called()
        with patch("alpha_pipeline.dexscreener.get_token_pairs_batch", return_value={}), patch(
            "alpha_pipeline.dexscreener.get_pairs_by_ref_batch", side_effect=RuntimeError("exact down")
        ), patch("alpha_pipeline.dexscreener.get_token_pairs") as individual:
            failed_contexts = alpha_pipeline.fetch_current_pool_contexts([token("exception")])
        self.assertEqual("current_pool_exact_batch:RuntimeError", failed_contexts["base:exception"]["failure_reason"])
        individual.assert_not_called()

    def test_current_coverage_gate_blocks_warm_at_seventy_nine_and_allows_eighty(self):
        items = [token(str(index)) for index in range(100)]
        refs = {item["token_key"]: {"payload": reference_payload()} for item in items}

        def contexts(available):
            return {
                item["token_key"]: (
                    {"pool_ref": item["primary_pool_id"], "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1}
                    if int(item["contract_address"]) < available
                    else {"pool_ref": item["primary_pool_id"], "failure_reason": "fixed_pool_missing_from_batch"}
                )
                for item in items
            }

        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=items), patch(
            "alpha_pipeline.fetch_current_pool_contexts", return_value=contexts(79)
        ), patch("alpha_pipeline.warm_alpha_reference_cache") as warm:
            incomplete = alpha_pipeline.refresh_alpha(self.conn, self.config, "current-79", force=True)
        self.assertEqual("incomplete", incomplete["status"])
        self.assertEqual("low_current_pool_ratio", self.conn.execute("SELECT error_summary FROM alpha_screen_runs WHERE run_id=?", (incomplete["run_id"],)).fetchone()[0])
        warm.assert_not_called()
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=items), patch(
            "alpha_pipeline.fetch_current_pool_contexts", return_value=contexts(80)
        ), patch("alpha_pipeline.warm_alpha_reference_cache", return_value=(refs, 0)), patch(
            "alpha_pipeline.fetch_screen_inputs", return_value={"ready": True, "failure_reason": None, "price_usd": .1, "listing_reference_open_price": 2, "ath_price": 3, "market_cap_usd": 10, "market_cap_confidence": "verified", "current_volume": 6, "baseline_volumes": [3], "liquidity_usd": 1, "price_above_range": False, "compression_score": None, "provenance": {}}
        ), patch("alpha_pipeline.binance_derivatives.get_funding_rate", return_value=.01), patch("alpha_pipeline.binance_derivatives.get_open_interest", return_value=2), patch("alpha_pipeline.binance_derivatives.get_mark_price", return_value=3) as mark:
            complete = alpha_pipeline.refresh_alpha(self.conn, self.config, "current-80", force=True)
        self.assertEqual("complete", complete["status"])
        self.assertEqual(80, complete["counts"]["target"])
        self.assertEqual(80, mark.call_count)

    def test_cheap_cap_gate_excludes_only_when_all_known_caps_are_high(self):
        item = {**token("cap"), "extra_json": '{"alpha_raw":{"marketCap":200000000}}'}
        current = {"pool_ref": item["primary_pool_id"], "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1, "market_cap_usd": 150_000_000, "fdv_usd": 120_000_000}
        self.assertFalse(alpha_pipeline._is_conservative_cheap_cap_target(item, current, 100_000_000))
        self.assertTrue(alpha_pipeline._is_conservative_cheap_cap_target(item, {**current, "fdv_usd": 50_000_000}, 100_000_000))
        self.assertTrue(alpha_pipeline._is_conservative_cheap_cap_target({**item, "extra_json": "{}"}, {"pool_ref": item["primary_pool_id"], "price_usd": .1, "volume_24h": 6, "liquidity_usd": 1}, 100_000_000))
        config = {"alpha": {**self.config["alpha"]}}
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=[item]), patch(
            "alpha_pipeline.fetch_current_pool_contexts", return_value={item["token_key"]: current}
        ), patch("alpha_pipeline.warm_alpha_reference_cache") as warm:
            result = alpha_pipeline.refresh_alpha(self.conn, config, "no-target", force=True)
        self.assertEqual("complete", result["status"])
        self.assertEqual(0, result["counts"]["target"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots WHERE run_id=?", (result["run_id"],)).fetchone()[0])
        warm.assert_not_called()

    def test_invalid_listing_reference_short_circuits_gt_and_preserves_old_cache_payload(self):
        item = token("virtual")
        old = reference_payload()
        dbm.upsert_alpha_reference_cache(self.conn, item["token_key"], old, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "success")
        with patch("alpha_pipeline.binance_alpha.fetch_listing_reference_open", return_value=None), patch(
            "alpha_pipeline.geckoterminal.get_pool_ohlcv"
        ) as gt, patch("alpha_pipeline.coingecko.get_contract_coin") as cg:
            alpha_pipeline.warm_alpha_reference_cache(self.conn, [item], self.config, dt.datetime.utcnow())
        entry = dbm.fetch_alpha_reference_cache(self.conn, [item["token_key"]])[item["token_key"]]
        self.assertEqual("failed", entry["outcome"])
        self.assertEqual("listing_reference_open_missing_or_invalid", entry["error_summary"])
        self.assertEqual(old, entry["payload"])
        gt.assert_not_called()
        cg.assert_not_called()

    def test_malformed_fixed_pair_fails_closed_without_rotating_or_fallback(self):
        item = token("bad-pair")
        malformed_fixed = {
            "pairAddress": "fixed-bad-pair",
            "priceUsd": "Infinity",
            "volume": {"h24": 6},
            "liquidity": {"usd": 1},
        }
        with patch(
            "alpha_pipeline.dexscreener.get_token_pairs_batch",
            return_value={"bad-pair": [malformed_fixed]},
        ), patch("alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={}), patch(
            "alpha_pipeline.dexscreener.get_token_pairs"
        ) as individual:
            contexts = alpha_pipeline.fetch_current_pool_contexts([item])
        self.assertEqual("fixed_pool_invalid_numeric", contexts[item["token_key"]]["failure_reason"])
        self.assertEqual("base/fixed-bad-pair", contexts[item["token_key"]]["pool_ref"])
        individual.assert_not_called()

    def test_exact_pair_batch_skips_non_dict_and_non_finite_pairs(self):
        valid = {
            "pairAddress": "fixed-good",
            "priceUsd": ".1",
            "volume": {"h24": 6},
            "liquidity": {"usd": 1},
        }
        non_finite = {
            "pairAddress": "fixed-nan",
            "priceUsd": "NaN",
            "volume": {"h24": 6},
            "liquidity": {"usd": 1},
        }

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"pairs": [None, "bad", non_finite, valid]}

        with patch("collectors.dexscreener.requests.get", return_value=Response()), patch(
            "collectors.dexscreener.get_pair_by_ref"
        ) as individual:
            pairs = dexscreener.get_pairs_by_ref_batch("base", ["fixed-good", "fixed-nan"])
        self.assertEqual({"fixed-good"}, set(pairs))
        individual.assert_not_called()

    def test_malformed_nested_liquidity_or_volume_fails_closed_per_fixed_pool(self):
        item = token("nested")
        for liquidity, volume in (("bad", {"h24": 6}), ({"usd": 1}, [6])):
            with self.subTest(liquidity=liquidity, volume=volume), patch(
                "alpha_pipeline.dexscreener.get_token_pairs_batch",
                return_value={
                    "nested": [{"pairAddress": "fixed-nested", "priceUsd": ".1", "liquidity": liquidity, "volume": volume}]
                },
            ), patch("alpha_pipeline.dexscreener.get_pairs_by_ref_batch", return_value={}):
                contexts = alpha_pipeline.fetch_current_pool_contexts([item])
            self.assertEqual("fixed_pool_invalid_numeric", contexts[item["token_key"]]["failure_reason"])

    def test_token_pair_batch_skips_malformed_base_or_quote_and_keeps_legal_pair(self):
        legal = {
            "pairAddress": "legal",
            "baseToken": {"address": "0xabc"},
            "quoteToken": {"address": "0xquote"},
        }

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return [
                    {"pairAddress": "bad-base", "baseToken": "not-an-object", "quoteToken": {"address": "0xquote"}},
                    {"pairAddress": "bad-quote", "baseToken": {"address": "0xabc"}, "quoteToken": []},
                    legal,
                ]

        with patch("collectors.dexscreener.requests.get", return_value=Response()):
            pairs = dexscreener.get_token_pairs_batch("base", ["0xabc"], allow_individual_fallback=False)
        self.assertEqual([legal], pairs["0xabc"])


if __name__ == "__main__":
    unittest.main()
