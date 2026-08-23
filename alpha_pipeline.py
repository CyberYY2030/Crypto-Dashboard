from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

import alpha_logic
import db as dbm
from collectors import binance_alpha, binance_derivatives, coingecko, dexscreener, geckoterminal, moralis_evm


def _moralis_api_key(config: Dict[str, Any]) -> str:
    environment_key = os.environ.get("MORALIS_API_KEY", "").strip()
    if environment_key:
        return environment_key
    return str((config.get("alpha") or {}).get("moralis_api_key") or "").strip()


def fetch_alpha_rows(conn, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return dbm.fetch_alpha_universe(conn)


def extract_raw_fast_metrics(token_row: Dict[str, Any]) -> Dict[str, float | None]:
    raw_data = _extra_data(token_row)
    pool_snapshot = raw_data.get("pool_snapshot") or {}
    market_cap_usd = pool_snapshot.get("market_cap_usd") or pool_snapshot.get("fdv_usd")
    volume_24h = pool_snapshot.get("volume_24h")
    return {
        "market_cap_usd": float(market_cap_usd) if market_cap_usd not in (None, "") else None,
        "volume_24h": float(volume_24h) if volume_24h not in (None, "") else None,
    }


def _extra_data(token_row: Dict[str, Any]) -> Dict[str, Any]:
    raw = token_row.get("extra_json")
    return json.loads(raw) if isinstance(raw, str) and raw else {}


def _alpha_raw(extra: Dict[str, Any]) -> Dict[str, Any]:
    return extra.get("alpha_raw") if isinstance(extra.get("alpha_raw"), dict) else extra


def _matched_snapshot(extra: Dict[str, Any], pool_ref: str) -> Dict[str, Any]:
    snapshot = extra.get("pool_snapshot") or {}
    return snapshot if snapshot.get("pool_ref") == pool_ref else {}


def sync_alpha_universe(conn, config: Dict[str, Any]):
    rows = binance_alpha.fetch_token_list()
    futures_symbols = binance_derivatives.list_futures_symbols()
    candidates_by_chain: Dict[str, List[Dict[str, Any]]] = {}
    for raw in rows:
        normalized = binance_alpha.normalize_token_entry(raw)
        if not normalized:
            continue
        guessed_futures_symbol = f"{normalized['symbol']}USDT"
        if guessed_futures_symbol not in futures_symbols:
            continue
        item = {**normalized, "futures_symbol": guessed_futures_symbol, "raw": raw}
        candidates_by_chain.setdefault(normalized["chain"], []).append(item)

    existing_by_key = {row["token_key"]: row for row in dbm.fetch_alpha_universe(conn)}
    stable_quotes = (config.get("alpha") or {}).get("stable_quote_symbols")
    for chain, chain_rows in candidates_by_chain.items():
        pair_map = dexscreener.get_token_pairs_batch(
            chain,
            [row["contract_address"] for row in chain_rows],
            allow_individual_fallback=False,
        )
        for row in chain_rows:
            try:
                token_key = binance_alpha.make_token_key(row["chain"], row["contract_address"])
                existing = existing_by_key.get(token_key) or {}
                primary_pool_id = existing.get("primary_pool_id")
                pairs = pair_map.get(row["contract_address"].lower(), [])
                if primary_pool_id:
                    primary_pool = next((pair for pair in pairs if dexscreener.pair_matches_ref(pair, primary_pool_id)), None)
                else:
                    primary_pool = dexscreener.choose_primary_pool(pairs, row["contract_address"], stable_quotes)
                    primary_pool_id = f"{row['chain']}/{primary_pool['pairAddress']}" if primary_pool else None
                snapshot = dexscreener.extract_pair_context(primary_pool or {}, primary_pool_id) if primary_pool_id else {}
                extra_json = {"alpha_raw": row["raw"], "pool_snapshot": snapshot}
                if primary_pool_id and not snapshot:
                    extra_json["pool_snapshot"] = {"pool_ref": primary_pool_id, "failure_reason": "fixed_pool_not_in_batch"}

                dbm.upsert_alpha_universe(
                    conn,
                    {
                        "token_key": token_key,
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "chain": row["chain"],
                        "contract_address": row["contract_address"],
                        "alpha_symbol": row["alpha_symbol"],
                        "futures_symbol": row["futures_symbol"],
                        "primary_pool_id": primary_pool_id,
                        "market_cap_confidence": None,
                        "is_active": 1,
                        "extra_json": extra_json,
                    },
                )
            except Exception:
                continue
    conn.commit()


def fetch_current_pool_contexts(tokens: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Fetch persisted fixed pools, then bounded exact-pair recovery for omissions."""
    observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    contexts: Dict[str, Dict[str, Any]] = {}
    by_chain: Dict[str, List[Dict[str, Any]]] = {}
    for token in tokens:
        by_chain.setdefault(token["chain"], []).append(token)
    for chain, chain_tokens in by_chain.items():
        try:
            pairs_by_address = dexscreener.get_token_pairs_batch(
                chain,
                [token["contract_address"] for token in chain_tokens],
                allow_individual_fallback=False,
            )
        except Exception as exc:
            for token in chain_tokens:
                contexts[token["token_key"]] = {
                    "failure_reason": f"current_pool_batch:{type(exc).__name__}",
                    "observed_at": observed_at,
                }
            continue
        for token in chain_tokens:
            pool_ref = token["primary_pool_id"]
            pairs = pairs_by_address.get(token["contract_address"].lower(), [])
            pair = next(
                (candidate for candidate in pairs if dexscreener.pair_matches_ref(candidate, pool_ref)),
                None,
            )
            context = dexscreener.extract_pair_context(pair or {}, pool_ref)
            if _valid_current_pool_context(context):
                context["observed_at"] = observed_at
                context["source"] = "dexscreener_batch_fixed_pool"
            else:
                context = {
                    "failure_reason": "fixed_pool_missing_from_batch" if not pair else "fixed_pool_invalid_numeric",
                    "observed_at": observed_at,
                    "pool_ref": pool_ref,
                }
            contexts[token["token_key"]] = context

    missing_by_chain: Dict[str, List[Dict[str, Any]]] = {}
    for token in tokens:
        pool_ref = token["primary_pool_id"]
        if "/" not in pool_ref:
            contexts[token["token_key"]] = {
                "failure_reason": "fixed_pool_invalid_ref",
                "observed_at": observed_at,
                "pool_ref": pool_ref,
            }
        elif contexts.get(token["token_key"], {}).get("failure_reason"):
            missing_by_chain.setdefault(token["chain"], []).append(token)
    for chain, chain_tokens in missing_by_chain.items():
        pair_ids = [token["primary_pool_id"].split("/", 1)[1] for token in chain_tokens]
        try:
            pairs_by_ref = dexscreener.get_pairs_by_ref_batch(chain, pair_ids)
        except Exception as exc:
            for token in chain_tokens:
                contexts[token["token_key"]]["failure_reason"] = f"current_pool_exact_batch:{type(exc).__name__}"
            continue
        for token in chain_tokens:
            pool_ref = token["primary_pool_id"]
            pair_id = pool_ref.split("/", 1)[1].lower()
            context = dexscreener.extract_pair_context(pairs_by_ref.get(pair_id, {}), pool_ref)
            if _valid_current_pool_context(context):
                context["observed_at"] = observed_at
                context["source"] = "dexscreener_exact_pair_batch"
                contexts[token["token_key"]] = context
    return contexts


def _parse_timestamp(value: str | None) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def _positive_finite_number(value: Any) -> Optional[float]:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if math.isfinite(normalized) and normalized > 0 else None


def _valid_current_pool_context(context: Dict[str, Any]) -> bool:
    return bool(context) and all(
        _positive_finite_number(context.get(field)) is not None
        for field in ("price_usd", "volume_24h", "liquidity_usd")
    )


def _is_conservative_cheap_cap_target(
    token: Dict[str, Any], current_pool_context: Dict[str, Any], market_cap_limit: float
) -> bool:
    """Keep a token unless every available inexpensive cap estimate excludes it."""
    evidence = []
    for field in ("market_cap_usd", "fdv_usd"):
        value = _positive_finite_number(current_pool_context.get(field))
        if value is not None:
            evidence.append(value)
    try:
        raw_cap = _alpha_raw(_extra_data(token)).get("marketCap")
    except Exception:
        raw_cap = None
    value = _positive_finite_number(raw_cap)
    if value is not None:
        evidence.append(value)
    return not evidence or not all(value >= market_cap_limit for value in evidence)


class _ReferenceValidationError(Exception):
    pass


def _reference_payload(token: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    alpha_cfg = config["alpha"]
    baseline_days = max(int(alpha_cfg.get("volume_baseline_days", 7)), 1)
    breakout_days = max(int(alpha_cfg.get("breakout_lookback_days", 7)), 1)
    compression_days = max(int(alpha_cfg.get("compression_lookback_days", 7)), 1)
    raw_alpha = _alpha_raw(_extra_data(token))
    listing_reference = binance_alpha.fetch_listing_reference_open(
        token["chain"],
        token["contract_address"],
        raw_alpha.get("listingTime"),
    )
    if not listing_reference or _positive_finite_number(listing_reference.get("price")) is None:
        raise _ReferenceValidationError("listing_reference_open_missing_or_invalid")
    try:
        payload = geckoterminal.get_pool_ohlcv(
            token["primary_pool_id"],
            timeframe="day",
            aggregate=1,
            limit=max(baseline_days, breakout_days, compression_days * 2) + 1,
        )
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            raise _ReferenceValidationError(f"gt_ohlcv_http_{status_code}") from None
        raise _ReferenceValidationError("gt_ohlcv_HTTPError") from None
    except Exception as exc:
        raise _ReferenceValidationError(f"gt_ohlcv_{type(exc).__name__}") from None
    try:
        features = geckoterminal.compute_completed_daily_features(
            payload,
            baseline_days,
            breakout_days,
            compression_days,
        )
    except Exception as exc:
        raise _ReferenceValidationError(f"gt_features_{type(exc).__name__}") from None
    if not features or _positive_finite_number(features.get("baseline_volume_median")) is None:
        raise _ReferenceValidationError("gt_features_missing_or_invalid")
    coin_context = {}
    try:
        coin_payload = coingecko.get_contract_coin(token["chain"], token["contract_address"])
        coin_context = coingecko.extract_contract_coin_context(coin_payload)
    except Exception:
        pass
    return {
        "listing_reference_open_price": listing_reference["price"],
        "listing_reference_open_time_ms": listing_reference["open_time_ms"],
        "listing_reference_day_offset_days": listing_reference["listing_day_offset_days"],
        "ath_price": coin_context.get("ath_price"),
        "market_cap_usd": coin_context.get("market_cap_usd"),
        "market_cap_confidence": coin_context.get("market_cap_confidence"),
        "features": features,
        "provenance": {
            "listing_reference_source": listing_reference["source"],
            "listing_reference_semantic": "first_available_daily_candle_open",
            "listing_reference_open_time_ms": listing_reference["open_time_ms"],
            "listing_reference_day_offset_days": listing_reference["listing_day_offset_days"],
            "baseline_source": "geckoterminal_completed_utc_day",
            "baseline_window_days": baseline_days,
            "breakout_window_days": breakout_days,
            "compression_window_days": compression_days,
            "completed_candle_count": features.get("completed_candle_count", 0),
        },
    }


def _is_compatible_listing_reference_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    provenance = payload.get("provenance") or {}
    try:
        open_time = int(payload.get("listing_reference_open_time_ms"))
        day_offset = float(payload.get("listing_reference_day_offset_days"))
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        _positive_finite_number(payload.get("listing_reference_open_price")) is not None
        and open_time > 0
        and math.isfinite(day_offset)
        and day_offset >= 0
        and provenance.get("listing_reference_source") == "binance_web3_dex_contract_kline_ai"
        and provenance.get("listing_reference_semantic") == "first_available_daily_candle_open"
    )


def warm_alpha_reference_cache(conn, tokens: List[Dict[str, Any]], config: Dict[str, Any], now: Optional[dt.datetime] = None):
    now = now or dt.datetime.utcnow()
    alpha_cfg = config["alpha"]
    refresh_hours = alpha_cfg.get("reference_refresh_hours", 24)
    retry_hours = alpha_cfg.get("reference_failure_retry_hours", 6)
    batch_size = min(max(int(alpha_cfg.get("reference_refresh_batch_size", 20)), 1), 30)
    request_interval = max(float(alpha_cfg.get("reference_request_interval_seconds", 15.1)), 0.0)
    cache = dbm.fetch_alpha_reference_cache(conn, [token["token_key"] for token in tokens])
    candidates = []
    for token in tokens:
        entry = cache.get(token["token_key"])
        refreshed_at = _parse_timestamp(entry.get("refreshed_at") if entry else None)
        attempted_at = _parse_timestamp(entry.get("attempted_at") if entry else None)
        fresh = (
            _is_compatible_listing_reference_payload(entry.get("payload") if entry else None)
            and refreshed_at
            and (now - refreshed_at).total_seconds() < refresh_hours * 3600
        )
        retry_blocked = entry and entry["outcome"] == "failed" and attempted_at and (now - attempted_at).total_seconds() < retry_hours * 3600
        if not fresh and not retry_blocked:
            candidates.append((attempted_at or dt.datetime.min, token))
    candidates.sort(key=lambda item: (item[0], item[1]["token_key"]))
    refreshed = 0
    previous_attempt_started = None
    for _, token in candidates[:batch_size]:
        if previous_attempt_started is not None and request_interval > 0:
            elapsed = time.monotonic() - previous_attempt_started
            if elapsed < request_interval:
                time.sleep(request_interval - elapsed)
        previous_attempt_started = time.monotonic()
        attempted_at = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            payload = _reference_payload(token, config)
            listing_reference_open_price = payload.get("listing_reference_open_price")
            if (
                listing_reference_open_price is None
                or not math.isfinite(float(listing_reference_open_price))
                or float(listing_reference_open_price) <= 0
                or not payload.get("features")
            ):
                raise RuntimeError("reference_incomplete")
        except _ReferenceValidationError as exc:
            error_code = str(exc)
            dbm.upsert_alpha_reference_cache(
                conn,
                token["token_key"],
                None,
                None,
                attempted_at,
                "failed",
                error_code,
            )
            if error_code == "gt_ohlcv_http_429":
                break
        except Exception as exc:
            dbm.upsert_alpha_reference_cache(
                conn,
                token["token_key"],
                None,
                None,
                attempted_at,
                "failed",
                type(exc).__name__,
            )
        else:
            dbm.upsert_alpha_reference_cache(
                conn,
                token["token_key"],
                payload,
                attempted_at,
                attempted_at,
                "success",
            )
            refreshed += 1
    if candidates[:batch_size]:
        conn.commit()
    cache = dbm.fetch_alpha_reference_cache(conn, [token["token_key"] for token in tokens])
    max_age = alpha_cfg.get("reference_max_age_hours", 36)
    usable = {
        token_key: entry
        for token_key, entry in cache.items()
        if entry.get("payload")
        and _is_compatible_listing_reference_payload(entry.get("payload"))
        and (refreshed_at := _parse_timestamp(entry.get("refreshed_at")))
        and (now - refreshed_at).total_seconds() <= max_age * 3600
    }
    return usable, refreshed


def _screen_inputs_from_fresh_data(token_row, current_pool_context, reference_payload):
    pool_ref = token_row.get("primary_pool_id")
    raw_data = _alpha_raw(_extra_data(token_row))
    pool_context = current_pool_context or {}
    reference_payload = reference_payload or {}
    reasons: List[str] = []
    if not pool_context or pool_context.get("failure_reason"):
        reasons.append(pool_context.get("failure_reason") or "missing_fixed_pool_snapshot")
    if not reference_payload:
        reasons.append("missing_reference_cache")
    provenance = {
        "pool_ref": pool_ref,
        "price_volume_source": "dexscreener_batch_fixed_pool",
        "observed_at": pool_context.get("observed_at"),
        **(reference_payload.get("provenance") or {}),
    }
    features = reference_payload.get("features") or {}
    listing_reference_open_price = reference_payload.get("listing_reference_open_price")
    ath_price = reference_payload.get("ath_price")
    market_cap_usd = reference_payload.get("market_cap_usd")
    market_cap_confidence = reference_payload.get("market_cap_confidence")
    if market_cap_usd is None and pool_context.get("market_cap_usd") is not None:
        market_cap_usd = pool_context["market_cap_usd"]
        market_cap_confidence = "estimated_pair_market_cap"
    if market_cap_usd is None and pool_context.get("fdv_usd") is not None:
        market_cap_usd = pool_context["fdv_usd"]
        market_cap_confidence = "estimated_pair_fdv"
    if market_cap_usd is None and raw_data.get("marketCap") not in (None, ""):
        market_cap_usd = raw_data["marketCap"]
        market_cap_confidence = "estimated_alpha_raw"
    raw_baseline_volumes = features.get("baseline_volumes", [])
    if not isinstance(raw_baseline_volumes, list):
        raw_baseline_volumes = []
    baseline_volumes = [_positive_finite_number(value) for value in raw_baseline_volumes]
    if len(baseline_volumes) != len(raw_baseline_volumes) or any(value is None for value in baseline_volumes):
        baseline_volumes = []
        reasons.append("invalid_baseline_volumes")
    raw_values = {
        "price_usd": pool_context.get("price_usd"),
        "current_volume": pool_context.get("volume_24h"),
        "liquidity_usd": pool_context.get("liquidity_usd"),
        "baseline_volume": features.get("baseline_volume_median"),
        "listing_reference_open_price": listing_reference_open_price,
        "market_cap_usd": market_cap_usd,
    }
    normalized = {key: _positive_finite_number(value) for key, value in raw_values.items()}
    for key, missing, invalid in (
        ("price_usd", "missing_price", "invalid_price_non_positive_or_non_finite"),
        ("current_volume", "missing_current_volume", "invalid_current_volume_non_positive_or_non_finite"),
        ("liquidity_usd", "missing_liquidity", "invalid_liquidity_non_positive_or_non_finite"),
        ("baseline_volume", "insufficient_completed_baseline", "invalid_baseline_volume_non_positive_or_non_finite"),
        ("listing_reference_open_price", "missing_listing_reference_open", "invalid_listing_reference_open_non_positive_or_non_finite"),
        ("market_cap_usd", "missing_market_cap", "invalid_market_cap_non_positive_or_non_finite"),
    ):
        if raw_values[key] is None:
            reasons.append(missing)
        elif normalized[key] is None:
            reasons.append(invalid)
    price_usd = normalized["price_usd"]
    current_volume = normalized["current_volume"]
    liquidity_usd = normalized["liquidity_usd"]
    baseline_volume = normalized["baseline_volume"]
    listing_reference_open_price = normalized["listing_reference_open_price"]
    market_cap_usd = normalized["market_cap_usd"]
    ath_price = _positive_finite_number(ath_price)
    return {
        "price_usd": price_usd,
        "listing_reference_open_price": listing_reference_open_price,
        "listing_reference_open_time_ms": reference_payload.get("listing_reference_open_time_ms"),
        "listing_reference_day_offset_days": reference_payload.get("listing_reference_day_offset_days"),
        "ath_price": ath_price,
        "market_cap_usd": market_cap_usd,
        "market_cap_confidence": market_cap_confidence,
        "current_volume": current_volume,
        "baseline_volumes": baseline_volumes,
        "baseline_volume": baseline_volume,
        "price_above_range": bool(
            price_usd is not None
            and features.get("previous_high") is not None
            and price_usd > features["previous_high"]
        ),
        "compression_score": features.get("compression_score"),
        "liquidity_usd": liquidity_usd,
        "provenance": provenance,
        "failure_reason": ";".join(reasons) if reasons else None,
        "ready": not reasons,
    }


def fetch_screen_inputs(
    token_row: Dict[str, Any],
    config: Dict[str, Any],
    current_pool_context: Optional[Dict[str, Any]] = None,
    reference_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if current_pool_context is not None or reference_payload is not None:
        return _screen_inputs_from_fresh_data(
            token_row,
            current_pool_context,
            reference_payload,
        )
    alpha_cfg = config["alpha"]
    baseline_days = max(int(alpha_cfg.get("volume_baseline_days", 7)), 1)
    breakout_days = max(int(alpha_cfg.get("breakout_lookback_days", 7)), 1)
    compression_days = max(int(alpha_cfg.get("compression_lookback_days", 7)), 1)
    pool_ref = token_row.get("primary_pool_id")
    extra = _extra_data(token_row)
    raw_data = _alpha_raw(extra)
    reasons: List[str] = []
    provenance = {"pool_ref": pool_ref, "price_volume_source": None, "baseline_source": "geckoterminal_completed_utc_day", "baseline_window_days": baseline_days, "completed_candle_count": 0}
    pool_context = _matched_snapshot(extra, pool_ref) if pool_ref else {}
    if pool_context:
        provenance["price_volume_source"] = "dexscreener_batch_fixed_pool"
    elif pool_ref:
        try:
            pool_context = dexscreener.extract_pair_context(dexscreener.get_pair_by_ref(pool_ref), pool_ref)
            provenance["price_volume_source"] = "dexscreener_direct_fixed_pool"
        except Exception as exc:
            reasons.append(f"fixed_pool_fetch:{type(exc).__name__}")
    else:
        reasons.append("missing_fixed_pool")
    if not pool_context:
        reasons.append("missing_fixed_pool_snapshot")
    listing_reference = None
    try:
        listing_reference = binance_alpha.fetch_listing_reference_open(
            token_row["chain"],
            token_row["contract_address"],
            raw_data.get("listingTime"),
        )
        listing_reference_open_price = listing_reference.get("price") if listing_reference else None
        if listing_reference:
            provenance.update(
                {
                    "listing_reference_source": listing_reference.get("source"),
                    "listing_reference_semantic": "first_available_daily_candle_open",
                    "listing_reference_open_time_ms": listing_reference.get("open_time_ms"),
                    "listing_reference_day_offset_days": listing_reference.get("listing_day_offset_days"),
                }
            )
    except Exception as exc:
        listing_reference_open_price = None
        reasons.append(f"listing_reference_open:{type(exc).__name__}")
    ath_price = None
    market_cap_usd = None
    market_cap_confidence = None

    try:
        coin_payload = coingecko.get_contract_coin(token_row["chain"], token_row["contract_address"])
        coin_ctx = coingecko.extract_contract_coin_context(coin_payload)
        ath_price = coin_ctx.get("ath_price")
        if coin_ctx.get("market_cap_usd") is not None:
            market_cap_usd = coin_ctx["market_cap_usd"]
            market_cap_confidence = coin_ctx.get("market_cap_confidence") or "verified"
    except Exception:
        pass
    if market_cap_usd is None and pool_context.get("market_cap_usd") is not None:
        market_cap_usd, market_cap_confidence = pool_context["market_cap_usd"], "estimated_pair_market_cap"
    if market_cap_usd is None and pool_context.get("fdv_usd") is not None:
        market_cap_usd, market_cap_confidence = pool_context["fdv_usd"], "estimated_pair_fdv"
    if market_cap_usd is None and raw_data.get("marketCap") not in (None, ""):
        market_cap_usd, market_cap_confidence = float(raw_data["marketCap"]), "estimated_alpha_raw"
    if not pool_ref:
        features = {}
    else:
        try:
            payload = geckoterminal.get_pool_ohlcv(pool_ref, timeframe="day", aggregate=1, limit=max(baseline_days, breakout_days, compression_days * 2) + 1)
            features = geckoterminal.compute_completed_daily_features(payload, baseline_days, breakout_days, compression_days)
        except Exception as exc:
            features = {}
            reasons.append(f"pool_ohlcv:{type(exc).__name__}")
    provenance.update({"completed_candle_count": features.get("completed_candle_count", 0), "breakout_window_days": breakout_days, "compression_window_days": compression_days})
    current_volume = pool_context.get("volume_24h")
    price_usd = pool_context.get("price_usd")
    liquidity_usd = pool_context.get("liquidity_usd")
    baseline_volumes = features.get("baseline_volumes", [])
    baseline_volume = features.get("baseline_volume_median")
    if price_usd is None:
        reasons.append("missing_price")
    elif price_usd <= 0:
        reasons.append("invalid_price_non_positive")
    if current_volume is None:
        reasons.append("missing_current_volume")
    elif current_volume <= 0:
        reasons.append("invalid_current_volume_non_positive")
    if liquidity_usd is None:
        reasons.append("missing_liquidity")
    elif liquidity_usd <= 0:
        reasons.append("invalid_liquidity_non_positive")
    if baseline_volume is None:
        reasons.append("insufficient_completed_baseline")
    elif baseline_volume <= 0:
        reasons.append("invalid_baseline_volume_non_positive")
    if _positive_finite_number(listing_reference_open_price) is None:
        reasons.append("missing_listing_reference_open")
    if market_cap_usd is None:
        reasons.append("missing_market_cap")
    elif market_cap_usd <= 0:
        reasons.append("invalid_market_cap_non_positive")
    return {
        "price_usd": price_usd,
        "listing_reference_open_price": listing_reference_open_price,
        "listing_reference_open_time_ms": listing_reference.get("open_time_ms") if listing_reference else None,
        "listing_reference_day_offset_days": listing_reference.get("listing_day_offset_days") if listing_reference else None,
        "ath_price": ath_price,
        "market_cap_usd": market_cap_usd,
        "market_cap_confidence": market_cap_confidence,
        "current_volume": current_volume,
        "baseline_volumes": baseline_volumes,
        "baseline_volume": baseline_volume,
        "price_above_range": bool(price_usd is not None and features.get("previous_high") is not None and price_usd > features["previous_high"]),
        "compression_score": features.get("compression_score"),
        "liquidity_usd": liquidity_usd,
        "provenance": provenance,
        "failure_reason": ";".join(reasons) if reasons else None,
        "ready": not reasons,
    }


def fetch_holder_rows(token_row: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_key = _moralis_api_key(config)
    if not api_key:
        return []
    rows = moralis_evm.get_top_holders(
        token_address=token_row["contract_address"],
        chain=token_row["chain"],
        api_key=api_key,
        limit=20,
    )
    out = []
    for row in rows:
        address = str(row.get("owner_address") or "").strip()
        try: balance = float(row.get("balance_formatted"))
        except (TypeError, ValueError): continue
        if not address or not math.isfinite(balance) or balance < 0: continue
        try: pct_supply=float(row.get("percentage_relative_to_total_supply")) if row.get("percentage_relative_to_total_supply") is not None else None
        except (TypeError,ValueError): continue
        if pct_supply is not None and (not math.isfinite(pct_supply) or pct_supply < 0 or pct_supply > 100): continue
        label = str(row.get("owner_address_label") or "")
        entity = row.get("entity")
        entity_name = entity.get("name") if isinstance(entity, dict) else str(entity or "")
        text = f"{label} {entity_name}".lower()
        if address.lower() in {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"} or re.search(r"\b(burn|dead|zero)\b", text): holder_type="burn"
        elif "bridge" in text: holder_type="bridge"
        elif "treasury" in text or "vesting" in text: holder_type="treasury_vesting"
        elif re.search(r"\b(lp|dex|pool)\b|liquidity pool", text): holder_type="liquidity_pool"
        elif re.search(r"\b(exchange|binance|coinbase|kraken|okx|bybit|kucoin|bitget|mexc|htx)\b|\bgate\.io\b", text): holder_type="exchange"
        elif row.get("is_contract"): holder_type="contract_unknown"
        else: holder_type="wallet"
        out.append(
            {
                "address": address, "balance": balance,
                "pct_supply": pct_supply,
                "address_label": label or None, "entity_name": entity_name or None,
                "holder_type": holder_type,
                "is_excluded": int(holder_type != "wallet"),
                "extra_json": {"is_contract": bool(row.get("is_contract")), "owner_address_label": label, "entity": entity},
            }
        )
    return out


def refresh_alpha(conn, config: Dict[str, Any], now_ts: str | None = None, force: bool = False, entrypoint: str = "refresh_alpha"):
    now_ts = now_ts or dt.datetime.utcnow().replace(microsecond=0).isoformat()
    alpha_cfg = config["alpha"]
    now = dt.datetime.utcnow()
    run_id, reason = dbm.claim_alpha_screen_run(
        conn,
        now_ts,
        entrypoint,
        0,
        alpha_cfg.get("refresh_minutes", 30),
        alpha_cfg.get("stale_run_minutes", 60),
        force,
    )
    if not run_id:
        return {"status": "skipped", "reason": reason}
    sync_row = conn.execute(
        "SELECT universe_synced_at FROM alpha_screen_runs "
        "WHERE universe_synced=1 AND universe_synced_at IS NOT NULL "
        "ORDER BY universe_synced_at DESC LIMIT 1"
    ).fetchone()
    universe_synced = not sync_row or (
        now - dt.datetime.fromisoformat(sync_row[0]).replace(tzinfo=None)
    ).total_seconds() >= alpha_cfg.get("universe_refresh_hours", 24) * 3600
    if universe_synced:
        try:
            sync_alpha_universe(conn, config)
            conn.execute(
                "UPDATE alpha_screen_runs SET universe_synced=1,universe_synced_at=? WHERE run_id=?",
                (dt.datetime.now(dt.timezone.utc).isoformat(), run_id),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            dbm.finish_alpha_screen_run(conn, run_id, "failed", {}, [], type(exc).__name__)
            conn.commit()
            return {"status": "failed", "run_id": run_id, "reason": type(exc).__name__}
    tokens = fetch_alpha_rows(conn, config)
    conn.execute("UPDATE alpha_screen_runs SET universe_count=? WHERE run_id=?", (len(tokens), run_id))
    conn.commit()
    eligible = [
        token
        for token in tokens
        if token.get("alpha_symbol") and token.get("futures_symbol") and token.get("primary_pool_id")
    ]
    if not eligible:
        counts = {"eligible": 0, "snapshot": 0, "ready": 0, "passed": 0}
        dbm.finish_alpha_screen_run(conn, run_id, "failed", counts, [], "no_eligible_tokens")
        conn.commit()
        return {"status": "failed", "run_id": run_id, "reason": "no_eligible_tokens", "counts": counts}
    try:
        current_pool_contexts = fetch_current_pool_contexts(eligible)
    except Exception as exc:
        conn.rollback()
        counts = {"eligible": len(eligible), "snapshot": 0, "ready": 0, "passed": 0}
        dbm.finish_alpha_screen_run(conn, run_id, "failed", counts, [], type(exc).__name__)
        conn.commit()
        return {"status": "failed", "run_id": run_id, "reason": type(exc).__name__, "counts": counts}
    counts = {
        "eligible": len(eligible),
        "snapshot": 0,
        "ready": 0,
        "passed": 0,
        "current_pool": sum(
            1 for context in current_pool_contexts.values() if _valid_current_pool_context(context)
        ),
        "target": 0,
        "reference_ready": 0,
        "reference_refreshed": 0,
    }
    current_ratio = counts["current_pool"] / counts["eligible"]
    if current_ratio < alpha_cfg.get("min_current_pool_ratio", 0.8):
        dbm.finish_alpha_screen_run(conn, run_id, "incomplete", counts, [], "low_current_pool_ratio")
        conn.commit()
        return {"status": "incomplete", "run_id": run_id, "counts": counts, "warnings": []}
    targets = [
        token
        for token in eligible
        if _valid_current_pool_context(current_pool_contexts.get(token["token_key"], {}))
        and _is_conservative_cheap_cap_target(
            token,
            current_pool_contexts[token["token_key"]],
            alpha_cfg["market_cap_limit_usd"],
        )
    ]
    counts["target"] = len(targets)
    if not targets:
        dbm.finish_alpha_screen_run(conn, run_id, "complete", counts, [], None)
        conn.commit()
        return {"status": "complete", "run_id": run_id, "counts": counts, "warnings": []}
    try:
        reference_cache, reference_refreshed = warm_alpha_reference_cache(conn, targets, config, now)
    except Exception as exc:
        conn.rollback()
        dbm.finish_alpha_screen_run(conn, run_id, "failed", counts, [], type(exc).__name__)
        conn.commit()
        return {"status": "failed", "run_id": run_id, "reason": type(exc).__name__, "counts": counts}
    counts["reference_ready"] = len(reference_cache)
    counts["reference_refreshed"] = reference_refreshed
    rows = []
    holder_rows = []
    holder_attempts = []
    warnings = []
    try:
      for token in targets:
        try:
            cache_entry = reference_cache.get(token["token_key"])
            inputs = fetch_screen_inputs(
                token,
                config,
                current_pool_contexts.get(token["token_key"]),
                cache_entry["payload"] if cache_entry else None,
            )
        except Exception as exc:
            inputs={"ready":False,"failure_reason":f"screen_input:{type(exc).__name__}","baseline_volumes":[],"current_volume":None,"price_usd":None,"listing_reference_open_price":None,"ath_price":None,"market_cap_usd":None,"market_cap_confidence":None,"liquidity_usd":None,"price_above_range":False,"compression_score":None,"provenance":{}}
        baseline_volumes = inputs["baseline_volumes"]
        ratio = alpha_logic.volume_expansion_ratio(
            current_volume=inputs["current_volume"],
            baseline_volumes=baseline_volumes,
        )
        drawdown_listing_reference = alpha_logic.pct_drawdown(inputs["price_usd"], inputs["listing_reference_open_price"])
        drawdown_ath = alpha_logic.pct_drawdown(inputs["price_usd"], inputs["ath_price"])
        passed = bool(
            inputs.get("ready", True)
            and inputs["price_usd"] is not None and inputs["listing_reference_open_price"] is not None
            and inputs["market_cap_usd"] < alpha_cfg["market_cap_limit_usd"]
            and alpha_logic.passes_drawdown_filter(
                inputs["price_usd"],
                inputs["listing_reference_open_price"],
                inputs["ath_price"],
                alpha_cfg["drawdown_threshold_pct"],
            )
            and alpha_logic.passes_median_volume_filter(
                current_volume=inputs["current_volume"],
                baseline_volumes=baseline_volumes,
                min_volume=alpha_cfg.get("volume_min_usd", 5_000_000),
                min_ratio=alpha_cfg["volume_expansion_ratio_min"],
            )
        )
        screen_row={
                "ts": now_ts,
                "token_key": token["token_key"],
                "signal_label": alpha_logic.classify_signal(
                    volume_expansion_ratio=ratio,
                    price_above_range=inputs["price_above_range"],
                    compression_score=inputs["compression_score"],
                    breakout_ratio_min=alpha_cfg["volume_expansion_ratio_min"],
                    compression_score_min=alpha_cfg.get("compression_score_min", 0.6),
                ),
                "score": alpha_logic.composite_score(
                    drawdown_alpha_pct=drawdown_listing_reference,
                    drawdown_ath_pct=drawdown_ath,
                    market_cap_usd=inputs["market_cap_usd"],
                    volume_ratio=ratio,
                ),
                "price_usd": inputs["price_usd"],
                "volume_24h": inputs["current_volume"],
                "volume_expansion_ratio": ratio,
                "liquidity_usd": inputs.get("liquidity_usd"),
                "market_cap_usd": inputs["market_cap_usd"],
                "market_cap_confidence": inputs["market_cap_confidence"],
                "drawdown_from_alpha_open_pct": None,
                "drawdown_from_listing_reference_pct": drawdown_listing_reference,
                "drawdown_from_ath_pct": drawdown_ath,
                "funding_rate": None,
                "open_interest_usd": None,
                "passed_layer1": passed, "run_id":run_id,
                "extra_json": {"provenance": inputs.get("provenance", {}), "failure_reason": inputs.get("failure_reason")},
            }
        if passed:
            try:
                raw_funding_rate = binance_derivatives.get_funding_rate(token["futures_symbol"])
                raw_oi = binance_derivatives.get_open_interest(token["futures_symbol"])
                raw_mark = binance_derivatives.get_mark_price(token["futures_symbol"])
            except Exception as exc:
                warnings.append(f"futures:{token['token_key']}:{type(exc).__name__}")
                futures_reason = f"futures_unavailable:{type(exc).__name__}"
            else:
                try:
                    funding_rate = float(raw_funding_rate)
                    oi = float(raw_oi)
                    mark = float(raw_mark)
                except (TypeError, ValueError, OverflowError):
                    funding_rate = oi = mark = None
                if (
                    funding_rate is not None
                    and math.isfinite(funding_rate)
                    and math.isfinite(oi)
                    and math.isfinite(mark)
                    and oi > 0
                    and mark > 0
                ):
                    screen_row["funding_rate"] = funding_rate
                    screen_row["open_interest_usd"] = oi * mark
                    futures_reason = None
                else:
                    warnings.append(f"futures:{token['token_key']}:invalid_metrics")
                    futures_reason = "futures_unavailable:invalid_metrics"
            if futures_reason:
                passed = False
                existing_reason = inputs.get("failure_reason")
                screen_row["extra_json"]["failure_reason"] = ";".join(
                    reason for reason in (existing_reason, futures_reason) if reason
                )
        if not passed:
            screen_row["signal_label"] = "watch"
        screen_row["passed_layer1"] = passed
        rows.append(screen_row)
        counts["snapshot"] += 1
        counts["ready"] += int(bool(inputs.get("ready")))
        counts["passed"] += int(passed)
      if counts["snapshot"] != counts["target"]:
          raise RuntimeError("incomplete_snapshot_coverage")
      ready_ratio = counts["ready"] / counts["target"]
      if ready_ratio < alpha_cfg.get("min_ready_ratio", 0.8):
          dbm.finish_alpha_screen_run(conn, run_id, "incomplete", counts, warnings, "low_ready_ratio")
          conn.commit()
          return {"status": "incomplete", "run_id": run_id, "counts": counts, "warnings": warnings}
      token_by_key = {token["token_key"]: token for token in targets}
      for screen_row in rows:
          if not screen_row["passed_layer1"]:
              continue
          token = token_by_key[screen_row["token_key"]]
          api_key = _moralis_api_key(config)
          state = conn.execute(
              "SELECT attempted_at FROM alpha_holder_refresh_state WHERE token_key=?",
              (token["token_key"],),
          ).fetchone()
          due = not state or (
              now - dt.datetime.fromisoformat(state[0]).replace(tzinfo=None)
          ).total_seconds() >= alpha_cfg.get("holder_refresh_hours", 6) * 3600
          if api_key and due:
              try:
                  for holder in fetch_holder_rows(token, config):
                      holder_rows.append((token, holder))
                  holder_attempts.append((token["token_key"], "success"))
              except Exception as exc:
                  warnings.append(f"holder:{token['token_key']}:{type(exc).__name__}")
                  holder_attempts.append((token["token_key"], "failed"))
      conn.execute("BEGIN")
      for row in rows:
          dbm.insert_alpha_screen_snapshot(conn, row)
      for token, holder in holder_rows:
          holder["run_id"] = run_id
          dbm.insert_alpha_holder_snapshot(conn, now_ts, token["token_key"], holder)
      for token_key,outcome in holder_attempts:
          conn.execute(
              "INSERT INTO alpha_holder_refresh_state(token_key,attempted_at,last_run_id,outcome) "
              "VALUES(?,?,?,?) ON CONFLICT(token_key) DO UPDATE SET "
              "attempted_at=excluded.attempted_at,last_run_id=excluded.last_run_id,outcome=excluded.outcome",
              (token_key, dt.datetime.now(dt.timezone.utc).isoformat(), run_id, outcome),
          )
      dbm.finish_alpha_screen_run(conn, run_id, "complete", counts, warnings)
      conn.commit()
      return {"status": "complete", "run_id": run_id, "counts": counts, "warnings": warnings}
    except Exception as exc:
      conn.rollback()
      dbm.finish_alpha_screen_run(conn, run_id, "failed", counts, warnings, type(exc).__name__)
      conn.commit()
      return {"status": "failed", "run_id": run_id, "reason": type(exc).__name__, "counts": counts}
