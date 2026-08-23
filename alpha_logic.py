from __future__ import annotations

from statistics import median
from typing import Iterable, Optional


def pct_drawdown(current_price: float, reference_price: float) -> Optional[float]:
    if current_price is None or reference_price in (None, 0):
        return None
    return max(0.0, (1.0 - (current_price / reference_price)) * 100.0)


def passes_drawdown_filter(
    current_price: float,
    alpha_open_price: float | None,
    ath_price: float | None,
    threshold_pct: float,
) -> bool:
    dd_alpha = pct_drawdown(current_price, alpha_open_price) if alpha_open_price else None
    dd_ath = pct_drawdown(current_price, ath_price) if ath_price else None
    return (dd_alpha is not None and dd_alpha >= threshold_pct) or (
        dd_ath is not None and dd_ath >= threshold_pct
    )


def volume_expansion_ratio(current_volume: float, baseline_volumes: Iterable[float]) -> Optional[float]:
    cleaned = [float(v) for v in baseline_volumes if v is not None]
    if current_volume is None or not cleaned:
        return None
    baseline = median(cleaned)
    if baseline == 0:
        return None
    return float(current_volume / baseline)


def passes_volume_filter(
    current_volume: float | None,
    previous_volume: float | None,
    min_volume: float,
    min_ratio: float,
) -> bool:
    if current_volume is None or previous_volume in (None, 0):
        return False
    return float(current_volume) > float(min_volume) and (float(current_volume) / float(previous_volume)) > float(
        min_ratio
    )


def passes_median_volume_filter(current_volume: float | None, baseline_volumes: Iterable[float], min_volume: float, min_ratio: float) -> bool:
    cleaned = [float(value) for value in baseline_volumes if value is not None]
    return passes_volume_filter(current_volume, float(median(cleaned)) if cleaned else None, min_volume, min_ratio)


def classify_signal(
    volume_expansion_ratio: float | None,
    price_above_range: bool,
    compression_score: float | None,
    breakout_ratio_min: float = 1.5,
    compression_score_min: float = 0.6,
) -> str:
    if volume_expansion_ratio is not None and volume_expansion_ratio >= breakout_ratio_min and price_above_range:
        return "first_volume_breakout"
    if compression_score is not None and compression_score >= compression_score_min and price_above_range:
        return "post_compression_confirmation"
    return "watch"


def composite_score(
    drawdown_alpha_pct: float | None,
    drawdown_ath_pct: float | None,
    market_cap_usd: float | None,
    volume_ratio: float | None,
) -> float:
    drawdown_score = max(drawdown_alpha_pct or 0.0, drawdown_ath_pct or 0.0)
    cap_score = 0.0 if not market_cap_usd else max(0.0, 100.0 - min(100.0, market_cap_usd / 1_000_000))
    volume_score = min((volume_ratio or 0.0) * 20.0, 100.0)
    return round(drawdown_score * 0.45 + cap_score * 0.25 + volume_score * 0.30, 2)
