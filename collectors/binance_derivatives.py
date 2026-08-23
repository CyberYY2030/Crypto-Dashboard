from __future__ import annotations

import requests
from typing import Any, Dict, Optional

BASE = "https://fapi.binance.com"  # USD-M futures

def _get(path: str, params: Dict[str, Any]) -> Any:
    r = requests.get(f"{BASE}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_open_interest(symbol: str) -> Optional[float]:
    # GET /fapi/v1/openInterest {symbol}
    j = _get("/fapi/v1/openInterest", {"symbol": symbol})
    # {"openInterest":"12345.678","symbol":"BTCUSDT","time":...}
    try:
        return float(j.get("openInterest"))
    except Exception:
        return None


def list_futures_symbols() -> set[str]:
    j = _get("/fapi/v1/exchangeInfo", {})
    symbols = j.get("symbols") or []
    return {row.get("symbol") for row in symbols if row.get("symbol")}


def get_funding_rate(symbol: str) -> Optional[float]:
    rows = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
    if not rows:
        return None
    try:
        return float(rows[-1]["fundingRate"])
    except Exception:
        return None


def has_futures_symbol(symbol: str) -> bool:
    try:
        j = _get("/fapi/v1/openInterest", {"symbol": symbol})
        return bool(j.get("symbol"))
    except Exception:
        return False


def get_mark_price(symbol: str) -> Optional[float]:
    j = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    try:
        return float(j.get("markPrice"))
    except Exception:
        return None
