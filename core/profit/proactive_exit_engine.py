"""
Proactive exit engine (casecheck): profit protection mode + reversal score + TP1/TP2 + partial TP.
Chỉ proactive close khi đã vào profit_protection_mode và reversal_exit_score >= threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.profit.volatility_guard import load_profit_config


def load_proactive_exit_config() -> dict:
    """Đọc section proactive_exit từ profit.active.json."""
    cfg = load_profit_config()
    return cfg.get("proactive_exit") or {}


@dataclass
class ProactiveExitResult:
    action: str  # HOLD | MOVE_SL | PARTIAL_TP | PROACTIVE_CLOSE
    reason: str
    reason_code: str
    in_profit_protection_mode: bool
    reversal_exit_score: float | None
    suggested_sl: float | None
    partial_tp_pct: float
    explanation: dict


def _to_ohlcv(c: Any) -> tuple[float, float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None)
    low = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None)
    v = getattr(c, "volume", None) or (c.get("volume") if isinstance(c, dict) else 0.0)
    return (float(o or 0), float(h or 0), float(low or 0), float(cl or 0), float(v or 0))


def _ema(closes: list[float], period: int) -> float | None:
    if not closes or len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
    return ema


def _reversal_score_long(
    candles: list,
    quote: Any,
    weights: dict,
    ema_period: int,
    swing_lookback: int,
    volume_spike_ratio: float,
    min_candles: int,
) -> tuple[float, dict]:
    """Reversal exit score 0-1 for long position; breakdown dict."""
    if not candles or len(candles) < min_candles:
        return 0.0, {}
    try:
        from core.patterns.candlestick import detect_patterns
    except ImportError:
        detect_patterns = lambda x: []
    patterns = detect_patterns(candles)
    last = candles[-1]
    o, h, low, c, vol = _to_ohlcv(last)
    closes = [_to_ohlcv(x)[3] for x in candles]
    highs = [_to_ohlcv(x)[1] for x in candles]
    lows = [_to_ohlcv(x)[2] for x in candles]
    volumes = [_to_ohlcv(x)[4] for x in candles]
    breakdown = {}
    score = 0.0
    # close_below_ema
    ema_val = _ema(closes, ema_period)
    if ema_val is not None:
        v = 1.0 if c < ema_val else 0.0
        breakdown["close_below_ema"] = v
        score += weights.get("close_below_ema", 0.25) * v
    # break_swing_low
    if len(lows) >= swing_lookback + 1:
        swing_low = min(lows[-(swing_lookback + 1):-1])
        v = 1.0 if low < swing_low else 0.0
        breakdown["break_swing_low"] = v
        score += weights.get("break_swing_low", 0.2) * v
    # bearish_engulfing
    v = 1.0 if "engulfing_bear" in patterns else 0.0
    breakdown["bearish_engulfing"] = v
    score += weights.get("bearish_engulfing", 0.2) * v
    # volume_spike_bearish
    if len(volumes) >= 6 and volumes[-1] and sum(volumes[-6:-1]) > 0:
        avg_vol = sum(volumes[-6:-1]) / 5
        v = 1.0 if (volumes[-1] / avg_vol >= volume_spike_ratio and c < o) else 0.0
    else:
        v = 0.0
    breakdown["volume_spike_bearish"] = v
    score += weights.get("volume_spike_bearish", 0.15) * v
    # lower_high_lower_low
    if len(highs) >= 3 and len(lows) >= 3:
        h1, h2, h3 = highs[-3], highs[-2], highs[-1]
        l1, l2, l3 = lows[-3], lows[-2], lows[-1]
        v = 1.0 if (h1 >= h2 >= h3 and l1 >= l2 >= l3) else 0.0
    else:
        v = 0.0
    breakdown["lower_high_lower_low"] = v
    score += weights.get("lower_high_lower_low", 0.1) * v
    # momentum_weakening
    if len(candles) >= 2:
        prev = candles[-2]
        _, _, _, prev_c, _ = _to_ohlcv(prev)
        prev_body = abs(prev_c - _to_ohlcv(prev)[0])
        curr_body = abs(c - o)
        curr_range = h - low if h > low else 1e-10
        v = 1.0 if curr_body < curr_range * 0.3 and curr_body < prev_body else 0.0
    else:
        v = 0.0
    breakdown["momentum_weakening"] = v
    score += weights.get("momentum_weakening", 0.1) * v
    return min(1.0, score), breakdown


def _reversal_score_short(
    candles: list,
    quote: Any,
    weights: dict,
    ema_period: int,
    swing_lookback: int,
    volume_spike_ratio: float,
    min_candles: int,
) -> tuple[float, dict]:
    """Reversal exit score 0-1 for short position (bullish reversal = exit short)."""
    if not candles or len(candles) < min_candles:
        return 0.0, {}
    try:
        from core.patterns.candlestick import detect_patterns
    except ImportError:
        detect_patterns = lambda x: []
    patterns = detect_patterns(candles)
    last = candles[-1]
    o, h, low, c, vol = _to_ohlcv(last)
    closes = [_to_ohlcv(x)[3] for x in candles]
    highs = [_to_ohlcv(x)[1] for x in candles]
    lows = [_to_ohlcv(x)[2] for x in candles]
    volumes = [_to_ohlcv(x)[4] for x in candles]
    breakdown = {}
    score = 0.0
    # close_above_ema (bullish for short exit)
    ema_val = _ema(closes, ema_period)
    if ema_val is not None:
        v = 1.0 if c > ema_val else 0.0
        breakdown["close_above_ema"] = v
        score += weights.get("close_above_ema", 0.25) * v
    # break_swing_high
    if len(highs) >= swing_lookback + 1:
        swing_high = max(highs[-(swing_lookback + 1):-1])
        v = 1.0 if h > swing_high else 0.0
        breakdown["break_swing_high"] = v
        score += weights.get("break_swing_high", 0.2) * v
    # bullish_engulfing
    v = 1.0 if "engulfing_bull" in patterns else 0.0
    breakdown["bullish_engulfing"] = v
    score += weights.get("bullish_engulfing", 0.2) * v
    # volume_spike_bullish
    if len(volumes) >= 6 and volumes[-1] and sum(volumes[-6:-1]) > 0:
        avg_vol = sum(volumes[-6:-1]) / 5
        v = 1.0 if (volumes[-1] / avg_vol >= volume_spike_ratio and c > o) else 0.0
    else:
        v = 0.0
    breakdown["volume_spike_bullish"] = v
    score += weights.get("volume_spike_bullish", 0.15) * v
    # higher_high_higher_low
    if len(highs) >= 3 and len(lows) >= 3:
        h1, h2, h3 = highs[-3], highs[-2], highs[-1]
        l1, l2, l3 = lows[-3], lows[-2], lows[-1]
        v = 1.0 if (h1 <= h2 <= h3 and l1 <= l2 <= l3) else 0.0
    else:
        v = 0.0
    breakdown["higher_high_higher_low"] = v
    score += weights.get("higher_high_higher_low", 0.1) * v
    # momentum_turning_up
    if len(candles) >= 2:
        prev = candles[-2]
        _, _, _, prev_c, _ = _to_ohlcv(prev)
        prev_body = abs(prev_c - _to_ohlcv(prev)[0])
        curr_body = abs(c - o)
        curr_range = h - low if h > low else 1e-10
        v = 1.0 if curr_body > curr_range * 0.3 and c > o and curr_body >= prev_body * 0.5 else 0.0
    else:
        v = 0.0
    breakdown["momentum_turning_up"] = v
    score += weights.get("momentum_turning_up", 0.1) * v
    return min(1.0, score), breakdown


def evaluate_position(
    position: Any,
    current_price: float,
    klines_1h: list,
    quote: Any,
    config: dict | None = None,
    *,
    has_partial_closed: bool = False,
) -> ProactiveExitResult:
    """
    Đánh giá vị thế: có vào profit_protection_mode không; nếu có thì MOVE_SL / PARTIAL_TP / PROACTIVE_CLOSE.
    has_partial_closed: True nếu đã chốt một phần (không gợi ý PARTIAL_TP nữa).
    """
    cfg = config or load_proactive_exit_config()
    if not cfg.get("enabled", True):
        return ProactiveExitResult(
            action="HOLD",
            reason="",
            reason_code="",
            in_profit_protection_mode=False,
            reversal_exit_score=None,
            suggested_sl=None,
            partial_tp_pct=0.0,
            explanation={"message": "Proactive exit disabled."},
        )
    entry = float(getattr(position, "entry_price", 0) or 0)
    sl = getattr(position, "stop_loss", None)
    quantity = float(getattr(position, "quantity", 0) or 0)
    side = (getattr(position, "side", "long") or "long").strip().lower()
    if entry <= 0 or quantity <= 0:
        return ProactiveExitResult(
            action="HOLD",
            reason="",
            reason_code="",
            in_profit_protection_mode=False,
            reversal_exit_score=None,
            suggested_sl=None,
            partial_tp_pct=0.0,
            explanation={"message": "Invalid position."},
        )
    risk_usd = 0.0
    if sl is not None and float(sl) > 0:
        risk_usd = abs(entry - float(sl)) * quantity
    if side == "long":
        pnl_usd = (current_price - entry) * quantity
    else:
        pnl_usd = (entry - current_price) * quantity
    roi_pct = (pnl_usd / (entry * quantity)) * 100 if (entry * quantity) > 0 else 0.0
    unrealized_r = (pnl_usd / risk_usd) if risk_usd > 0 else 0.0

    # Partial take-profit at +1R (or configured) without waiting for profit-protection mode
    if cfg.get("partial_1r_enabled", False) and not has_partial_closed and risk_usd > 0:
        min_1r = float(cfg.get("partial_1r_min_r", 1.0) or 1.0)
        frac = max(0.0, min(1.0, float(cfg.get("partial_1r_fraction", 0.35) or 0.0)))
        if frac > 0 and unrealized_r >= min_1r:
            expl_1r = {
                "unrealized_r": round(unrealized_r, 2),
                "message": f"Partial TP at >= {min_1r}R — scale out {frac*100:.0f}%.",
            }
            return ProactiveExitResult(
                action="PARTIAL_TP",
                reason=expl_1r["message"],
                reason_code="partial_tp_1r",
                in_profit_protection_mode=False,
                reversal_exit_score=None,
                suggested_sl=None,
                partial_tp_pct=frac,
                explanation=expl_1r,
            )

    activation_r = float(cfg.get("profit_protection_activation_r", 1.5))
    activation_roi = float(cfg.get("profit_protection_activation_roi_pct", 40.0))
    in_mode = unrealized_r >= activation_r or roi_pct >= activation_roi

    tp1_pct = float(cfg.get("tp1_pct_from_entry", 6.0))
    tp2_pct = float(cfg.get("tp2_pct_from_entry", 10.0))
    if side == "long":
        tp1_level = entry * (1.0 + tp1_pct / 100.0)
        tp2_level = entry * (1.0 + tp2_pct / 100.0)
    else:
        tp1_level = entry * (1.0 - tp1_pct / 100.0)
        tp2_level = entry * (1.0 - tp2_pct / 100.0)

    explanation = {
        "in_profit_protection_mode": in_mode,
        "unrealized_r": round(unrealized_r, 2),
        "roi_pct": round(roi_pct, 2),
        "tp1_level": round(tp1_level, 6),
        "tp2_level": round(tp2_level, 6),
    }

    if not in_mode:
        explanation["message"] = f"Chưa đạt ngưỡng profit protection (cần >= {activation_r} R hoặc {activation_roi}% ROI)."
        return ProactiveExitResult(
            action="HOLD",
            reason=explanation["message"],
            reason_code="",
            in_profit_protection_mode=False,
            reversal_exit_score=None,
            suggested_sl=None,
            partial_tp_pct=0.0,
            explanation=explanation,
        )

    weights = cfg.get("reversal_signals_weights") or {}
    ema_period = int(cfg.get("ema_period", 9))
    swing_lookback = int(cfg.get("swing_low_lookback", 5))
    volume_spike_ratio = float(cfg.get("volume_spike_ratio", 2.0))
    min_candles = int(cfg.get("min_candles_for_score", 10))
    threshold = float(cfg.get("proactive_exit_threshold", 0.6))

    if side == "long":
        reversal_score, breakdown = _reversal_score_long(
            klines_1h, quote, weights, ema_period, swing_lookback, volume_spike_ratio, min_candles
        )
    else:
        reversal_score, breakdown = _reversal_score_short(
            klines_1h, quote, weights, ema_period, swing_lookback, volume_spike_ratio, min_candles
        )
    explanation["reversal_exit_score"] = round(reversal_score, 2)
    explanation["reversal_breakdown"] = breakdown

    if reversal_score >= threshold:
        explanation["message"] = f"Confirmed reversal (score {reversal_score:.2f} >= {threshold}); proactive exit."
        return ProactiveExitResult(
            action="PROACTIVE_CLOSE",
            reason=explanation["message"],
            reason_code="proactive_exit_triggered",
            in_profit_protection_mode=True,
            reversal_exit_score=reversal_score,
            suggested_sl=None,
            partial_tp_pct=0.0,
            explanation=explanation,
        )

    partial_pct = float(cfg.get("partial_take_profit_pct", 0.0))
    if partial_pct > 0 and not has_partial_closed:
        if side == "long" and current_price >= tp1_level:
            explanation["message"] = f"Price >= TP1 ({tp1_level:.4f}); partial TP {partial_pct*100:.0f}%."
            return ProactiveExitResult(
                action="PARTIAL_TP",
                reason=explanation["message"],
                reason_code="partial_tp_taken",
                in_profit_protection_mode=True,
                reversal_exit_score=reversal_score,
                suggested_sl=None,
                partial_tp_pct=partial_pct,
                explanation=explanation,
            )
        if side == "short" and current_price <= tp1_level:
            explanation["message"] = f"Price <= TP1 ({tp1_level:.4f}); partial TP {partial_pct*100:.0f}%."
            return ProactiveExitResult(
                action="PARTIAL_TP",
                reason=explanation["message"],
                reason_code="partial_tp_taken",
                in_profit_protection_mode=True,
                reversal_exit_score=reversal_score,
                suggested_sl=None,
                partial_tp_pct=partial_pct,
                explanation=explanation,
            )

    trailing_mode = (cfg.get("trailing_sl_mode") or "lock_profit_r").strip()
    lock_r = 1.0
    min_hold_min = float(cfg.get("min_hold_minutes_before_move_sl", 0) or 0)
    age_minutes = 1e9
    opened_at = getattr(position, "opened_at", None)
    if opened_at is not None and min_hold_min > 0:
        try:
            oa = opened_at
            if getattr(oa, "tzinfo", None) is not None:
                oa = oa.replace(tzinfo=None)
            age_minutes = (datetime.utcnow() - oa).total_seconds() / 60.0
        except Exception:
            age_minutes = 1e9

    if trailing_mode == "lock_profit_r" and risk_usd > 0 and quantity > 0 and (
        min_hold_min <= 0 or age_minutes >= min_hold_min
    ):
        risk_per_unit = abs(entry - float(sl)) if sl else 0.0
        if risk_per_unit > 0 and side == "long":
            new_sl = entry + lock_r * risk_per_unit
            if new_sl < current_price and (sl is None or new_sl > float(sl)):
                explanation["message"] = "Lock profit ~1R; move SL."
                return ProactiveExitResult(
                    action="MOVE_SL",
                    reason=explanation["message"],
                    reason_code="moved_sl_to_profit",
                    in_profit_protection_mode=True,
                    reversal_exit_score=reversal_score,
                    suggested_sl=round(new_sl, 6),
                    partial_tp_pct=0.0,
                    explanation=explanation,
                )
        if risk_per_unit > 0 and side == "short":
            new_sl = entry - lock_r * risk_per_unit
            if new_sl > current_price and (sl is None or new_sl < float(sl)):
                explanation["message"] = "Lock profit ~1R; move SL."
                return ProactiveExitResult(
                    action="MOVE_SL",
                    reason=explanation["message"],
                    reason_code="moved_sl_to_profit",
                    in_profit_protection_mode=True,
                    reversal_exit_score=reversal_score,
                    suggested_sl=round(new_sl, 6),
                    partial_tp_pct=0.0,
                    explanation=explanation,
                )

    explanation["message"] = "In profit protection; no action (reversal score below threshold)."
    return ProactiveExitResult(
        action="HOLD",
        reason=explanation["message"],
        reason_code="",
        in_profit_protection_mode=True,
        reversal_exit_score=reversal_score,
        suggested_sl=None,
        partial_tp_pct=0.0,
        explanation=explanation,
    )
