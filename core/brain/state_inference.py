"""V4 state inference: market, token, position (rule-based)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.regime.detector import derive_regime

from core.brain.types import MarketState, PositionState, TokenState

_ROOT = Path(__file__).resolve().parent.parent.parent


def _cfg() -> dict:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _trend_strength(klines: list[Any]) -> float:
    if len(klines) < 8:
        return 0.5
    c0 = float(klines[0].close)
    c1 = float(klines[-1].close)
    ret = (c1 - c0) / max(abs(c0), 1e-12)
    return max(0.0, min(1.0, (ret / 0.06 + 1) / 2))


def _reversal_proxy(klines: list[Any]) -> float:
    if len(klines) < 6:
        return 0.3
    highs = [float(k.high) for k in klines[-6:]]
    lows = [float(k.low) for k in klines[-6:]]
    rng = max(highs) - min(lows)
    if rng <= 0:
        return 0.3
    last = float(klines[-1].close)
    ext = (last - min(lows)) / rng
    return max(0.0, min(1.0, ext))


def _momentum_score(change_24h: float) -> float:
    return max(0.0, min(1.0, (change_24h / 15.0 + 1) / 2))


def infer_market_state(
    btc_change_24h: float,
    btc_volume_24h: float,
    alt_regimes: list[str],
    shock_change_point: float,
    cfg: dict | None = None,
) -> tuple[MarketState, float]:
    cfg = (cfg or _cfg()).get("state_inference", {})
    btc_reg = derive_regime(btc_change_24h, btc_volume_24h)
    mom_hi = sum(1 for r in alt_regimes if r == "high_momentum")
    risk = sum(1 for r in alt_regimes if r == "risk_off")
    n = max(len(alt_regimes), 1)
    breadth_hi = mom_hi / n
    breadth_risk = risk / n

    if shock_change_point >= float(cfg.get("shock_cp_for_unstable", 0.82)):
        return "SHOCK_UNSTABLE", 0.92
    if btc_reg == "risk_off" or breadth_risk > 0.55:
        return "RISK_OFF", 0.85
    if btc_reg == "high_momentum" and breadth_hi >= float(cfg.get("breadth_hi_trending", 0.35)):
        rev = shock_change_point
        if rev > float(cfg.get("exhausting_rev_proxy", 0.55)):
            return "RISK_ON_EXHAUSTING", 0.72
        return "RISK_ON_TRENDING", 0.78
    return "BALANCED", 0.6


def infer_token_state(
    change_24h: float,
    klines: list[Any],
    market_state: MarketState,
    cfg: dict | None = None,
) -> tuple[TokenState, float]:
    cfg = (cfg or _cfg()).get("state_inference", {})
    mom = _momentum_score(change_24h)
    trend = _trend_strength(klines)
    rev = _reversal_proxy(klines)
    vols = [float(getattr(k, "volume", 0) or 0) for k in klines[-12:]] if len(klines) >= 12 else []
    rel_vol_falling = False
    if len(vols) >= 4:
        rel_vol_falling = vols[-1] < 0.7 * (sum(vols[-4:-1]) / 3)

    from core.brain.change_point import structure_break_score

    st_sc, failed = structure_break_score(klines, "long" if change_24h >= 0 else "short")
    if failed and st_sc >= float(cfg.get("failed_breakout_min", 0.65)):
        return "FAILED_BREAKOUT", min(0.95, 0.85 + st_sc * 0.1)

    if mom > 0.82 and rev > 0.62 and rel_vol_falling:
        return "EXHAUSTION", 0.74
    if market_state == "SHOCK_UNSTABLE" and rev < 0.35 and mom < 0.4:
        return "PANIC_UNWIND", 0.82
    if mom > 0.75 and trend > 0.65 and rev < 0.38:
        return "CONTINUATION", 0.78
    if mom > 0.5 and trend > 0.55 and rev < 0.5:
        return "EARLY_BREAKOUT", 0.65
    if mom > 0.85 and trend > 0.7:
        return "LATE_BREAKOUT", 0.68
    if abs(change_24h) > float(cfg.get("mean_revert_change", 12)) and rev > 0.7:
        return "MEAN_REVERSION_CANDIDATE", 0.55
    if trend < 0.42 and mom < 0.48:
        return "DEAD_CHOP", 0.55
    return "DEAD_CHOP", 0.5


def infer_position_state(
    *,
    side: str,
    entry_price: float,
    stop_loss: float | None,
    price_now: float,
    unrealized_r: float,
    token_state: TokenState,
    market_state: MarketState,
    change_point_score: float,
    cfg: dict | None = None,
) -> tuple[PositionState, float]:
    cfg = (cfg or _cfg()).get("state_inference", {})
    sl = float(stop_loss) if stop_loss is not None else None
    thesis_broken = False
    if sl is not None and entry_price > 0:
        risk_px = abs(entry_price - sl)
        if risk_px > 0:
            if side == "long":
                thesis_broken = price_now < entry_price - 0.35 * risk_px
            else:
                thesis_broken = price_now > entry_price + 0.35 * risk_px

    if change_point_score >= float(cfg.get("cp_exit_urgent", 0.88)) or thesis_broken:
        return "EXIT_URGENT", 0.9
    if thesis_broken or token_state == "FAILED_BREAKOUT":
        return "THESIS_BROKEN", 0.88
    if token_state in ("EXHAUSTION", "PANIC_UNWIND") or market_state in ("RISK_OFF", "SHOCK_UNSTABLE"):
        return "THESIS_WEAK", 0.72
    if unrealized_r >= float(cfg.get("protected_r", 1.2)):
        return "PROFIT_PROTECTED", 0.75
    if unrealized_r >= float(cfg.get("stretched_r", 2.5)):
        return "THESIS_STRETCHED", 0.65
    return "THESIS_HEALTHY", 0.7
