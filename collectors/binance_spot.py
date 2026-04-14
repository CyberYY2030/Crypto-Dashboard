from __future__ import annotations
import requests
from typing import List, Tuple, Optional

BASE = "https://api.binance.com"

def get_klines(symbol: str, interval: str="1d", startTime: Optional[int]=None, endTime: Optional[int]=None, limit: int=1000):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if startTime is not None:
        params["startTime"] = int(startTime)
    if endTime is not None:
        params["endTime"] = int(endTime)
    r = requests.get(f"{BASE}/api/v3/klines", params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def get_24hr_ticker(symbol: str):
    r = requests.get(f"{BASE}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=25)
    r.raise_for_status()
    return r.json()

def backfill_daily_closes(symbol: str, days: int=650) -> List[Tuple[str, float]]:
    limit = min(1000, max(1, days))
    data = get_klines(symbol=symbol, interval="1d", limit=limit)
    out: List[Tuple[str, float]] = []
    import datetime as dt
    for row in data:
        open_ms = int(row[0])
        close = float(row[4])
        d = dt.datetime.utcfromtimestamp(open_ms/1000).date().isoformat()
        out.append((d, close))
    return out
