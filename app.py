from __future__ import annotations

import subprocess
import sys
import datetime as dt
import json

import pandas as pd
import streamlit as st

import db as dbm
from utils import fmt_money, fmt_pct, fmt_price, load_config


def render_macro_dashboard(conn):
    truth = dbm.get_latest_metrics_date(conn)
    latest = truth["date"]
    latest_run = dbm.get_latest_collection_run(conn, "daily_macro")
    st.title("Crypto Daily Dashboard (BTC/ETH)")

    if st.button("更新数据（抓取并入库）", key="refresh_macro"):
        subprocess.run([sys.executable, "run_daily.py"], check=False)
        st.rerun()

    if latest_run:
        st.caption(
            f"Latest run: {latest_run['status']} | completed: {latest_run['completed_at'] or 'not completed'} | "
            f"core coverage: {len(latest_run['actual_core_metrics'])}/{len(latest_run['expected_core_metrics'])}"
        )
        if latest_run["warnings"]:
            st.warning("Run warnings: " + " | ".join(latest_run["warnings"]))
        if latest_run["status"] == "failed":
            st.error("The latest collection failed and was not published. The dashboard retains the last complete run.")

    if not latest:
        st.warning("No verified macro data yet. Run the canonical `.venv\\Scripts\\python.exe run_daily.py` command first.")
        return

    metrics = dbm.fetch_metrics_for_date(conn, latest)
    fg_detail = dbm.fetch_metric_detail_for_date(conn, latest, "fear_greed") or {}
    if truth["legacy_unverified"]:
        st.error(f"Legacy / unverified data date: {latest}. No complete daily_macro run ledger exists for this value.")
    else:
        try:
            age_days = max(0, (dt.date.today() - dt.date.fromisoformat(latest)).days)
        except ValueError:
            age_days = "unknown"
        st.caption(f"Verified latest complete data date (Asia/Shanghai): {latest} | age: {age_days} day(s)")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.subheader("BTC")
        st.metric("Price", fmt_price(metrics.get("btc_price")), fmt_pct(metrics.get("btc_change_24h_pct")))
        st.write(f"7D: {fmt_pct(metrics.get('btc_change_7d_pct'))}  |  30D: {fmt_pct(metrics.get('btc_change_30d_pct'))}")
    with c2:
        st.subheader("ETH")
        st.metric("Price", fmt_price(metrics.get("eth_price")), fmt_pct(metrics.get("eth_change_24h_pct")))
        st.write(f"7D: {fmt_pct(metrics.get('eth_change_7d_pct'))}  |  30D: {fmt_pct(metrics.get('eth_change_30d_pct'))}")
    with c3:
        st.subheader("Risk Regime")
        rv30 = metrics.get("btc_rv_30d")
        rv_pct = metrics.get("btc_rv30_pctile_1y")
        st.metric("BTC RV30 (ann.)", fmt_pct(rv30 * 100) if rv30 is not None else "—")
        st.metric("RV30 1Y Percentile", f"{rv_pct:.0f}" if rv_pct is not None else "—")
    with c4:
        st.subheader("Sentiment")
        fg = fg_detail.get("value", metrics.get("fear_greed"))
        st.metric("Fear & Greed", f"{fg:.0f}" if fg is not None else "—")
        fg_class = ((fg_detail.get("extra") or {}).get("class") or "").strip()
        if fg_class:
            st.caption(f"Classification: {fg_class}")

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        st.subheader("Derivatives (USD)")
        btc_oi_usd = metrics.get("btc_oi_usd")
        eth_oi_usd = metrics.get("eth_oi_usd")
        st.metric("BTC OI", f"${btc_oi_usd/1e9:,.2f}B" if btc_oi_usd is not None else "—")
        st.metric("ETH OI", f"${eth_oi_usd/1e9:,.2f}B" if eth_oi_usd is not None else "—")
    with c6:
        st.subheader("DeFi")
        st.metric("Total TVL", fmt_money(metrics.get("defi_tvl")))
    with c7:
        st.subheader("Stablecoins")
        st.metric("Mcap (pegged USD)", fmt_money(metrics.get("stablecoin_mcap_usd")))
    with c8:
        st.subheader("ahr999")
        ahr = metrics.get("ahr999")
        st.metric("ahr999", f"{ahr:.3f}" if ahr is not None else "—")

    st.divider()
    st.subheader("Last 3 months trends")
    days = 92

    left, right = st.columns(2)
    with left:
        st.markdown("### Fear & Greed (3M)")
        fg_series = dbm.fetch_metric_series(conn, "fear_greed", limit=days)
        if fg_series:
            df = pd.DataFrame({"date": [d for d, _ in fg_series], "FearGreed": [v for _, v in fg_series]})
            df["date"] = pd.to_datetime(df["date"])
            st.line_chart(df.set_index("date"))
        else:
            st.info("Fear & Greed series not available yet. Run `python run_daily.py` once to backfill.")
    with right:
        st.markdown("### BTC RV30 (3M)")
        rv_series = dbm.fetch_metric_series(conn, "btc_rv_30d", limit=days)
        if rv_series:
            df = pd.DataFrame({"date": [d for d, _ in rv_series], "RV30": [v * 100 for _, v in rv_series]})
            df["date"] = pd.to_datetime(df["date"])
            st.line_chart(df.set_index("date"))
        else:
            st.info("BTC RV30 series not available yet. Run `python run_daily.py` once to backfill.")

    left2, right2 = st.columns(2)
    with left2:
        st.markdown("### DeFi TVL (3M) (B USD)")
        tvl = dbm.fetch_metric_series(conn, "defi_tvl", limit=days)
        if tvl:
            df = pd.DataFrame({"date": [d for d, _ in tvl], "TVL_B": [v / 1e9 for _, v in tvl]})
            df["date"] = pd.to_datetime(df["date"])
            st.line_chart(df.set_index("date"))
        else:
            st.info("TVL series not available yet. Run `python run_daily.py` once to backfill.")
    with right2:
        st.markdown("### Stablecoin Mcap (3M) (B USD)")
        sc = dbm.fetch_metric_series(conn, "stablecoin_mcap_usd", limit=days)
        if sc:
            df = pd.DataFrame({"date": [d for d, _ in sc], "Stablecoins_B": [v / 1e9 for _, v in sc]})
            df["date"] = pd.to_datetime(df["date"])
            st.line_chart(df.set_index("date"))
        else:
            st.info("Stablecoin series not available yet. Run `python run_daily.py` once to backfill.")


def render_alpha_dashboard(conn):
    st.title("Alpha EVM Monitor")
    st.caption("Focus: small-cap Binance Alpha EVM tokens with futures support and early abnormal movement signals.")

    if st.button("刷新 Alpha 候选池", key="refresh_alpha"):
        subprocess.run([sys.executable, "run_alpha.py", "--force"], check=False)
        st.rerun()

    latest_run = dbm.latest_alpha_run(conn)
    published_run = dbm.latest_alpha_run(conn, complete_only=True)
    snapshot_rows = dbm.fetch_latest_alpha_snapshot(conn)
    if latest_run:
        st.caption(
            f"Latest attempt: {latest_run['status']} | current pool: {latest_run['current_pool_count']}/{latest_run['eligible_count']} "
            f"| target: {latest_run.get('target_count', 0)} | reference: {latest_run['reference_ready_count']} ready, {latest_run['reference_refreshed_count']} refreshed "
            f"| screen ready: {latest_run['ready_count']}/{latest_run.get('target_count', 0)} | error: {latest_run['error_summary'] or '—'}"
        )
        if latest_run["status"] == "incomplete":
            if latest_run["error_summary"] == "low_current_pool_ratio":
                st.warning("Latest attempt has insufficient current fixed-pool coverage; reference warming did not run and the dashboard retains the previous complete run.")
            elif latest_run["error_summary"] == "low_ready_ratio":
                st.warning("Latest attempt has insufficient target reference-ready coverage and was not published; the dashboard retains the previous complete run.")
            else:
                st.warning("Latest attempt was incomplete and was not published; the dashboard retains the previous complete run.")
        elif latest_run["status"] == "failed":
            st.error("Latest attempt was not published; the dashboard retains the previous complete run.")
    if published_run:
        st.caption(
            f"Published complete: {published_run['run_id']} | current pool: {published_run['current_pool_count']}/{published_run['eligible_count']} "
            f"| target: {published_run.get('target_count', 0)} | ready: {published_run['ready_count']}/{published_run.get('target_count', 0)} "
            f"| completed: {published_run['completed_at']}"
        )
        if published_run.get("target_count", 0) == 0:
            st.info("Complete, no target candidates.")
    if not snapshot_rows:
        if published_run and published_run.get("target_count", 0) == 0:
            return
        st.warning("No complete Alpha run. Legacy snapshots are unverified and are not shown.")
        return

    df = pd.DataFrame(snapshot_rows)
    def listing_offset(extra_json):
        try:
            return (json.loads(extra_json or "{}").get("provenance") or {}).get("listing_reference_day_offset_days")
        except (TypeError, ValueError):
            return None
    def failure_reason(extra_json):
        try:
            value = json.loads(extra_json or "{}").get("failure_reason")
        except (TypeError, ValueError):
            return "unparseable_extra_json"
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True)
        return None
    df["listing_reference_day_offset_days"] = df["extra_json"].map(listing_offset)
    df["failure_reason"] = df["extra_json"].map(failure_reason)
    df["data_ready"] = df["failure_reason"].isna()
    st.caption("Listing reference: Binance Web3 DEX first available daily candle open; it may be later than the listing UTC day.")
    passed_df = df[df["passed_layer1"] == 1]
    ready_df = df[df["data_ready"]]
    near_miss_df = ready_df[ready_df["passed_layer1"] == 0]
    data_gaps_df = df[~df["data_ready"]]
    alpha_cfg = config.get("alpha", {})
    market_cap = pd.to_numeric(df["market_cap_usd"], errors="coerce")
    drawdown = pd.to_numeric(df["drawdown_from_listing_reference_pct"], errors="coerce")
    ath_drawdown = pd.to_numeric(df["drawdown_from_ath_pct"], errors="coerce")
    volume = pd.to_numeric(df["volume_24h"], errors="coerce")
    volume_ratio = pd.to_numeric(df["volume_expansion_ratio"], errors="coerce")
    drawdown_threshold = alpha_cfg.get("drawdown_threshold_pct", 90)
    small_cap = df["data_ready"] & market_cap.lt(alpha_cfg.get("market_cap_limit_usd", 100000000))
    deep_drawdown = df["data_ready"] & (
        drawdown.ge(drawdown_threshold) | ath_drawdown.ge(drawdown_threshold)
    )
    volume_expansion = df["data_ready"] & volume.ge(alpha_cfg.get("volume_min_usd", 0)) & volume_ratio.ge(alpha_cfg.get("volume_expansion_ratio_min", 1))
    all_core_gates = small_cap & deep_drawdown & volume_expansion
    display_cols = [
        "symbol",
        "chain",
        "signal_label",
        "score",
        "price_usd",
        "volume_24h",
        "volume_expansion_ratio",
        "market_cap_usd",
        "market_cap_confidence",
        "drawdown_from_listing_reference_pct",
        "listing_reference_day_offset_days",
        "drawdown_from_ath_pct",
        "funding_rate",
        "open_interest_usd",
    ]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Snapshot Rows", len(df))
    c2.metric("Data Ready", len(ready_df))
    c3.metric("Formal Candidates", len(passed_df))
    c4.metric("Ready Near Misses", len(near_miss_df))
    c5.metric("Data Gaps", len(data_gaps_df))
    st.caption(
        "Independent gate counts use the configured thresholds; only All Core Gates is their combined count, "
        "so these are not a funnel. Drawdown accepts either listing reference or ATH."
    )
    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("Data Ready", int(df["data_ready"].sum()))
    g2.metric("Small-cap", int(small_cap.sum()))
    g3.metric(f"{drawdown_threshold:g}% Drawdown", int(deep_drawdown.sum()))
    g4.metric("Volume Expansion", int(volume_expansion.sum()))
    g5.metric("All Core Gates", int(all_core_gates.sum()))

    st.markdown("### First Volume Breakout")
    breakout_df = passed_df[passed_df["signal_label"] == "first_volume_breakout"]
    if breakout_df.empty:
        st.info("No breakout candidates in the latest snapshot.")
    else:
        st.dataframe(breakout_df[display_cols], use_container_width=True)

    st.markdown("### Post-Compression Confirmation")
    confirm_df = passed_df[passed_df["signal_label"] == "post_compression_confirmation"]
    if confirm_df.empty:
        st.info("No confirmation candidates in the latest snapshot.")
    else:
        st.dataframe(confirm_df[display_cols], use_container_width=True)

    st.markdown("### All Passed Candidates")
    if passed_df.empty:
        st.info("No formal candidates in the latest snapshot. Watch the near-miss table below.")
    else:
        st.dataframe(passed_df[display_cols], use_container_width=True)

    st.markdown("### Latest Watchlist / Near Misses")
    if near_miss_df.empty:
        st.info("No data-ready near misses in the latest snapshot.")
    else:
        st.dataframe(near_miss_df[display_cols + ["passed_layer1"]], use_container_width=True)

    st.markdown("### Data Gaps")
    if data_gaps_df.empty:
        st.info("No data gaps in the latest snapshot.")
    else:
        st.dataframe(data_gaps_df[["symbol", "chain", "failure_reason", "price_usd", "volume_24h", "market_cap_usd"]], use_container_width=True)


st.set_page_config(page_title="Crypto Daily Dashboard", layout="wide")

config = load_config()
conn = dbm.connect(config.get("db_path", "crypto_dashboard.db"))

tabs = st.tabs(["Macro Dashboard", "Alpha EVM Monitor"])

with tabs[0]:
    render_macro_dashboard(conn)

with tabs[1]:
    render_alpha_dashboard(conn)

conn.close()
