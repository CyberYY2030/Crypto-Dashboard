from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import requests

BASE = "https://api.dexscreener.com"


def get_token_pairs(chain_id: str, token_address: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE}/token-pairs/v1/{chain_id}/{token_address}", timeout=25)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    return (data or {}).get("pairs") or []


def get_token_pairs_batch(
    chain_id: str,
    token_addresses: List[str],
    batch_size: int = 30,
    allow_individual_fallback: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {addr.lower(): [] for addr in token_addresses}
    for i in range(0, len(token_addresses), batch_size):
        chunk = [addr.lower() for addr in token_addresses[i : i + batch_size]]
        joined = ",".join(chunk)
        try:
            r = requests.get(f"{BASE}/tokens/v1/{chain_id}/{joined}", timeout=25)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            pairs = data if isinstance(data, list) else (data or {}).get("pairs") or []
        except requests.RequestException:
            pairs = []
            if allow_individual_fallback:
                for addr in chunk:
                    try:
                        pairs.extend(get_token_pairs(chain_id, addr))
                    except requests.RequestException:
                        continue
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            base_token = pair.get("baseToken")
            quote_token = pair.get("quoteToken")
            if base_token is None:
                base_token = {}
            if quote_token is None:
                quote_token = {}
            if not isinstance(base_token, dict) or not isinstance(quote_token, dict):
                continue
            base_addr = str(base_token.get("address") or "").lower()
            quote_addr = str(quote_token.get("address") or "").lower()
            if base_addr in grouped:
                grouped[base_addr].append(pair)
            if quote_addr in grouped and quote_addr != base_addr:
                grouped[quote_addr].append(pair)
    return grouped


def get_pairs_by_ref_batch(
    chain_id: str,
    pair_ids: List[str],
    batch_size: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """Return only exact persisted pair-address matches from bounded batch requests.

    This recovery path intentionally never calls ``get_pair_by_ref``: a partial
    provider response must leave a fixed pool unavailable rather than turn into
    an unbounded per-token request pattern.
    """
    matches: Dict[str, Dict[str, Any]] = {}
    normalized_ids = [str(pair_id).lower() for pair_id in pair_ids if pair_id]
    for index in range(0, len(normalized_ids), min(max(batch_size, 1), 30)):
        chunk = normalized_ids[index : index + min(max(batch_size, 1), 30)]
        try:
            response = requests.get(
                f"{BASE}/latest/dex/pairs/{chain_id}/{','.join(chunk)}",
                timeout=25,
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            payload = response.json() or {}
            pairs = payload.get("pairs") if isinstance(payload, dict) else []
        except requests.RequestException:
            continue
        for pair in pairs or []:
            if not isinstance(pair, dict):
                continue
            pair_address = str(pair.get("pairAddress") or "").lower()
            pool_ref = f"{chain_id}/{pair_address}"
            if pair_address in chunk and extract_pair_context(pair, pool_ref):
                matches[pair_address] = pair
    return matches


def choose_primary_pool(
    pairs: List[Dict[str, Any]], token_address: str | None = None, stable_quote_symbols: List[str] | None = None
) -> Optional[Dict[str, Any]]:
    """Choose once for a token; callers must persist and reuse the returned pairAddress."""
    token_address = (token_address or "").lower()
    stable_quotes = {symbol.upper() for symbol in (stable_quote_symbols or ["USDC", "USDT", "DAI", "USDE"])}

    def context(pair: Dict[str, Any]) -> tuple[bool, float, float, str]:
        pair_address = str(pair.get("pairAddress") or "")
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        volume = float((pair.get("volume") or {}).get("h24") or 0.0)
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        base_address = str(base.get("address") or "").lower()
        quote_address = str(quote.get("address") or "").lower()
        counterparty = quote if token_address and base_address == token_address else base if token_address and quote_address == token_address else quote
        return str(counterparty.get("symbol") or "").upper() in stable_quotes, liquidity, volume, pair_address

    eligible = [pair for pair in pairs if context(pair)[3] and context(pair)[1] > 0 and context(pair)[2] > 0]
    return min(eligible, key=lambda pair: (not context(pair)[0], -context(pair)[1], -context(pair)[2], context(pair)[3])) if eligible else None


def get_pair_by_ref(pool_ref: str) -> Dict[str, Any]:
    chain_id, pair_id = pool_ref.split("/", 1)
    r = requests.get(f"{BASE}/latest/dex/pairs/{chain_id}/{pair_id}", timeout=25)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    pairs = (r.json() or {}).get("pairs") or []
    _, pair_id = pool_ref.split("/", 1)
    return next((pair for pair in pairs if str(pair.get("pairAddress") or "").lower() == pair_id.lower()), {})


def pair_matches_ref(pair: Dict[str, Any], pool_ref: str) -> bool:
    if not isinstance(pair, dict) or not pair or "/" not in pool_ref:
        return False
    _, pair_id = pool_ref.split("/", 1)
    return str(pair.get("pairAddress") or "").lower() == pair_id.lower()


def extract_pair_context(pair: Dict[str, Any], pool_ref: str) -> Dict[str, Any]:
    if not pair_matches_ref(pair, pool_ref):
        return {}
    liquidity_data = pair.get("liquidity") or {}
    volume_data = pair.get("volume") or {}
    if not isinstance(liquidity_data, dict) or not isinstance(volume_data, dict):
        return {}
    liquidity = liquidity_data.get("usd")
    volume = volume_data.get("h24")
    try:
        price_usd = float(pair["priceUsd"]) if pair.get("priceUsd") not in (None, "") else None
        volume_24h = float(volume) if volume not in (None, "") else None
        liquidity_usd = float(liquidity) if liquidity not in (None, "") else None
        market_cap_usd = float(pair["marketCap"]) if pair.get("marketCap") not in (None, "") else None
        fdv_usd = float(pair["fdv"]) if pair.get("fdv") not in (None, "") else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}
    values = (price_usd, volume_24h, liquidity_usd, market_cap_usd, fdv_usd)
    if any(value is not None and not math.isfinite(value) for value in values):
        return {}
    return {
        "pool_ref": pool_ref,
        "price_usd": price_usd,
        "volume_24h": volume_24h,
        "liquidity_usd": liquidity_usd,
        "market_cap_usd": market_cap_usd,
        "fdv_usd": fdv_usd,
    }


def get_recent_daily_volumes(pool_ref: str, days: int = 7) -> List[float]:
    pair = get_pair_by_ref(pool_ref)
    h24 = float((pair.get("volume") or {}).get("h24") or 0.0)
    return [h24] * max(1, days)
