from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import requests

BASE = "https://www.binance.com"
EVM_CHAIN_ALIASES = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bsc": "bsc",
    "bnb chain": "bsc",
    "bnb-chain": "bsc",
    "binance smart chain": "bsc",
    "base": "base",
    "arbitrum": "arbitrum",
    "arbitrum one": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "polygon": "polygon",
    "polygon pos": "polygon",
    "avalanche": "avalanche",
    "avalanche c-chain": "avalanche",
    "blast": "blast",
    "scroll": "scroll",
    "linea": "linea",
    "mantle": "mantle",
    "zksync": "zksync",
    "zksync era": "zksync",
    "mode": "mode",
}
CONTRACT_KLINE_CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "base": 8453,
    "arbitrum": 42161,
    "linea": 59144,
}
CONTRACT_KLINE_PATH = "/bapi/defi/v1/public/wallet-direct/buw/wallet/dex/market/token/kline/ai"
CONTRACT_KLINE_HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "binance-web3/1.1 (Skill)",
}


def make_token_key(chain: str, contract_address: str) -> str:
    return f"{chain}:{contract_address.lower()}"


def normalize_chain(raw_chain: Any) -> Optional[str]:
    if raw_chain is None:
        return None
    key = str(raw_chain).strip().lower().replace("_", " ").replace("-", " ")
    return EVM_CHAIN_ALIASES.get(key)


def _first_value(entry: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return None


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_token_list() -> List[Dict[str, Any]]:
    payload = _get("/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list")
    return payload.get("data") or []


def normalize_token_entry(entry: Dict[str, Any]) -> Optional[Dict[str, str]]:
    raw_symbol = _first_value(entry, "symbol", "tokenSymbol", "assetSymbol", "code")
    token_id = _first_value(entry, "alphaId", "alpha_id", "tokenId", "token_id", "id")
    chain = normalize_chain(_first_value(entry, "chain", "chainName", "network", "blockchain", "assetPlatform"))
    contract_address = _first_value(
        entry,
        "contractAddress",
        "contract_address",
        "address",
        "tokenAddress",
        "token_address",
    )
    name = _first_value(entry, "name", "tokenName", "assetName", "fullName")
    alpha_symbol = _first_value(entry, "alphaSymbol", "tradingSymbol", "pairSymbol")

    if isinstance(raw_symbol, str) and raw_symbol.endswith("USDT") and "_" in raw_symbol and not alpha_symbol:
        alpha_symbol = raw_symbol
        raw_symbol = _first_value(entry, "tokenSymbol", "assetSymbol", "code")
    if not alpha_symbol and isinstance(token_id, str):
        alpha_symbol = token_id if token_id.endswith("USDT") else f"{token_id}USDT"

    if not chain or not contract_address or not raw_symbol or not alpha_symbol:
        return None

    return {
        "symbol": str(raw_symbol).upper(),
        "name": str(name) if name else str(raw_symbol).upper(),
        "chain": chain,
        "contract_address": str(contract_address).lower(),
        "alpha_symbol": str(alpha_symbol),
    }


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 10) -> List[List[Any]]:
    payload = _get(
        "/bapi/defi/v1/public/alpha-trade/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    return payload.get("data") or []


def extract_day1_open(rows: List[List[Any]]) -> Optional[float]:
    if not rows:
        return None
    earliest = sorted(rows, key=lambda row: int(row[0]))[0]
    return float(earliest[1])


def fetch_alpha_day1_open(symbol: str) -> Optional[float]:
    return extract_day1_open(fetch_klines(symbol=symbol, interval="1d", limit=30))


def fetch_listing_reference_open(
    chain: str,
    contract_address: str,
    listing_time_ms: Any,
) -> Optional[Dict[str, Any]]:
    chain_id = CONTRACT_KLINE_CHAIN_IDS.get(str(chain or "").lower())
    try:
        listing_time = int(listing_time_ms)
    except (TypeError, ValueError, OverflowError):
        return None
    if chain_id is None or not contract_address or listing_time <= 0:
        return None
    listing_day_start = listing_time // 86_400_000 * 86_400_000
    try:
        response = requests.get(
            f"{BASE}{CONTRACT_KLINE_PATH}",
            params={
                "chainId": chain_id,
                "contractAddress": contract_address,
                "interval": "1d",
                "limit": 3,
                "startTime": listing_day_start,
            },
            headers=CONTRACT_KLINE_HEADERS,
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("code") != "000000" or payload.get("success") is not True:
        return None
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None
    rows = data.get("klineInfos") or []
    if not isinstance(rows, list):
        return None
    candidates = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            open_time = int(row[0])
            price = float(row[1])
        except (TypeError, ValueError, OverflowError):
            continue
        if open_time >= listing_day_start and math.isfinite(price) and price > 0:
            candidates.append((open_time, price))
    if not candidates:
        return None
    open_time, price = min(candidates)
    return {
        "price": price,
        "open_time_ms": open_time,
        "listing_day_offset_days": (open_time - listing_day_start) / 86_400_000,
        "source": "binance_web3_dex_contract_kline_ai",
    }
