from __future__ import annotations

import time
import requests
from typing import Any, Dict, List, Tuple, Optional

BASE = "https://api.coingecko.com/api/v3"

def _get(url: str, params: Dict[str, Any] | None = None) -> Any:
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

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
