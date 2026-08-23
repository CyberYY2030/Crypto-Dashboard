from __future__ import annotations

import os
import time
import requests
from typing import Any, Dict, List, Tuple, Optional

BASE = "https://api.coingecko.com/api/v3"
CHAIN_TO_PLATFORM = {
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "binance-smart-chain",
    "binance-smart-chain": "binance-smart-chain",
    "arbitrum": "arbitrum-one",
    "arbitrum-one": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "optimistic-ethereum": "optimistic-ethereum",
    "polygon": "polygon-pos",
    "polygon-pos": "polygon-pos",
    "avalanche": "avalanche",
}


def _headers() -> Dict[str, str]:
    api_key = (os.getenv("COINGECKO_API_KEY") or "").strip()
    if not api_key:
        return {}
    return {"x-cg-demo-api-key": api_key}


def _get(url: str, params: Dict[str, Any] | None = None) -> Any:
    r = requests.get(url, params=params, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def platform_id_for_chain(chain: str) -> str:
    normalized = (chain or "").strip().lower()
    return CHAIN_TO_PLATFORM.get(normalized, normalized)

def get_simple_prices(ids: List[str], vs: str = "usd") -> Dict[str, Any]:
    # includes 24h change and 24h vol if asked
    params = {
        "ids": ",".join(ids),
        "vs_currencies": vs,
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_last_updated_at": "true",
    }
    return _get(f"{BASE}/simple/price", params=params)

def get_market_chart(coin_id: str, vs: str = "usd", days: int = 120) -> List[Tuple[int, float]]:
    # returns list of [timestamp_ms, price]
    data = _get(f"{BASE}/coins/{coin_id}/market_chart", params={"vs_currency": vs, "days": days, "interval": "daily"})
    return data.get("prices", [])

def get_ohlc(coin_id: str, vs: str = "usd", days: int = 90):
    # returns list of [timestamp_ms, open, high, low, close]
    return _get(f"{BASE}/coins/{coin_id}/ohlc", params={"vs_currency": vs, "days": days})

def compute_period_returns(daily_closes: List[float]) -> Dict[str, Optional[float]]:
    # daily_closes should be in ascending date order
    import math
    import numpy as np
    closes = [c for c in daily_closes if c is not None]
    if len(closes) < 35:
        return {"7d": None, "30d": None}
    def pct(cur, prev):
        if prev == 0:
            return None
        return (cur/prev - 1.0) * 100.0
    return {
        "7d": pct(closes[-1], closes[-8]) if len(closes) >= 8 else None,
        "30d": pct(closes[-1], closes[-31]) if len(closes) >= 31 else None,
    }

def latest_day_range_from_ohlc(ohlc_rows) -> Dict[str, Optional[float]]:
    # Pick last row
    if not ohlc_rows:
        return {"high": None, "low": None}
    last = ohlc_rows[-1]
    return {"high": float(last[2]), "low": float(last[3]), "close": float(last[4])}


def get_coin_market_fields(coin_id: str, vs_currency: str = "usd") -> Dict[str, Any]:
    rows = _get(
        f"{BASE}/coins/markets",
        params={
            "vs_currency": vs_currency,
            "ids": coin_id,
            "price_change_percentage": "24h",
        },
    )
    return rows[0] if rows else {}


def get_contract_coin(chain: str, contract_address: str) -> Dict[str, Any]:
    platform_id = platform_id_for_chain(chain)
    return _get(
        f"{BASE}/coins/{platform_id}/contract/{contract_address}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
    )


def get_contract_market_chart_range(
    chain: str,
    contract_address: str,
    vs_currency: str = "usd",
    days: int = 365,
) -> Dict[str, Any]:
    platform_id = platform_id_for_chain(chain)
    now_ts = int(time.time())
    from_ts = now_ts - max(int(days), 1) * 86400
    return _get(
        f"{BASE}/coins/{platform_id}/contract/{contract_address}/market_chart/range",
        params={
            "vs_currency": vs_currency,
            "from": from_ts,
            "to": now_ts,
            "precision": "full",
        },
    )


def extract_contract_coin_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    market_data = payload.get("market_data") or {}
    ath_price = (market_data.get("ath") or {}).get("usd")
    market_cap_usd = (market_data.get("market_cap") or {}).get("usd")
    return {
        "ath_price": float(ath_price) if ath_price not in (None, "") else None,
        "market_cap_usd": float(market_cap_usd) if market_cap_usd not in (None, "") else None,
        "market_cap_confidence": "verified" if market_cap_usd not in (None, "") else None,
    }


def extract_contract_market_context(payload: Dict[str, Any], baseline_days: int = 1) -> Dict[str, Any]:
    prices = payload.get("prices") or []
    total_volumes = payload.get("total_volumes") or []
    price_values = [float(row[1]) for row in prices if isinstance(row, list) and len(row) >= 2 and row[1] is not None]
    volume_values = [
        float(row[1]) for row in total_volumes if isinstance(row, list) and len(row) >= 2 and row[1] is not None
    ]
    current_volume = volume_values[-1] if volume_values else None
    baseline_volumes = list(reversed(volume_values[-(baseline_days + 1) : -1])) if len(volume_values) >= 2 else []
    return {
        "ath_price": max(price_values) if price_values else None,
        "current_volume": current_volume,
        "baseline_volumes": baseline_volumes,
    }
