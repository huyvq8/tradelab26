"""Open-trade thesis + zone-shift heuristic (deterministic, no LLM)."""
from __future__ import annotations

import json
from typing import Any

from core.portfolio.models import Position


def _bars_from_klines(klines: list[Any]) -> list[tuple[float, float, float, float]]:
    out: list[tuple[float, float, float, float]] = []
    for k in klines or []:
        if hasattr(k, "open"):
            out.append((float(k.open), float(k.high), float(k.low), float(k.close)))
        elif isinstance(k, (list, tuple)) and len(k) >= 4:
            out.append((float(k[0]), float(k[1]), float(k[2]), float(k[3])))
    return out[-48:] if len(out) > 48 else out


def _atr(bars: list[tuple[float, float, float, float]], n: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        o, h, l, c_prev = bars[i - 1][0], bars[i][1], bars[i - 1][2], bars[i - 1][3]
        _, h2, l2, _ = bars[i]
        tr = max(h2 - l2, abs(h2 - c_prev), abs(l2 - c_prev))
        trs.append(tr)
    tail = trs[-n:] if len(trs) >= n else trs
    return sum(tail) / len(tail) if tail else 0.0


def _zone_level(score: float, thr: dict[str, float]) -> str:
    if score < thr.get("low", 0.35):
        return "low"
    if score < thr.get("elevated", 0.55):
        return "elevated"
    if score < thr.get("high", 0.75):
        return "high"
    return "critical"


def _profile_for_position(position: Position, cfg: dict[str, Any]) -> dict[str, Any]:
    profiles = cfg.get("profiles") or {}
    key = (position.thesis_type or "generic").lower()
    if key in profiles:
        return profiles[key]
    if key == "generic":
        return profiles.get("default") or {}
    return profiles.get("default") or {}


def compute_zone_shift_and_state(
    position: Position,
    price_now: float,
    klines: list[Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    Returns zone_shift_risk_score, zone_shift_risk_level, thesis_state, thesis_score, reason_codes.
    thesis_state ∈ NORMAL | WARNING | DANGER | INVALID
    """
    bars = _bars_from_klines(klines)
    thr_z = cfg.get("zone_level_thresholds") or {}
    prof = _profile_for_position(position, cfg)
    w_warn = float(prof.get("warning_zone_shift", 0.45))
    w_dang = float(prof.get("danger_zone_shift", 0.65))
    w_inv = float(prof.get("invalid_zone_shift", 0.85))
    sl_mult = float(prof.get("sl_extension_invalid_mult", 2.0))

    direction = 1 if position.side == "long" else -1
    entry = float(position.entry_price or 0)
    reasons: list[str] = []
    score = 0.0

    if entry <= 0 or not bars:
        return {
            "zone_shift_risk_score": 0.0,
            "zone_shift_risk_level": "low",
            "thesis_state": "NORMAL",
            "thesis_score": 1.0,
            "reason_codes": ["insufficient_bars"],
        }

    atr = _atr(bars, 14)
    last = bars[-3:] if len(bars) >= 3 else bars

    # Bearish / bullish streak (last 3 bars)
    bear = 0
    bull = 0
    for o, h, l, c in last:
        rng = max(h - l, 1e-12)
        if c < o:
            bear += 1
            uw = h - max(o, c)
            if uw / rng > 0.55:
                score += 0.1
                reasons.append("upper_wick_pressure")
        elif c > o:
            bull += 1

    if direction > 0 and bear >= 2:
        score += 0.12 * bear
        reasons.append("bearish_close_streak")
    if direction < 0 and bull >= 2:
        score += 0.12 * bull
        reasons.append("bullish_close_streak")

    # Drawdown from entry vs ATR
    if atr > 0:
        if direction > 0:
            adv = max(0.0, entry - price_now)
        else:
            adv = max(0.0, price_now - entry)
        score += min(0.4, (adv / max(atr, 1e-9)) * 0.12)
        if adv > 0:
            reasons.append("adverse_from_entry")

    # Structure: close in bottom/top third of recent range (last 5)
    tail5 = bars[-5:]
    lo = min(x[2] for x in tail5)
    hi = max(x[1] for x in tail5)
    rng5 = hi - lo
    if rng5 > 0:
        if direction > 0:
            pos_in = (price_now - lo) / rng5
            if pos_in < 0.2:
                score += 0.18
                reasons.append("close_near_range_low")
        else:
            pos_in = (hi - price_now) / rng5
            if pos_in < 0.2:
                score += 0.18
                reasons.append("close_near_range_high")

    score = max(0.0, min(1.0, score))
    level = _zone_level(score, thr_z)
    thesis_score = max(0.0, min(1.0, 1.0 - score))

    sl = position.stop_loss
    if sl is not None:
        dist = abs(entry - float(sl))
        if dist > 0:
            if direction > 0 and price_now < entry - sl_mult * dist:
                reasons.append("deep_beyond_sl_plane")
                return {
                    "zone_shift_risk_score": max(score, w_inv),
                    "zone_shift_risk_level": "critical",
                    "thesis_state": "INVALID",
                    "thesis_score": 0.0,
                    "reason_codes": reasons,
                }
            if direction < 0 and price_now > entry + sl_mult * dist:
                reasons.append("deep_beyond_sl_plane")
                return {
                    "zone_shift_risk_score": max(score, w_inv),
                    "zone_shift_risk_level": "critical",
                    "thesis_state": "INVALID",
                    "thesis_score": 0.0,
                    "reason_codes": reasons,
                }

    if score >= w_inv:
        state = "INVALID"
    elif score >= w_dang:
        state = "DANGER"
    elif score >= w_warn:
        state = "WARNING"
    else:
        state = "NORMAL"

    return {
        "zone_shift_risk_score": round(score, 4),
        "zone_shift_risk_level": level,
        "thesis_state": state,
        "thesis_score": round(thesis_score, 4),
        "reason_codes": reasons or ["ok"],
    }


def snapshot_json_for_eval(
    position: Position, price_now: float, eval_result: dict[str, Any]
) -> str:
    return json.dumps(
        {
            "symbol": position.symbol,
            "side": position.side,
            "entry_price": position.entry_price,
            "price_now": price_now,
            "eval": eval_result,
        },
        ensure_ascii=False,
    )
