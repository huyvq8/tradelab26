# Scoring cho short: chi short khi tong diem >= min_score
from __future__ import annotations

from typing import Any


def _ohlcv(c: Any) -> tuple[float, float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None)
    lo = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None)
    v = getattr(c, "volume", None) or (c.get("volume") if isinstance(c, dict) else 0.0)
    return (float(o or 0), float(h or 0), float(lo or 0), float(cl or 0), float(v or 0))


def _rsi(candles: list, period: int = 14) -> float | None:
    if not candles or len(candles) <= period:
        return None
    closes = [_ohlcv(c)[3] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(candles: list, period: int = 14) -> float | None:
    if not candles or len(candles) < 2 or period < 1:
        return None
    tr_list = []
    prev_close = None
    for c in candles:
        o, h, lo, cl, _ = _ohlcv(c)
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        tr_list.append(tr)
        prev_close = cl
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else None
    return sum(tr_list[-period:]) / period


def score_short_setup(
    setup_type: str,
    metrics: dict,
    candles: list,
    current_price: float,
    htf_downtrend: bool,
    config: dict | None = None,
) -> tuple[float, list[str], dict]:
    cfg = config or {}
    score = 0.0
    reasons: list[str] = []
    debug: dict = {}

    atr = _atr(candles, 14)
    rsi = _rsi(candles, 14)
    if candles:
        o, h, lo, cl, vol = _ohlcv(candles[-1])
        if "atr" in metrics and metrics.get("reasons"):
            if "pump_candle" in metrics["reasons"] and atr and atr > 0:
                body = abs(cl - o)
                if body >= atr * 0.8:
                    score += 2
                    reasons.append("pump_vs_atr")
        if "resistance" in metrics or "breakout_level" in metrics or "ema" in metrics:
            score += 2
            reasons.append("resistance_touch")
        if metrics.get("volume_ratio", 0) >= 1.3:
            score += 1
            reasons.append("volume_spike")
        if rsi is not None and rsi > 70:
            score += 1
            reasons.append("rsi_overbought")
        if "reasons" in metrics and "fail_continuation" in metrics["reasons"]:
            score += 2
            reasons.append("fail_continuation")
        if "reasons" in metrics and "break_structure_down" in metrics["reasons"]:
            score += 3
            reasons.append("break_structure")
        if "break_down" in metrics and metrics.get("break_down"):
            score += 2
            reasons.append("break_down")
        if htf_downtrend:
            score += 2
            reasons.append("htf_downtrend")

    debug["raw_score"] = score
    debug["atr"] = atr
    debug["rsi"] = rsi
    debug["htf_downtrend"] = htf_downtrend
    return (score, reasons, debug)
