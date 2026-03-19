"""
Structural signal building: ATR, entry zone, TP bands, setup quality (used by implementations.py).
"""
from __future__ import annotations

from typing import Any


def _ohlc(c: Any) -> tuple[float, float, float, float]:
    o = float(getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None) or 0)
    h = float(getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None) or 0)
    low = float(getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None) or 0)
    cl = float(getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None) or 0)
    return o, h, low, cl


def atr_mean_range(klines: list, period: int = 14) -> float | None:
    """Simple ATR proxy: mean(high-low) over last `period` bars."""
    if not klines:
        return None
    use = klines[-period:] if len(klines) >= period else klines
    ranges = []
    for c in use:
        _, h, low, _ = _ohlc(c)
        if h > low:
            ranges.append(h - low)
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def closes_series(klines: list) -> list[float]:
    return [_ohlc(c)[3] for c in klines]


def ema_last(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
    return ema


def extension_score_in_range(
    price: float,
    klines: list,
    *,
    lookback: int = 10,
) -> float | None:
    """
    Where `price` sits inside the recent high-low range (last `lookback` bars), 0..1.
    1.0 = at/above range high (extended long / chase risk proxy); 0.0 = at/below range low.
    Deterministic; for measurement / comparability across experiments (not a hard gate by default).
    """
    if not klines or lookback < 2 or price <= 0:
        return None
    use = klines[-lookback:] if len(klines) >= lookback else klines
    highs = [_ohlc(c)[1] for c in use]
    lows = [_ohlc(c)[2] for c in use]
    hi, lo = max(highs), min(lows)
    span = hi - lo
    if span <= 0:
        return 0.5
    x = (float(price) - lo) / span
    return max(0.0, min(1.0, x))


def quality_long_momentum(
    *,
    regime: str,
    change_24h: float,
    price: float,
    klines: list,
    atr: float | None,
) -> float:
    """0..1 setup score for long momentum / breakout-style entries."""
    q = 0.45
    if regime == "high_momentum":
        q += 0.12
    if change_24h > 5:
        q += 0.08
    elif change_24h > 3:
        q += 0.04
    cs = closes_series(klines)
    if len(cs) >= 6:
        if cs[-1] > cs[-5]:
            q += 0.08
        slope = cs[-1] - cs[-3]
        if slope > 0 and atr and atr > 0:
            if slope / atr > 0.15:
                q += 0.06
    ema9 = ema_last(cs, 9)
    if ema9 is not None and price >= ema9 * 0.998:
        q += 0.06
    return max(0.35, min(0.95, q))


def structural_long_levels(
    price: float,
    atr: float,
    *,
    sl_atr_mult: float = 1.25,
    tp_atr_mult: float = 2.0,
    tp_ext_atr_mult: float = 3.0,
    min_sl_pct: float = 0.009,
    max_sl_pct: float = 0.038,
    max_tp_pct: float = 0.04,
    max_tp_ext_pct: float = 0.065,
    zone_atr_mult: float = 0.22,
) -> tuple[float, float, float, float, float, float]:
    """
    Returns (zone_low, zone_high, stop_loss, take_profit, take_profit_extended, entry_ref).
    Long only. Distances clamped vs price %.
    """
    z = max(zone_atr_mult * atr, 0.0015 * price)
    zl, zh = price - z, price + z
    sl_dist = max(sl_atr_mult * atr, min_sl_pct * price)
    sl_dist = min(sl_dist, max_sl_pct * price)
    sl = price - sl_dist
    tp_dist = min(tp_atr_mult * atr, max_tp_pct * price)
    tp_dist = max(tp_dist, 0.008 * price)
    tp = price + tp_dist
    tp_ext_dist = min(tp_ext_atr_mult * atr, max_tp_ext_pct * price)
    tp_ext_dist = max(tp_ext_dist, tp_dist * 1.1)
    tp_ext = price + tp_ext_dist
    return zl, zh, sl, tp, tp_ext, price
