from __future__ import annotations

import os
import requests
import certifi
from typing import Any, Dict, Optional, List

BASE = "https://api.sosovalue.xyz"

def get_api_key() -> Optional[str]:
    return os.environ.get("SOSO_API_KEY")

def _verify_param() -> bool | str:
    """Return requests 'verify' parameter.
    - Default: use certifi CA bundle (more reliable on some Windows/Python builds)
    - If env SOSO_SSL_NO_VERIFY=1, disable verification (NOT recommended).
    - If env REQUESTS_CA_BUNDLE is set, requests will use it automatically; we still return certifi by default.
    """
    if os.environ.get("SOSO_SSL_NO_VERIFY") in ("1", "true", "TRUE", "yes", "YES"):
        return False
    return certifi.where()

def current_etf_data_metrics(etf_type: str, api_key: str) -> Dict[str, Any]:
    """Fetch current ETF data metrics.
    Docs: https://sosovalue.gitbook.io/soso-value-api-doc/api-document/get-current-etf-data-metrics
    """
    url = f"{BASE}/openapi/v2/etf/currentEtfDataMetrics"
    headers = {"x-soso-api-key": api_key, "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json={"type": etf_type}, timeout=25, verify=False)
    r.raise_for_status()
    return r.json()


def historical_inflow_chart(etf_type: str, api_key: str) -> List[Dict[str, Any]]:
    """Fetch last up to 300 days historical inflow chart.
    Endpoint: POST /openapi/v2/etf/historicalInflowChart
    """
    try:
        url = f"{BASE}/openapi/v2/etf/historicalInflowChart"
        headers = {"x-soso-api-key": api_key, "Content-Type": "application/json"}
        r = requests.post(url, headers=headers, json={"type": etf_type}, timeout=25, verify=False)
        r.raise_for_status()
        j = r.json()
        if j and j.get("code") == 0:
            data = j.get("data")
            # 结构1：data 是 list
            if isinstance(data, list):
                return data
            # 结构2：data 是 dict，list 在 data["list"]
            if isinstance(data, dict):
                return data.get("list") or []
    except requests.exceptions.RequestException:
        return []
    return []
