import unittest
import tempfile
import os
from pathlib import Path
import datetime as dt
import sqlite3
from unittest.mock import patch

import alpha_pipeline
import db as dbm


class AlphaRunContractTests(unittest.TestCase):
    def setUp(self):
        self.conn=dbm.connect(":memory:")
        self.config={"alpha":{"refresh_minutes":30,"stale_run_minutes":60,"universe_refresh_hours":24,"market_cap_limit_usd":100000000,"drawdown_threshold_pct":90,"volume_min_usd":1,"volume_expansion_ratio_min":1.5,"holder_refresh_hours":6}}
        self.token={"token_key":"base:x","chain":"base","contract_address":"x","alpha_symbol":"XUSDT","futures_symbol":"XUSDT","primary_pool_id":"base/p"}
        self.current_pool_patcher=patch("alpha_pipeline.fetch_current_pool_contexts",side_effect=self._current_pool_contexts)
        self.reference_patcher=patch("alpha_pipeline.warm_alpha_reference_cache",side_effect=self._reference_cache)
        self.current_pool_patcher.start()
        self.reference_patcher.start()

    def _current_pool_contexts(self,tokens):
        return {token["token_key"]:{"pool_ref":token["primary_pool_id"],"price_usd":.1,"volume_24h":6,"liquidity_usd":1,"observed_at":"test"} for token in tokens}

    def _reference_cache(self,conn,tokens,config,now):
        payload={"listing_reference_open_price":2,"listing_reference_open_time_ms":1728000000000,"listing_reference_day_offset_days":1.0,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"verified","features":{"baseline_volumes":[3],"baseline_volume_median":3,"previous_high":.05,"compression_score":0},"provenance":{"listing_reference_source":"binance_web3_dex_contract_kline_ai","listing_reference_semantic":"first_available_daily_candle_open"}}
        return {token["token_key"]:{"payload":payload} for token in tokens},0

    def tearDown(self):
        self.current_pool_patcher.stop()
        self.reference_patcher.stop()
        self.conn.close()

    def test_input_exception_publishes_watch_for_eligible_token(self):
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows", return_value=[self.token]), patch("alpha_pipeline.fetch_screen_inputs", side_effect=RuntimeError("down")):
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,"2026-08-20T00:00:00",force=True)
        self.assertEqual("incomplete",result["status"]); self.assertEqual(1,result["counts"]["snapshot"])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0])

    def test_cadence_skip_does_not_sync_or_fetch(self):
        dbm.start_alpha_screen_run(self.conn,"old","2026-08-20T00:00:00","test",0,False); dbm.finish_alpha_screen_run(self.conn,"old","complete",{},[]); self.conn.commit()
        with patch("alpha_pipeline.sync_alpha_universe") as sync, patch("alpha_pipeline.fetch_alpha_rows") as rows:
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,force=False)
        self.assertEqual("skipped",result["status"]); sync.assert_not_called(); rows.assert_not_called()

    def test_holder_uses_official_formatted_fields_and_classifies(self):
        token={"chain":"base","contract_address":"x"}; config={"alpha":{"moralis_api_key":"key"}}
        payload=[{"owner_address":"0xa","owner_address_label":"Bridge Pool","balance_formatted":"12.5","percentage_relative_to_total_supply":"1.2","entity":{"name":"Bridge"},"is_contract":True},{"owner_address":"0xb","balance":"999","balance_formatted":"NaN"}]
        with patch("alpha_pipeline.moralis_evm.get_top_holders",return_value=payload): rows=alpha_pipeline.fetch_holder_rows(token,config)
        self.assertEqual(1,len(rows)); self.assertEqual("bridge",rows[0]["holder_type"]); self.assertEqual(12.5,rows[0]["balance"])

    def test_moralis_environment_key_enables_holder_cadence_without_leakage(self):
        inputs={"ready":True,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"v","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        with patch.dict(os.environ,{"MORALIS_API_KEY":" env-test-key "}),patch("alpha_pipeline.sync_alpha_universe"),patch("alpha_pipeline.fetch_alpha_rows",return_value=[self.token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),patch("alpha_pipeline.binance_derivatives.get_funding_rate",return_value=.01),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=2),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=3),patch("alpha_pipeline.moralis_evm.get_top_holders",return_value=[]) as holders:
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,"env-key",force=True)
        holders.assert_called_once_with(token_address="x",chain="base",api_key="env-test-key",limit=20)
        self.assertNotIn("env-test-key",str(result))
        self.assertNotIn("env-test-key",self.conn.execute("SELECT extra_json FROM alpha_screen_snapshots").fetchone()[0])

    def test_fresh_claim_blocks_second_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"claim.db")
            first=dbm.connect(path); second=dbm.connect(path)
            run, reason=dbm.claim_alpha_screen_run(first,"2026-08-20T00:00:00","test",0,30,60,True)
            self.assertIsNotNone(run)
            self.assertEqual((None,"running"), dbm.claim_alpha_screen_run(second,"2026-08-20T00:00:01","test",0,30,60,True))
            first.close(); second.close()

    def test_stale_claim_is_failed_before_replacement(self):
        dbm.start_alpha_screen_run(self.conn,"stale","x","test",0,False)
        self.conn.execute("UPDATE alpha_screen_runs SET started_at=? WHERE run_id='stale'", ((dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2)).isoformat(),)); self.conn.commit()
        run, reason=dbm.claim_alpha_screen_run(self.conn,"y","test",0,30,60,True)
        self.assertIsNotNone(run); self.assertIsNone(reason)
        self.assertEqual("failed",self.conn.execute("SELECT status FROM alpha_screen_runs WHERE run_id='stale'").fetchone()[0])

    def test_holder_rejects_negative_and_navigate(self):
        token={"chain":"base","contract_address":"x"}; config={"alpha":{"moralis_api_key":"k"}}
        payload=[{"owner_address":"a","balance_formatted":"-1"},{"owner_address":"b","balance_formatted":"1","owner_address_label":"navigate","is_contract":False}]
        with patch("alpha_pipeline.moralis_evm.get_top_holders",return_value=payload): rows=alpha_pipeline.fetch_holder_rows(token,config)
        self.assertEqual("wallet",rows[0]["holder_type"])

    def test_legacy_schema_migrates_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"legacy.db"); raw=sqlite3.connect(path)
            raw.execute("CREATE TABLE alpha_screen_snapshots(ts TEXT,token_key TEXT,PRIMARY KEY(ts,token_key))"); raw.execute("CREATE TABLE alpha_holder_snapshots(ts TEXT,token_key TEXT,address TEXT,PRIMARY KEY(ts,token_key,address))")
            raw.execute("INSERT INTO alpha_screen_snapshots VALUES('t','x')"); raw.execute("INSERT INTO alpha_holder_snapshots VALUES('t','x','a')"); raw.commit(); raw.close()
            first=dbm.connect(path); first.close(); second=dbm.connect(path)
            self.assertEqual(1,second.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0])
            columns=[r[1] for r in second.execute("PRAGMA table_info(alpha_screen_snapshots)")]
            self.assertIn("run_id",columns)
            self.assertIn("drawdown_from_listing_reference_pct",columns)
            indexes={r[1] for r in second.execute("PRAGMA index_list(alpha_screen_snapshots)")}; self.assertIn("idx_alpha_screen_run_token",indexes); second.close()

    def test_failed_same_timestamp_does_not_replace_complete(self):
        dbm.upsert_alpha_universe(self.conn,{"token_key":"base:x","symbol":"X","chain":"base","contract_address":"x","alpha_symbol":"XUSDT","futures_symbol":"XUSDT"})
        dbm.start_alpha_screen_run(self.conn,"complete","same","test",1,False); dbm.finish_alpha_screen_run(self.conn,"complete","complete",{"eligible":1,"snapshot":1},[])
        for run,status in [("failed","failed"),("running","running")]:
            dbm.start_alpha_screen_run(self.conn,run,"same","test",1,False)
            if status=="failed": dbm.finish_alpha_screen_run(self.conn,run,"failed",{},[])
        for run in ("complete","failed","running"):
            dbm.insert_alpha_screen_snapshot(self.conn,{"ts":f"same-{run}","token_key":"base:x","signal_label":"watch","passed_layer1":0,"run_id":run})
        self.assertEqual("same-complete",dbm.fetch_latest_alpha_snapshot(self.conn)[0]["ts"])

    def test_second_screen_insert_failure_rolls_back_new_run(self):
        for key in ("base:old","base:a","base:b"):
            dbm.upsert_alpha_universe(self.conn,{"token_key":key,"symbol":key[-1],"chain":"base","contract_address":key,"alpha_symbol":"XUSDT","futures_symbol":"XUSDT","primary_pool_id":"base/p"})
        dbm.start_alpha_screen_run(self.conn,"previous","old","test",1,False); dbm.finish_alpha_screen_run(self.conn,"previous","complete",{"eligible":1,"snapshot":1},[])
        dbm.insert_alpha_screen_snapshot(self.conn,{"ts":"old","token_key":"base:old","signal_label":"watch","passed_layer1":0,"run_id":"previous"}); self.conn.commit()
        tokens=[{"token_key":"base:a","chain":"base","contract_address":"a","alpha_symbol":"XUSDT","futures_symbol":"XUSDT","primary_pool_id":"base/p"},{"token_key":"base:b","chain":"base","contract_address":"b","alpha_symbol":"XUSDT","futures_symbol":"XUSDT","primary_pool_id":"base/p"}]
        inputs={"ready":True,"failure_reason":None,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"verified","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        original=dbm.insert_alpha_screen_snapshot; calls=[]
        def fail_second(conn,row):
            calls.append(row)
            if len(calls)==2: raise sqlite3.OperationalError("injected")
            original(conn,row)
        with patch("alpha_pipeline.sync_alpha_universe"), patch("alpha_pipeline.fetch_alpha_rows",return_value=tokens), patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs), patch("alpha_pipeline.dbm.insert_alpha_screen_snapshot",side_effect=fail_second):
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,"new",force=True)
        self.assertEqual("failed",result["status"])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots WHERE run_id=?",(result["run_id"],)).fetchone()[0])
        self.assertEqual("failed",dbm.latest_alpha_run(self.conn)["status"])
        self.assertEqual("old",dbm.fetch_latest_alpha_snapshot(self.conn)[0]["ts"])

    def test_synced_failed_run_throttles_next_universe_sync(self):
        token={"token_key":"base:a","chain":"base","contract_address":"a","alpha_symbol":"XUSDT","futures_symbol":"XUSDT","primary_pool_id":"base/p"}
        inputs={"ready":True,"failure_reason":None,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"verified","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        original=dbm.insert_alpha_screen_snapshot
        with patch("alpha_pipeline.sync_alpha_universe") as sync, patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]), patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs), patch("alpha_pipeline.dbm.insert_alpha_screen_snapshot",side_effect=sqlite3.OperationalError("fail")):
            first=alpha_pipeline.refresh_alpha(self.conn,self.config,"one",force=True)
        self.assertEqual("failed",first["status"]); self.assertEqual(1,sync.call_count)
        with patch("alpha_pipeline.sync_alpha_universe") as sync2, patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]), patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs):
            alpha_pipeline.refresh_alpha(self.conn,self.config,"two",force=True)
        sync2.assert_not_called()

    def test_sync_exception_records_failed_claim_without_snapshots(self):
        def partial_sync(conn, config):
            dbm.upsert_alpha_universe(conn,{"token_key":"base:partial","symbol":"P","chain":"base","contract_address":"partial","alpha_symbol":"PUSDT","futures_symbol":"PUSDT","primary_pool_id":"base/p"})
            raise RuntimeError("secret payload")
        with patch("alpha_pipeline.sync_alpha_universe",side_effect=partial_sync):
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,"sync-fail",force=True)
        run=self.conn.execute("SELECT status,error_summary FROM alpha_screen_runs WHERE run_id=?",(result["run_id"],)).fetchone()
        self.assertEqual(("failed","RuntimeError"),run)
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM alpha_screen_snapshots").fetchone()[0])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM alpha_universe WHERE token_key='base:partial'").fetchone()[0])

    def test_holder_attempt_outcomes_are_throttled_after_complete(self):
        token={"token_key":"base:p","chain":"base","contract_address":"p","alpha_symbol":"PUSDT","futures_symbol":"PUSDT","primary_pool_id":"base/p"}
        inputs={"ready":True,"failure_reason":None,"baseline_volumes":[3,3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"v","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        for outcome, effect in [("success",[{"address":"a","balance":1,"pct_supply":None,"holder_type":"wallet","is_excluded":0}]),("success",[]),("failed",RuntimeError("down"))]:
            with self.subTest(outcome=outcome):
                conn=dbm.connect(":memory:"); config={**self.config,"alpha":{**self.config["alpha"],"moralis_api_key":"k"}}
                holder_patch = patch("alpha_pipeline.fetch_holder_rows", side_effect=effect) if isinstance(effect, Exception) else patch("alpha_pipeline.fetch_holder_rows", return_value=effect)
                with patch("alpha_pipeline.sync_alpha_universe"),patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),holder_patch as holders,patch("alpha_pipeline.binance_derivatives.get_funding_rate",return_value=.01),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=2),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=3):
                    first=alpha_pipeline.refresh_alpha(conn,config,"one",force=True); second=alpha_pipeline.refresh_alpha(conn,config,"two",force=True)
                self.assertEqual("complete",first["status"], first); self.assertEqual(1,holders.call_count)
                self.assertEqual(outcome,conn.execute("SELECT outcome FROM alpha_holder_refresh_state").fetchone()[0]); conn.close()

    def test_failed_publish_does_not_advance_holder_state(self):
        token={"token_key":"base:p","chain":"base","contract_address":"p","alpha_symbol":"PUSDT","futures_symbol":"PUSDT","primary_pool_id":"base/p"}
        inputs={"ready":True,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"v","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        config={**self.config,"alpha":{**self.config["alpha"],"moralis_api_key":"k"}}
        with patch("alpha_pipeline.sync_alpha_universe"),patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),patch("alpha_pipeline.fetch_holder_rows",return_value=[]),patch("alpha_pipeline.binance_derivatives.get_funding_rate",return_value=.01),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=2),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=3),patch("alpha_pipeline.dbm.insert_alpha_screen_snapshot",side_effect=sqlite3.OperationalError("fail")):
            result=alpha_pipeline.refresh_alpha(self.conn,config,"fail",force=True)
        self.assertEqual("failed",result["status"])
        self.assertEqual(0,self.conn.execute("SELECT COUNT(*) FROM alpha_holder_refresh_state").fetchone()[0])

    def test_futures_enrichment_is_required_for_passed_tokens(self):
        token={"token_key":"base:f","chain":"base","contract_address":"f","alpha_symbol":"FUSDT","futures_symbol":"FUSDT","primary_pool_id":"base/p"}
        inputs={"ready":True,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"v","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        with patch("alpha_pipeline.sync_alpha_universe"),patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),patch("alpha_pipeline.fetch_holder_rows",return_value=[]),patch("alpha_pipeline.binance_derivatives.get_funding_rate",return_value=.01),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=2),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=3):
            result=alpha_pipeline.refresh_alpha(self.conn,self.config,"f",force=True)
        row=self.conn.execute("SELECT funding_rate,open_interest_usd FROM alpha_screen_snapshots WHERE run_id=?",(result["run_id"],)).fetchone(); self.assertEqual((.01,6),row)
        with patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),patch("alpha_pipeline.fetch_holder_rows",return_value=[]),patch("alpha_pipeline.binance_derivatives.get_funding_rate",side_effect=RuntimeError("provider down")),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=2),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=3):
            failed_futures=alpha_pipeline.refresh_alpha(self.conn,self.config,"f-fail",force=True)
        self.assertEqual("complete",failed_futures["status"])
        row=self.conn.execute("SELECT funding_rate,open_interest_usd,passed_layer1,signal_label,extra_json FROM alpha_screen_snapshots WHERE run_id=?",(failed_futures["run_id"],)).fetchone()
        self.assertEqual((None,None,0,"watch"),row[:4])
        self.assertIn("futures_unavailable:RuntimeError",row[4])
        self.assertIn("futures:base:f:RuntimeError",failed_futures["warnings"])

    def test_invalid_futures_metrics_demote_to_watch(self):
        token={"token_key":"base:f","chain":"base","contract_address":"f","alpha_symbol":"FUSDT","futures_symbol":"FUSDT","primary_pool_id":"base/p"}
        inputs={"ready":True,"baseline_volumes":[3],"current_volume":6,"price_usd":.1,"listing_reference_open_price":2,"ath_price":3,"market_cap_usd":10,"market_cap_confidence":"v","liquidity_usd":1,"price_above_range":True,"compression_score":0,"provenance":{}}
        invalid_sets=[(None,2,3),(float("nan"),2,3),(.01,float("inf"),3),(.01,0,3),(.01,2,-1)]
        for funding,oi,mark in invalid_sets:
            with self.subTest(funding=funding,oi=oi,mark=mark):
                conn=dbm.connect(":memory:")
                with patch("alpha_pipeline.sync_alpha_universe"),patch("alpha_pipeline.fetch_alpha_rows",return_value=[token]),patch("alpha_pipeline.fetch_screen_inputs",return_value=inputs),patch("alpha_pipeline.binance_derivatives.get_funding_rate",return_value=funding),patch("alpha_pipeline.binance_derivatives.get_open_interest",return_value=oi),patch("alpha_pipeline.binance_derivatives.get_mark_price",return_value=mark):
                    result=alpha_pipeline.refresh_alpha(conn,self.config,"invalid",force=True)
                row=conn.execute("SELECT passed_layer1,signal_label,funding_rate,open_interest_usd,extra_json FROM alpha_screen_snapshots WHERE run_id=?",(result["run_id"],)).fetchone()
                self.assertEqual((0,"watch",None,None),row[:4])
                self.assertIn("futures_unavailable:invalid_metrics",row[4])
                self.assertEqual(["futures:base:f:invalid_metrics"],result["warnings"])
                conn.close()

    def test_holder_parser_classification_and_invalid_rows(self):
        token={"chain":"base","contract_address":"x"}; config={"alpha":{"moralis_api_key":"k"}}
        rows=[("0x000000000000000000000000000000000000dead","",False,"burn"),("a","Bridge",False,"bridge"),("b","Treasury vesting",False,"treasury_vesting"),("c","LP pool",False,"liquidity_pool"),("d","OKX Gate.io",False,"exchange"),("e","",True,"contract_unknown"),("f","Alpaca navigate",False,"wallet")]
        payload=[{"owner_address":a,"owner_address_label":label,"balance_formatted":"1","percentage_relative_to_total_supply":"1","is_contract":contract} for a,label,contract,_ in rows]+[{"owner_address":"bad","balance":"9"},{"owner_address":"nan","balance_formatted":"NaN"},{"owner_address":"neg","balance_formatted":"-1"},{"owner_address":"pct","balance_formatted":"1","percentage_relative_to_total_supply":"101"}]
        with patch("alpha_pipeline.moralis_evm.get_top_holders",return_value=payload): parsed=alpha_pipeline.fetch_holder_rows(token,config)
        self.assertEqual([expected for *_,expected in rows],[row["holder_type"] for row in parsed])


if __name__ == "__main__": unittest.main()
