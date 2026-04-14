from __future__ import annotations

import requests
from typing import Any, Dict, List

BASE = "https://api.alternative.me/fng/"

def _get(params=None):
    r = requests.get(BASE, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_latest() -> Dict[str, Any]:
    j = _get({"limit": 1, "format": "json"})
    data = (j.get("data") or [])
    return data[0] if data else {}

def get_history(limit: int = 120) -> List[Dict[str, Any]]:
    j = _get({"limit": limit, "format": "json"})
    return j.get("data") or []
