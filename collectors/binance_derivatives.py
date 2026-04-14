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
