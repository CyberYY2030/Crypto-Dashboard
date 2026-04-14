from __future__ import annotations

import pandas as pd
import streamlit as st

from utils import load_config, fmt_price, fmt_pct, fmt_money
import db as dbm

import subprocess, sys

if st.button("更新数据（抓取并入库）"):
    subprocess.run([sys.executable, "run_daily.py"], check=False)
    st.rerun()
    
st.set_page_config(page_title="Crypto Daily Dashboard (BTC/ETH)", layout="wide")

config = load_config()
conn = dbm.connect(config.get("db_path", "crypto_dashboard.db"))

latest = dbm.fetch_latest_date(conn)
st.title("Crypto Daily Dashboard (BTC/ETH)")

if not latest:
    st.warning("No data yet. Run: `python run_daily.py` first (it will backfill ~1 year of data).")
    st.stop()

metrics = dbm.fetch_metrics_for_date(conn, latest)
st.caption(f"Latest data date (Asia/Shanghai): {latest}")

# ---- Top cards ----
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
    st.metric("BTC RV30 (ann.)", fmt_pct(rv30*100) if rv30 is not None else "—")
    st.metric("RV30 1Y Percentile", f"{rv_pct:.0f}" if rv_pct is not None else "—")
with c4:
    st.subheader("Sentiment")
    fg = metrics.get("fear_greed")
    st.metric("Fear & Greed", f"{fg:.0f}" if fg is not None else "—")

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

# ---- Charts (last 3 months) ----
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
        df = pd.DataFrame({"date": [d for d, _ in rv_series], "RV30": [v*100 for _, v in rv_series]})
        df["date"] = pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("BTC RV30 series not available yet. Run `python run_daily.py` once to backfill.")

left2, right2 = st.columns(2)

with left2:
    st.markdown("### DeFi TVL (3M) (B USD)")
    tvl = dbm.fetch_metric_series(conn, "defi_tvl", limit=days)
    if tvl:
        df = pd.DataFrame({"date": [d for d, _ in tvl], "TVL_B": [v/1e9 for _, v in tvl]})
        df["date"] = pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("TVL series not available yet. Run `python run_daily.py` once to backfill.")

with right2:
    st.markdown("### Stablecoin Mcap (3M) (B USD)")
    sc = dbm.fetch_metric_series(conn, "stablecoin_mcap_usd", limit=days)
    if sc:
        df = pd.DataFrame({"date": [d for d, _ in sc], "Stablecoins_B": [v/1e9 for _, v in sc]})
        df["date"] = pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("Stablecoin series not available yet. Run `python run_daily.py` once to backfill.")

st.divider()

st.subheader("ETF flows (optional, requires SoSoValue API key)")

st.markdown("### BTC/ETH ETF Total Net Inflow (3M) [SoSoValue](M USD)")
c1, c2 = st.columns(2)
with c1:
    btc_in = dbm.fetch_metric_series(conn, "btc_etf_totalNetInflow_usd", limit=days)
    if btc_in:
        df = pd.DataFrame({"date":[d for d,_ in btc_in], "BTC_ETF_NetInflow":[v/1e6 for _,v in btc_in]})
        df["date"]=pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("BTC ETF inflow series not available (set SOSO_API_KEY).")
with c2:
    eth_in = dbm.fetch_metric_series(conn, "eth_etf_totalNetInflow_usd", limit=days)
    if eth_in:
        df = pd.DataFrame({"date":[d for d,_ in eth_in], "ETH_ETF_NetInflow":[v/1e6 for _,v in eth_in]})
        df["date"]=pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("ETH ETF inflow series not available (set SOSO_API_KEY).")


st.markdown("### BTC/ETH ETF Total Net Assets (3M) [SoSoValue] (B USD)")
c3, c4 = st.columns(2)
with c3:
    btc_tna = dbm.fetch_metric_series(conn, "btc_etf_total_net_assets_usd", limit=days)
    if btc_tna:
        df = pd.DataFrame({"date":[d for d,_ in btc_tna], "BTC_ETF_TNA_B":[v/1e9 for _,v in btc_tna]})
        df["date"]=pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("BTC ETF TNA series not available (set SOSO_API_KEY).")
with c4:
    eth_tna = dbm.fetch_metric_series(conn, "eth_etf_total_net_assets_usd", limit=days)
    if eth_tna:
        df = pd.DataFrame({"date":[d for d,_ in eth_tna], "ETH_ETF_TNA_B":[v/1e9 for _,v in eth_tna]})
        df["date"]=pd.to_datetime(df["date"])
        st.line_chart(df.set_index("date"))
    else:
        st.info("ETH ETF TNA series not available (set SOSO_API_KEY).")



st.caption("Set env var SOSO_API_KEY to enable. Endpoint: POST /openapi/v2/etf/currentEtfDataMetrics (type: us-btc-spot/us-eth-spot).")
btc_etf = metrics.get("btc_etf_daily_net_inflow_usd")
eth_etf = metrics.get("eth_etf_daily_net_inflow_usd")
c9, c10, c11 = st.columns(3)
with c9:
    st.metric("BTC ETF Daily Net Inflow", fmt_money(btc_etf))
with c10:
    st.metric("ETH ETF Daily Net Inflow", fmt_money(eth_etf))
with c11:
    st.metric("BTC ETF Total Net Assets", fmt_money(metrics.get("btc_etf_total_net_assets_usd")))

conn.close()
