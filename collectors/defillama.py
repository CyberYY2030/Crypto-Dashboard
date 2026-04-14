from __future__ import annotations

import requests
from typing import Any, Dict, Optional

TVL_BASE = "https://api.llama.fi"
STABLECOINS_BASE = "https://stablecoins.llama.fi"

def _get(base: str, path: str):
    url = f"{base}{path}"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def get_defi_tvl_history():
    return _get(TVL_BASE, "/charts")

def get_stablecoin_charts_all():
    # DefiLlama stablecoin marketcap history
    # Public endpoint commonly used: https://stablecoins.llama.fi/stablecoincharts/all
    return _get(STABLECOINS_BASE, "/stablecoincharts/all")

def extract_stablecoin_total_usd(row: Dict[str, Any]) -> Optional[float]:
    if row is None:
        return None
    tc = row.get("totalCirculatingUSD")
    # Many responses use totalCirculatingUSD as dict: {"peggedUSD": ...}
    if isinstance(tc, (int, float)):
        return float(tc)
    if isinstance(tc, dict):
        if "peggedUSD" in tc and tc["peggedUSD"] is not None:
            return float(tc["peggedUSD"])
        s = 0.0
        found = False
        for v in tc.values():
            if isinstance(v, (int, float)):
                s += float(v); found = True
        return s if found else None

    tc2 = row.get("totalCirculating")
    if isinstance(tc2, (int, float)):
        return float(tc2)
    if isinstance(tc2, dict):
        if "peggedUSD" in tc2 and tc2["peggedUSD"] is not None:
            return float(tc2["peggedUSD"])
        s = 0.0; found=False
        for v in tc2.values():
            if isinstance(v, (int, float)):
                s += float(v); found=True
        return s if found else None
    return None
