"""
Adjust SL/TP from strategy defaults using ATR (1h) + hard caps so TP matches realistic hold times.
"""
from __future__ import annotations

from typing import Any

from core.strategies.base import StrategySignal


def _mean_atr_range(klines: list, period: int) -> float | None:
    if not klines or len(klines) < 2:
        return None
    use = klines[-period:] if len(klines) >= period else klines
    ranges = []
    for c in use:
        h = float(getattr(c, "high", None) or 0)
        low = float(getattr(c, "low", None) or 0)
        if h > low:
            ranges.append(h - low)
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def adjust_signal_sl_tp(signal: StrategySignal, klines_1h: list, cfg: dict | None) -> dict:
    """
    Mutate signal.stop_loss / take_profit when cfg enabled.
    Returns explanation dict for logging / journal.
    """
    out = {
        "adjusted": False,
        "atr": None,
        "sl_pct_before": None,
        "sl_pct_after": None,
        "tp_pct_before": None,
        "tp_pct_after": None,
    }
    if not cfg or not cfg.get("enabled", True):
        return out
    entry = float(signal.entry_price or 0)
    if entry <= 0:
        return out
    period = int(cfg.get("atr_period", 14) or 14)
    atr = _mean_atr_range(klines_1h, period)
    if atr is None or atr <= 0:
        return out
    out["atr"] = round(atr, 8)

    sl_mult = float(cfg.get("sl_atr_mult", 1.25) or 1.25)
    tp_mult = float(cfg.get("tp_atr_mult", 2.0) or 2.0)
    min_sl_pct = float(cfg.get("min_sl_pct", 0.9) or 0.9) / 100.0
    max_sl_pct = float(cfg.get("max_sl_pct", 3.8) or 3.8) / 100.0
    max_tp_pct = float(cfg.get("max_tp_pct", 4.0) or 4.0) / 100.0
    min_tp_pct = float(cfg.get("min_tp_pct", 1.2) or 1.2) / 100.0

    sl_dist_orig = abs(entry - float(signal.stop_loss)) / entry
    tp_dist_orig = abs(float(signal.take_profit) - entry) / entry
    out["sl_pct_before"] = round(sl_dist_orig * 100, 4)
    out["tp_pct_before"] = round(tp_dist_orig * 100, 4)

    sl_dist_atr = (atr * sl_mult) / entry
    tp_dist_atr = (atr * tp_mult) / entry

    sl_dist = max(min_sl_pct, min(max_sl_pct, max(sl_dist_orig, sl_dist_atr)))
    tp_dist = min(max_tp_pct, max(min_tp_pct, min(tp_dist_orig, tp_dist_atr)))

    if signal.side.lower() == "long":
        signal.stop_loss = entry * (1.0 - sl_dist)
        signal.take_profit = entry * (1.0 + tp_dist)
    else:
        signal.stop_loss = entry * (1.0 + sl_dist)
        signal.take_profit = entry * (1.0 - tp_dist)

    out["adjusted"] = True
    out["sl_pct_after"] = round(sl_dist * 100, 4)
    out["tp_pct_after"] = round(tp_dist * 100, 4)
    return out
