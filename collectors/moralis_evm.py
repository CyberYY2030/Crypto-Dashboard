from __future__ import annotations

from typing import Any, Dict, List

import requests

BASE = "https://deep-index.moralis.io/api/v2.2"


def _headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key}


def get_top_holders(token_address: str, chain: str, api_key: str, limit: int = 20) -> List[Dict[str, Any]]:
    r = requests.get(
        f"{BASE}/erc20/{token_address}/owners",
        params={"chain": chain, "limit": limit},
        headers=_headers(api_key),
        timeout=25,
    )
    r.raise_for_status()
    return (r.json() or {}).get("result") or []


def get_holder_metrics(token_address: str, chain: str, api_key: str) -> Dict[str, Any]:
    r = requests.get(
        f"{BASE}/erc20/{token_address}/holders",
        params={"chain": chain},
        headers=_headers(api_key),
        timeout=25,
    )
    r.raise_for_status()
    return r.json() or {}
