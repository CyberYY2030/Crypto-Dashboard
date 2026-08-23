from __future__ import annotations

import os
import math
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml
from dateutil import tz

def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    if not os.path.exists(path):
        # allow running with example defaults
        path = "config.example.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def shanghai_today(config: Dict[str, Any]) -> dt.date:
    tzname = (config.get("timezone") or "Asia/Shanghai")
    tzi = tz.gettz(tzname)
    now = dt.datetime.now(tzi)
    return now.date()

def pct_change(current: float, previous: float) -> Optional[float]:
    if previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0

def realized_vol_from_prices(close_series):
    """Annualized realized volatility from a sequence of close prices."""
    import pandas as pd
    s = pd.Series(close_series).dropna()
    if len(s) < 3:
        return None
    rets = (s / s.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else float("nan")).dropna()
    if len(rets) < 2:
        return None
    # Daily returns -> annualize by sqrt(365) for crypto
    return float(rets.std(ddof=1) * math.sqrt(365.0))

def fmt_money(x: Optional[float]) -> str:
    if x is None:
        return "—"
    absx = abs(x)
    if absx >= 1e12:
        return f"${x/1e12:,.2f}T"
    if absx >= 1e9:
        return f"${x/1e9:,.2f}B"
    if absx >= 1e6:
        return f"${x/1e6:,.2f}M"
    if absx >= 1e3:
        return f"${x:,.0f}"
    return f"${x:,.2f}"

def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "—"
    if x >= 1000:
        return f"${x:,.0f}"
    return f"${x:,.2f}"

def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}%"

def alert_style(pct: float, threshold: float) -> str:
    # For message emphasis in Telegram
    if abs(pct) >= threshold * 1.5:
        return "🚨🚨"
    if abs(pct) >= threshold:
        return "🚨"
    return "ℹ️"

def percentile_rank(value: float, history) -> Optional[float]:
    """Return percentile rank (0-100) of value among history (iterable)."""
    vals = [v for v in history if v is not None]
    if value is None or len(vals) < 10:
        return None
    vals_sorted = sorted(vals)
    import bisect
    idx = bisect.bisect_right(vals_sorted, value)
    return 100.0 * idx / len(vals_sorted)

def fmt_billions_usd(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"${x/1e9:,.2f}B"


def rolling_percentile(series, window: int = 365):
    """Percentile rank of the last value within trailing window (0-100)."""
    import pandas as pd
    s = pd.Series(series).dropna()
    if len(s) < 5:
        return None
    tail = s.iloc[-window:] if len(s) >= window else s
    last = tail.iloc[-1]
    return float((tail.rank(pct=True).iloc[-1]) * 100.0)


def fmt_compact_number(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    absx = abs(x)
    if absx >= 1e9:
        return f"{x/1e9:,.2f}B"
    if absx >= 1e6:
        return f"{x/1e6:,.2f}M"
    if absx >= 1e3:
        return f"{x/1e3:,.1f}K"
    return f"{x:,.2f}"
