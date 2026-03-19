"""V4 change-point / context-break detection (deterministic)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from core.brain.types import ChangePointResult, ReflexActionType, ShiftType, UrgencyLevel

_ROOT = Path(__file__).resolve().parent.parent.parent
_CFG_PATH = _ROOT / "config" / "brain_v4.v1.json"


def _load_cfg() -> dict[str, Any]:
    p = _CFG_PATH if _CFG_PATH.exists() else _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _klines_to_ohlc(k: Any) -> tuple[float, float, float, float, float]:
    return (
        float(k.open),
        float(k.high),
        float(k.low),
        float(k.close),
        float(getattr(k, "volume", 0) or 0),
    )


def structure_break_score(klines: list[Any], side: str) -> tuple[float, bool]:
    """Failed breakout / loss of micro structure. Returns (score, breakout_failed)."""
    if len(klines) < 8:
        return 0.0, False
    o, h, l, c, v = zip(*[_klines_to_ohlc(x) for x in klines[-8:]])
    hi = max(h)
    lo = min(l)
    rng = max(hi - lo, 1e-12)
    last_c = c[-1]
    # Close back into range after tagging high (long failure)
    tagged_high = h[-3] >= hi * 0.999 or h[-2] >= hi * 0.999
    back_inside = last_c < hi - 0.25 * rng and last_c > lo + 0.15 * rng
    failed_long = side == "long" and tagged_high and last_c < c[-2] and back_inside
    tagged_low = l[-3] <= lo * 1.001 or l[-2] <= lo * 1.001
    failed_short = side == "short" and tagged_low and last_c > c[-2] and not back_inside
    breakout_failed = failed_long or failed_short
    score = 0.75 if breakout_failed else 0.2
    if abs(c[-1] - c[-2]) / max(abs(c[-2]), 1e-12) > 0.04 and breakout_failed:
        score = min(1.0, score + 0.15)
    return score, breakout_failed


def participation_break_score(klines: list[Any]) -> float:
    if len(klines) < 12:
        return 0.0
    vols = [float(getattr(x, "volume", 0) or 0) for x in klines[-12:]]
    if not vols or max(vols) <= 0:
        return 0.0
    med = sorted(vols[:-1])[len(vols[:-1]) // 2] or 1e-9
    rel = vols[-1] / med
    if rel < 0.35:
        return 0.85
    if rel < 0.55:
        return 0.45
    body = abs(float(klines[-1].close) - float(klines[-1].open))
    rng = float(klines[-1].high) - float(klines[-1].low)
    if rng > 0 and body / rng < 0.15 and rel > 2.5:
        return 0.55
    return 0.15


def btc_leader_break_score(prev_btc_regime: str, curr_btc_regime: str) -> float:
    risk = {"risk_off", "RISK_OFF"}
    shock = {"SHOCK_UNSTABLE"}
    if curr_btc_regime in shock:
        return 0.95
    if prev_btc_regime and curr_btc_regime != prev_btc_regime:
        if curr_btc_regime in risk or "risk" in curr_btc_regime.lower():
            return 0.88
        return 0.5
    return 0.1


def crowding_break_score(funding_rate: float | None) -> float:
    if funding_rate is None:
        return 0.2
    x = abs(float(funding_rate))
    cap = 0.0008
    if x >= cap * 3:
        return 0.9
    if x >= cap * 2:
        return 0.65
    if x >= cap:
        return 0.4
    return 0.15


def shock_score(klines: list[Any]) -> float:
    if len(klines) < 6:
        return 0.0
    moves = []
    for k in klines[-6:]:
        o, h, l, c, _ = _klines_to_ohlc(k)
        base = max(abs(o), 1e-12)
        moves.append((h - l) / base)
    med = sorted(moves[:-1])[len(moves[:-1]) // 2] if len(moves) > 1 else moves[0]
    last = moves[-1]
    if med <= 0:
        return 0.0
    ratio = last / med
    if ratio >= 2.8:
        return 0.95
    if ratio >= 2.0:
        return 0.7
    if ratio >= 1.5:
        return 0.45
    return 0.1


def aggregate_change_point(
    *,
    structure: float,
    structure_failed_breakout: bool,
    participation: float,
    btc_leader: float,
    crowding: float,
    shock: float,
    cfg: dict[str, Any] | None = None,
) -> ChangePointResult:
    cfg = cfg or _load_cfg().get("change_point", {})
    w = cfg.get("weights") or {}
    ws = float(w.get("structure", 0.28))
    wp = float(w.get("participation", 0.22))
    wb = float(w.get("btc_leader", 0.22))
    wc = float(w.get("crowding", 0.13))
    wk = float(w.get("shock", 0.15))
    s = ws * structure + wp * participation + wb * btc_leader + wc * crowding + wk * shock
    s = max(0.0, min(1.0, s))
    fp_min = int(cfg.get("false_positive_min_detectors", 2))
    urgency_med = float(cfg.get("urgency_medium", 0.7))
    urgency_high = float(cfg.get("urgency_high", 0.85))
    low = float(cfg.get("urgency_low", 0.55))

    detectors_high = sum(1 for x in (structure, participation, btc_leader, crowding, shock) if x >= 0.72)
    context_break = s >= urgency_high or (detectors_high >= fp_min and s >= urgency_med) or (
        structure_failed_breakout and s >= low
    )

    shift_type: ShiftType = "NONE"
    if structure_failed_breakout and structure >= 0.65:
        shift_type = "FAILED_BREAKOUT"
    elif shock >= 0.75:
        shift_type = "VOLATILITY_SHOCK"
    elif btc_leader >= 0.8:
        shift_type = "BTC_LED_BREAK"
    elif participation >= 0.75:
        shift_type = "LIQUIDITY_VACUUM"
    elif crowding >= 0.8:
        shift_type = "CROWD_UNWIND"
    elif s >= urgency_high:
        shift_type = "THESIS_INVALIDATION_PRE_SL"

    if s >= urgency_high:
        urg: UrgencyLevel = "HIGH"
        rec: ReflexActionType = "FORCE_EXIT"
    elif s >= urgency_med:
        urg = "MEDIUM"
        rec = "PARTIAL_REDUCE"
    elif s >= low:
        urg = "LOW"
        rec = "BLOCK_SCALE_IN"
    else:
        urg = "NONE"
        rec = "NONE"

    reason_codes = [
        f"cp={s:.2f}",
        f"struct={structure:.2f}",
        f"part={participation:.2f}",
        f"btc={btc_leader:.2f}",
        f"crowd={crowding:.2f}",
        f"shock={shock:.2f}",
    ]
    return ChangePointResult(
        change_point_score=s,
        context_break_flag=context_break,
        shift_type=shift_type,
        urgency_level=urg,
        recommended_protective_action=rec,
        detector_scores={
            "structure": structure,
            "participation": participation,
            "btc_leader": btc_leader,
            "crowding": crowding,
            "shock": shock,
        },
        reason_codes=reason_codes,
    )


def compute_change_point_for_symbol(
    klines: list[Any],
    side: str,
    *,
    prev_btc_regime: str,
    curr_btc_regime: str,
    funding_rate: float | None = None,
) -> ChangePointResult:
    st, failed = structure_break_score(klines, side)
    part = participation_break_score(klines)
    btc_b = btc_leader_break_score(prev_btc_regime, curr_btc_regime)
    cr = crowding_break_score(funding_rate)
    sh = shock_score(klines)
    return aggregate_change_point(
        structure=st,
        structure_failed_breakout=failed,
        participation=part,
        btc_leader=btc_b,
        crowding=cr,
        shock=sh,
    )
