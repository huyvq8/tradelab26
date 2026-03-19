"""
Planner cho entry/SL/TP short: entry aggressive vs confirmed, SL trên đỉnh pump/trap + buffer, TP theo RR/support.
"""
from __future__ import annotations

from typing import Any


def _recent_high(candles: list, lookback: int = 10) -> float | None:
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    highs = [getattr(c, "high", c.get("high") if isinstance(c, dict) else 0) for c in subset]
    return max(highs) if highs else None


def _recent_low(candles: list, lookback: int = 10) -> float | None:
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    lows = [getattr(c, "low", c.get("low") if isinstance(c, dict) else 1e9) for c in subset]
    return min(lows) if lows else None


def plan_short_entry_sl_tp(
    setup_type: str,
    metrics: dict,
    candles: list[Any],
    current_price: float,
    config: dict | None = None,
) -> tuple[float, float, float, list[float], str]:
    """
    Trả về (entry_price, stop_loss, take_profit, take_profit_levels, invalidation_reason).
    SL trên đỉnh pump/trap/lower high + buffer. TP theo RR min (vd 1.5) và support gần.
    """
    cfg = config or {}
    buffer_pct = float(cfg.get("sl_buffer_pct", 0.003))
    min_rr = float(cfg.get("min_rr", 1.5))
    entry = current_price
    sl = current_price * (1 + buffer_pct)
    if setup_type == "pump_exhaustion" and "resistance" in metrics:
        res = metrics["resistance"]
        sl = res * (1 + buffer_pct)
    elif setup_type == "bull_trap" and "breakout_level" in metrics:
        level = metrics["breakout_level"]
        sl = level * (1 + buffer_pct)
    elif setup_type == "trend_pullback" and "ema" in metrics:
        ema = metrics["ema"]
        sl = max(ema, current_price) * (1 + buffer_pct)
    else:
        swing_high = _recent_high(candles, 5) if candles else None
        if swing_high is not None and swing_high > current_price:
            sl = swing_high * (1 + buffer_pct)

    risk = abs(entry - sl)
    if risk <= 0:
        risk = entry * 0.01
    tp1 = entry - min_rr * risk
    # TP2 có thể tại support gần
    support = _recent_low(candles, 15) if candles else None
    tp2 = support if support is not None and support < tp1 else tp1 * 0.98
    take_profit_levels = [tp1, tp2]
    take_profit = tp1
    invalidation_reason = f"price_above_sl_{sl:.4f}"
    return (entry, sl, take_profit, take_profit_levels, invalidation_reason)
