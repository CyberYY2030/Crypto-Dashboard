from __future__ import annotations

import datetime as dt
import os
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import numpy as np

from utils import load_config, shanghai_today, realized_vol_from_prices, fmt_price, fmt_pct, fmt_money, alert_style, rolling_percentile
import db as dbm

from collectors import coingecko, defillama, feargreed, binance_derivatives, binance_spot
from collectors.sosovalue import get_api_key as get_soso_key, current_etf_data_metrics, historical_inflow_chart
from telegram_alert import send_message, get_token_from_env, get_chat_id_from_env


def log_soso(msg: str):
    import os, datetime as dt
    os.makedirs("logs", exist_ok=True)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join("logs","sosovalue.log"), "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

BTC_GENESIS = dt.date(2009, 1, 3)

def _as_date_str(d: dt.date) -> str:
    return d.isoformat()

def _date_from_ts_ms(ts_ms: int) -> dt.date:
    return dt.datetime.utcfromtimestamp(ts_ms/1000).date()

def _date_from_ts_s(ts_s: int) -> dt.date:
    return dt.datetime.utcfromtimestamp(ts_s).date()

def compute_ahr999(price_series: pd.Series) -> pd.Series:
    """Compute ahr999 using original formula:
    ahr999 = (P / GMA200) * (P / IGV)
    GMA200: geometric mean of last 200 days price (in USD)
    IGV: 10^(5.84*log10(age_days) - 17.01), age_days since 2009-01-03 (BTC genesis)
    """
    # geometric mean rolling
    # gmean = exp(mean(log(p)))
    logp = price_series.apply(lambda x: float('nan') if x is None or x <= 0 else float(np.log(x)))
    gma200 = (logp.rolling(200).mean()).apply(lambda x: float('nan') if pd.isna(x) else float(np.exp(x)))

    dates = price_series.index
    age_days = pd.Series([(d.date() - BTC_GENESIS).days for d in dates], index=dates).astype(float)
    # Avoid <=0
    age_days = age_days.clip(lower=1.0)
    igv = (10 ** (5.84 * np.log10(age_days) - 17.01)).astype(float)

    ahr = (price_series / gma200) * (price_series / igv)
    return ahr

def ensure_price_history(conn, symbol: str, binance_symbol: str, days: int):
    """Backfill daily closes using Binance spot klines (no key). Only inserts missing dates."""
    series = binance_spot.backfill_daily_closes(binance_symbol, days=days)
    if not series:
        return
    existing = dbm.fetch_existing_dates_for_prices(conn, symbol)
    for ds, close in series:
        if ds in existing:
            continue
        dbm.upsert_price(conn, ds, symbol, float(close), None, None, None, "binance")

def upsert_metric_series(conn, metric: str, series: List[Tuple[str, float]], source: str):
    existing = dbm.fetch_existing_dates_for_metric(conn, metric)
    for ds, val in series:
        if ds in existing:
            continue
        dbm.upsert_metric(conn, ds, metric, float(val) if val is not None else None, source)

def backfill_fear_greed(conn, limit: int = 400):
    data = feargreed.get_history(limit=limit)
    # API returns newest first. Each item: {"value":"74","value_classification":"Greed","timestamp":"170xxx","time_until_update":"..."}
    existing = dbm.fetch_existing_dates_for_metric(conn, "fear_greed")
    for item in data:
        try:
            ts = int(item.get("timestamp"))
            d = _date_from_ts_s(ts)
            ds = d.isoformat()
            if ds in existing:
                continue
            val = float(item.get("value")) if item.get("value") is not None else None
            cls = item.get("value_classification")
            dbm.upsert_metric(conn, ds, "fear_greed", val, "alternative.me", {"class": cls})
        except Exception:
            continue

def backfill_defillama(conn, days: int = 370):
    # TVL
    tvl_hist = defillama.get_defi_tvl_history()
    if tvl_hist:
        existing = dbm.fetch_existing_dates_for_metric(conn, "defi_tvl")
        for row in tvl_hist[-(days+5):]:
            try:
                d = _date_from_ts_s(int(row.get("date")))
                ds = d.isoformat()
                if ds in existing:
                    continue
                tvl_val = float(row.get("totalLiquidityUSD", row.get("tvl")))
                dbm.upsert_metric(conn, ds, "defi_tvl", tvl_val, "defillama")
            except Exception:
                continue
    # Stablecoins
    sc_hist = defillama.get_stablecoin_charts_all()
    if sc_hist:
        existing = dbm.fetch_existing_dates_for_metric(conn, "stablecoin_mcap_usd")
        for row in sc_hist[-(days+5):]:
            try:
                d = _date_from_ts_s(int(row.get("date")))
                ds = d.isoformat()
                if ds in existing:
                    continue
                sc_total = defillama.extract_stablecoin_total_usd(row)
                if sc_total is None:
                    # Some schemas use "totalCirculatingUSD"
                    if "totalCirculatingUSD" in row:
                        sc_total = float(row["totalCirculatingUSD"])
                dbm.upsert_metric(conn, ds, "stablecoin_mcap_usd", sc_total, "defillama")
            except Exception:
                continue

def compute_derived_metrics(conn, today: dt.date):
    # Load last ~650 days of BTC prices to compute ahr999 and RV
    btc = pd.read_sql_query("SELECT date, close FROM prices WHERE symbol='BTC' AND close IS NOT NULL ORDER BY date", conn)
    if btc.empty:
        return
    btc["date"] = pd.to_datetime(btc["date"])
    btc = btc.set_index("date")["close"].astype(float)

    eth = pd.read_sql_query("SELECT date, close FROM prices WHERE symbol='ETH' AND close IS NOT NULL ORDER BY date", conn)
    if not eth.empty:
        eth["date"] = pd.to_datetime(eth["date"])
        eth = eth.set_index("date")["close"].astype(float)
    else:
        eth = None

    # Compute rolling returns and RV for last 370 days
    end = pd.to_datetime(today.isoformat())
    start = end - pd.Timedelta(days=370)

    btc_slice = btc.loc[btc.index >= start]
    # 7d/30d returns
    btc_ret7 = (btc_slice / btc_slice.shift(7) - 1.0) * 100.0
    btc_ret30 = (btc_slice / btc_slice.shift(30) - 1.0) * 100.0

    # RV7/RV30: use log returns std over window, annualized sqrt(365)
    btc_logret = (btc_slice / btc_slice.shift(1)).apply(lambda x: float('nan') if x is None or x <= 0 else float(np.log(x)))
    btc_rv7 = btc_logret.rolling(7).std(ddof=1) * (365.0 ** 0.5)
    btc_rv30 = btc_logret.rolling(30).std(ddof=1) * (365.0 ** 0.5)

    # ahr999 needs 200d lookback so compute on full btc series then slice
    ahr = compute_ahr999(btc).loc[btc.index >= start]

    # store
    for idx, val in btc_ret7.dropna().items():
        ds = idx.date().isoformat()
        dbm.upsert_metric(conn, ds, "btc_change_7d_pct", float(val), "computed")
    for idx, val in btc_ret30.dropna().items():
        ds = idx.date().isoformat()
        dbm.upsert_metric(conn, ds, "btc_change_30d_pct", float(val), "computed")
    for idx, val in btc_rv7.dropna().items():
        ds = idx.date().isoformat()
        dbm.upsert_metric(conn, ds, "btc_rv_7d", float(val), "computed")
    for idx, val in btc_rv30.dropna().items():
        ds = idx.date().isoformat()
        dbm.upsert_metric(conn, ds, "btc_rv_30d", float(val), "computed")
    for idx, val in ahr.dropna().items():
        ds = idx.date().isoformat()
        dbm.upsert_metric(conn, ds, "ahr999", float(val), "computed")

    # ETH derived
    if eth is not None and not eth.empty:
        eth_slice = eth.loc[eth.index >= start]
        eth_ret7 = (eth_slice / eth_slice.shift(7) - 1.0) * 100.0
        eth_ret30 = (eth_slice / eth_slice.shift(30) - 1.0) * 100.0
        eth_logret = (eth_slice / eth_slice.shift(1)).apply(lambda x: float('nan') if x is None or x <= 0 else float(np.log(x)))
        eth_rv7 = eth_logret.rolling(7).std(ddof=1) * (365.0 ** 0.5)
        eth_rv30 = eth_logret.rolling(30).std(ddof=1) * (365.0 ** 0.5)
        for idx, val in eth_ret7.dropna().items():
            ds = idx.date().isoformat()
            dbm.upsert_metric(conn, ds, "eth_change_7d_pct", float(val), "computed")
        for idx, val in eth_ret30.dropna().items():
            ds = idx.date().isoformat()
            dbm.upsert_metric(conn, ds, "eth_change_30d_pct", float(val), "computed")
        for idx, val in eth_rv7.dropna().items():
            ds = idx.date().isoformat()
            dbm.upsert_metric(conn, ds, "eth_rv_7d", float(val), "computed")
        for idx, val in eth_rv30.dropna().items():
            ds = idx.date().isoformat()
            dbm.upsert_metric(conn, ds, "eth_rv_30d", float(val), "computed")

    # RV percentile regime (using btc_rv_30d trailing 365)
    rv_series = pd.read_sql_query("SELECT date, value FROM metrics WHERE metric='btc_rv_30d' AND value IS NOT NULL ORDER BY date", conn)
    if not rv_series.empty:
        rv_series["date"] = pd.to_datetime(rv_series["date"])
        rv_series = rv_series.set_index("date")["value"].astype(float)
        # compute percentile for last 370 days
        for i in range(len(rv_series)):
            pass
        # simple: percentile of latest within trailing 365 for today
        latest_val = float(rv_series.iloc[-1])
        tail = rv_series.iloc[-365:] if len(rv_series) >= 365 else rv_series
        pct = float((tail.rank(pct=True).iloc[-1]) * 100.0)
        ds = rv_series.index[-1].date().isoformat()
        dbm.upsert_metric(conn, ds, "btc_rv30_pctile_1y", pct, "computed")

def collect_today_snapshot(conn, config: Dict[str, Any], today: dt.date) -> Dict[str, Any]:
    date_str = today.isoformat()
    fiat = (config.get("display", {}) or {}).get("fiat", "usd")

    # Coin ids kept only for fallback
    btc_id = config.get("symbols", {}).get("coingecko", {}).get("btc_id", "bitcoin")
    eth_id = config.get("symbols", {}).get("coingecko", {}).get("eth_id", "ethereum")

    # Use Binance spot for current price & 24h change (no key).
    btc_sym = config.get("symbols", {}).get("binance_spot", {}).get("btc_symbol", "BTCUSDT")
    eth_sym = config.get("symbols", {}).get("binance_spot", {}).get("eth_symbol", "ETHUSDT")

    def _binance_pick(sym: str):
        try:
            t = binance_spot.get_24hr_ticker(sym)
            px = float(t.get("lastPrice")) if t.get("lastPrice") is not None else None
            ch24 = float(t.get("priceChangePercent")) if t.get("priceChangePercent") is not None else None
            vol_quote = float(t.get("quoteVolume")) if t.get("quoteVolume") is not None else None
            return px, ch24, vol_quote
        except Exception:
            return None, None, None

    btc_px, btc_24h, btc_vol = _binance_pick(btc_sym)
    eth_px, eth_24h, eth_vol = _binance_pick(eth_sym)

    # Fallback to CoinGecko simple price if Binance is blocked
    if btc_px is None or eth_px is None:
        cg = coingecko.get_simple_prices([btc_id, eth_id], vs=fiat)
        def pick(coin_id: str):
            d = cg.get(coin_id, {})
            px = float(d.get(fiat)) if d.get(fiat) is not None else None
            ch24 = float(d.get(f"{fiat}_24h_change")) if d.get(f"{fiat}_24h_change") is not None else None
            vol = float(d.get(f"{fiat}_24h_vol")) if d.get(f"{fiat}_24h_vol") is not None else None
            return px, ch24, vol
        if btc_px is None:
            btc_px, btc_24h, btc_vol = pick(btc_id)
        if eth_px is None:
            eth_px, eth_24h, eth_vol = pick(eth_id)

    # Store today's spot metrics
    dbm.upsert_metric(conn, date_str, "btc_price", btc_px, "binance")
    dbm.upsert_metric(conn, date_str, "btc_change_24h_pct", btc_24h, "binance")
    dbm.upsert_metric(conn, date_str, "eth_price", eth_px, "binance")
    dbm.upsert_metric(conn, date_str, "eth_change_24h_pct", eth_24h, "binance")

    # Binance Futures Open Interest (base + USD notional)
    if config.get("modules", {}).get("binance_oi", True):
        btc_fut = config.get("symbols", {}).get("binance", {}).get("btc_symbol", "BTCUSDT")
        eth_fut = config.get("symbols", {}).get("binance", {}).get("eth_symbol", "ETHUSDT")
        btc_oi = binance_derivatives.get_open_interest(btc_fut)
        eth_oi = binance_derivatives.get_open_interest(eth_fut)

        dbm.upsert_metric(conn, date_str, "btc_oi_base", btc_oi, "binance")
        dbm.upsert_metric(conn, date_str, "eth_oi_base", eth_oi, "binance")

        btc_oi_usd = (btc_oi * btc_px) if (btc_oi is not None and btc_px is not None) else None
        eth_oi_usd = (eth_oi * eth_px) if (eth_oi is not None and eth_px is not None) else None
        dbm.upsert_metric(conn, date_str, "btc_oi_usd", btc_oi_usd, "computed")
        dbm.upsert_metric(conn, date_str, "eth_oi_usd", eth_oi_usd, "computed")

    # SoSoValue ETF metrics (optional, requires API key)
    soso_key = get_soso_key() or (config.get('sosovalue', {}) or {}).get('api_key')
    if not soso_key:
        log_soso("SOSO_API_KEY missing (env var SOSO_API_KEY empty and config.sosovalue.api_key not set). ETF metrics skipped.")

    def _norm_date(x):
        # SoSoValue 可能返回 "YYYY-MM-DD" 或时间戳(毫秒/秒)；做个兼容
        if x is None:
            return None
        if isinstance(x, str) and len(x) >= 10 and x[4] == "-" and x[7] == "-":
            return x[:10]
        try:
            t = float(x)
            # 13位当毫秒
            if t > 1e12:
                t /= 1000.0
            return dt.datetime.utcfromtimestamp(t).date().isoformat()
        except Exception:
            return None

    if soso_key:

        # Backfill historical series (up to 300 days) into local DB
        for etf_type, prefix in [("us-btc-spot", "btc"), ("us-eth-spot", "eth")]:
            try:
                hist = historical_inflow_chart(etf_type, soso_key)
                if hist:
                    existing = dbm.fetch_existing_dates_for_metric(conn, f"{prefix}_etf_totalNetInflow_usd")
                    for row in hist:
                        ds = row.get("date")
                        if (not ds) or (ds in existing):
                            continue
                        dbm.upsert_metric(conn, ds, f"{prefix}_etf_totalNetInflow_usd",
                                          float(row.get("totalNetInflow")) if row.get("totalNetInflow") is not None else None,
                                          "sosovalue")
                        dbm.upsert_metric(conn, ds, f"{prefix}_etf_totalNetAssets_usd",
                                          float(row.get("totalNetAssets")) if row.get("totalNetAssets") is not None else None,
                                          "sosovalue")
                        # also store under snake_case metric name (used by dashboard)
                        dbm.upsert_metric(conn, ds, f"{prefix}_etf_total_net_assets_usd",
                                          float(row.get("totalNetAssets")) if row.get("totalNetAssets") is not None else None,
                                          "sosovalue")
            except Exception:
                pass

        # Current snapshot metrics (aggregate)
        try:
            j_btc = current_etf_data_metrics("us-btc-spot", soso_key)
            try:
                log_soso(f"BTC current metrics raw: {str(j_btc)[:300]}")
            except Exception:
                pass
            if j_btc and j_btc.get("code") == 0:
                data = j_btc.get("data", {}) or {}
                daily_inflow = (data.get("dailyNetInflow") or {}).get("value")
                cum_inflow = (data.get("cumNetInflow") or {}).get("value")
                tna = (data.get("totalNetAssets") or {}).get("value")
                dbm.upsert_metric(conn, date_str, "btc_etf_daily_net_inflow_usd", float(daily_inflow) if daily_inflow is not None else None, "sosovalue")
                dbm.upsert_metric(conn, date_str, "btc_etf_cum_net_inflow_usd", float(cum_inflow) if cum_inflow is not None else None, "sosovalue")
                dbm.upsert_metric(conn, date_str, "btc_etf_total_net_assets_usd", float(tna) if tna is not None else None, "sosovalue")
        except Exception as e:
            log_soso(f"ETF historical inflow chart failed: {e}")
            pass

        try:
            j_eth = current_etf_data_metrics("us-eth-spot", soso_key)
            try:
                log_soso(f"ETH current metrics raw: {str(j_eth)[:300]}")
            except Exception:
                pass
            if j_eth and j_eth.get("code") == 0:
                data = j_eth.get("data", {}) or {}
                daily_inflow = (data.get("dailyNetInflow") or {}).get("value")
                cum_inflow = (data.get("cumNetInflow") or {}).get("value")
                tna = (data.get("totalNetAssets") or {}).get("value")
                dbm.upsert_metric(conn, date_str, "eth_etf_daily_net_inflow_usd", float(daily_inflow) if daily_inflow is not None else None, "sosovalue")
                dbm.upsert_metric(conn, date_str, "eth_etf_cum_net_inflow_usd", float(cum_inflow) if cum_inflow is not None else None, "sosovalue")
                dbm.upsert_metric(conn, date_str, "eth_etf_total_net_assets_usd", float(tna) if tna is not None else None, "sosovalue")
        except Exception as e:
            log_soso(f"ETF historical inflow chart failed: {e}")
            pass

    return {
        "date": date_str,
        "btc_price": btc_px,
        "btc_24h": btc_24h,
        "eth_price": eth_px,
        "eth_24h": eth_24h,
    }

def maybe_send_alert(config: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
    if not snapshot:
        return False

    threshold = float(config.get("alert", {}).get("btc_daily_move_threshold", 7.0))
    btc_24h = snapshot.get("btc_24h")
    if btc_24h is None:
        return False
    if abs(float(btc_24h)) < threshold:
        return False

    token = get_token_from_env()
    chat_id = get_chat_id_from_env() or (config.get("telegram", {}) or {}).get("chat_id")

    if not token or not chat_id:
        print("ALERT TRIGGERED but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        return False

    date = snapshot.get("date")
    btc_price = snapshot.get("btc_price")
    eth_price = snapshot.get("eth_price")
    eth_24h = snapshot.get("eth_24h")

    flag = alert_style(float(btc_24h), threshold)

    msg = (
        f"{flag} <b>BTC 24H MOVE</b> {fmt_pct(float(btc_24h))}\n"
        f"<b>BTC</b> {fmt_price(btc_price)} | <b>ETH</b> {fmt_price(eth_price)} ({fmt_pct(eth_24h)})\n"
        f"<i>Date</i>: {date} (Asia/Shanghai)\n"
        f"<b>Action</b>: Check RV regime / OI / TVL / stablecoins."
    )
    send_message(token=token, chat_id=str(chat_id), text=msg, parse_mode="HTML")
    return True

def main():
    config = load_config()
    db_path = config.get("db_path", "crypto_dashboard.db")
    conn = dbm.connect(db_path)

    today = shanghai_today(config)

    # 1) Backfill core histories (1 year + lookback)
    fiat = (config.get("display", {}) or {}).get("fiat", "usd")
    btc_id = config.get("symbols", {}).get("coingecko", {}).get("btc_id", "bitcoin")
    eth_id = config.get("symbols", {}).get("coingecko", {}).get("eth_id", "ethereum")

    # Need ~650 days to compute ahr999 with 200d lookback for last year
    ensure_price_history(conn, "BTC", config.get("symbols", {}).get("binance_spot", {}).get("btc_symbol", "BTCUSDT"), days=650)
    ensure_price_history(conn, "ETH", config.get("symbols", {}).get("binance_spot", {}).get("eth_symbol", "ETHUSDT"), days=400)

    backfill_fear_greed(conn, limit=400)
    backfill_defillama(conn, days=400)

    conn.commit()

    # 2) Compute derived metrics (ahr999, RV, returns) using local stored data
    compute_derived_metrics(conn, today)
    conn.commit()

    # 3) Collect today's snapshot (24h change, OI, optional ETF)
    snapshot = collect_today_snapshot(conn, config,today)
    conn.commit()
    conn.close()
    if snapshot is None:
        print("[WARN] snapshot is None, skip alerts (check logs).")
        sent = False
    else:
        sent = maybe_send_alert(config, snapshot)
        print("Telegram alert sent." if sent else "No alert.")

if __name__ == "__main__":
    main()
