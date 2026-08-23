from __future__ import annotations

import datetime as dt
from statistics import median
from typing import Any, Dict, List, Tuple

import requests

BASE = "https://api.geckoterminal.com/api/v2"
NETWORK_ALIASES = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon": "polygon_pos",
    "polygon_pos": "polygon_pos",
    "avalanche": "avax",
    "avax": "avax",
}


def split_pool_ref(pool_ref: str) -> Tuple[str, str]:
    chain_id, pool_address = pool_ref.split("/", 1)
    network = NETWORK_ALIASES.get(chain_id, chain_id)
    return network, pool_address


def get_pool_ohlcv(pool_ref: str, timeframe: str = "day", aggregate: int = 1, limit: int = 8) -> Dict[str, Any]:
    network, pool_address = split_pool_ref(pool_ref)
    r = requests.get(
        f"{BASE}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}",
        params={"aggregate": aggregate, "limit": limit},
        headers={"accept": "application/json"},
        timeout=25,
    )
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json() or {}


def extract_daily_volume_context(payload: Dict[str, Any], baseline_days: int = 7) -> Tuple[float | None, List[float]]:
    candles = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    if not candles:
        return None, []
    ordered = sorted(candles, key=lambda row: int(row[0]))
    volumes = [float(row[5]) for row in ordered if len(row) >= 6 and row[5] is not None]
    if not volumes:
        return None, []
    current_volume = volumes[-1]
    baseline = list(reversed(volumes[-(baseline_days + 1) : -1])) if len(volumes) > 1 else []
    return current_volume, baseline


def extract_completed_daily_candles(payload: Dict[str, Any], now_utc: dt.datetime | None = None) -> List[Dict[str, float]]:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    today = now_utc.date()
    rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = []
    for row in rows:
        if len(row) < 6 or any(value is None for value in row[:6]):
            continue
        candle_date = dt.datetime.fromtimestamp(int(row[0]), tz=dt.timezone.utc).date()
        if candle_date >= today:
            continue
        candles.append({"ts": int(row[0]), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
    return sorted(candles, key=lambda candle: candle["ts"])


def compute_completed_daily_features(
    payload: Dict[str, Any], baseline_days: int, breakout_lookback_days: int, compression_lookback_days: int,
    now_utc: dt.datetime | None = None,
) -> Dict[str, Any]:
    candles = extract_completed_daily_candles(payload, now_utc)
    baseline_candles = candles[-baseline_days:]
    baseline_volumes = [candle["volume"] for candle in baseline_candles]
    breakout_candles = candles[-breakout_lookback_days:]
    previous_high = max((candle["high"] for candle in breakout_candles), default=None)
    compression_score = None
    needed = compression_lookback_days * 2
    if len(candles) >= needed:
        prior = candles[-needed:-compression_lookback_days]
        recent = candles[-compression_lookback_days:]
        def range_ratio(candle: Dict[str, float]) -> float:
            return (candle["high"] - candle["low"]) / candle["close"] if candle["close"] > 0 else 0.0
        prior_median = median(range_ratio(candle) for candle in prior)
        recent_median = median(range_ratio(candle) for candle in recent)
        compression_score = max(0.0, min(1.0, 1.0 - (recent_median / prior_median))) if prior_median > 0 else None
    return {
        "completed_candle_count": len(candles), "baseline_volumes": baseline_volumes,
        "baseline_volume_median": float(median(baseline_volumes)) if len(baseline_volumes) >= baseline_days else None,
        "previous_high": previous_high, "compression_score": compression_score,
    }
