"""Apply PolicyMode modifiers to entry, sizing, scale-in, proactive exit (P1)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from core.brain.types import BrainV4CycleContext, PolicyMode

_ROOT = Path(__file__).resolve().parent.parent.parent


def _p1_cfg() -> dict[str, Any]:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("p1", {})
    except Exception:
        return {}


def regime_clarity_proxy(regime: str, market_state: str) -> float:
    m = (market_state or "").upper().replace(" ", "_")
    r = (regime or "").lower()
    if m in ("RISK_OFF", "SHOCK_UNSTABLE") and r == "risk_off":
        return 0.85
    if m in ("RISK_ON_TRENDING", "RISK_ON_EXHAUSTING") and r == "high_momentum":
        return 0.82
    if m == "BALANCED":
        return 0.55
    return 0.4


def apply_policy_entry_overlay(
    signal: Any,
    v4: BrainV4CycleContext,
    *,
    regime: str,
    market_state: str,
) -> dict[str, Any] | None:
    """Return reject dict or None if OK."""
    cfg = _p1_cfg().get("entry", {})
    pen = float(cfg.get("confidence_penalty_per_strictness", 0.08))
    min_clar = float(cfg.get("min_regime_clarity_defensive", 0.42))
    mods = v4.policy.modifiers
    base_min = float(cfg.get("base_min_confidence", 0.35))
    conf = float(getattr(signal, "confidence", 0) or 0)
    required = base_min + max(0.0, mods.entry_strictness - 1.0) * pen
    if conf < required:
        return {
            "symbol": signal.symbol,
            "strategy_name": signal.strategy_name,
            "reason": f"Brain V4 entry strictness: confidence {conf:.2f} < {required:.2f} ({v4.policy.active_policy_mode})",
            "reason_code": "BRAIN_V4_ENTRY_STRICTNESS",
        }
    clar = regime_clarity_proxy(regime, market_state)
    sens = float(mods.no_trade_sensitivity)
    if sens > 1.0 and clar < min_clar * (2.0 - min(1.5, sens)):
        return {
            "symbol": signal.symbol,
            "strategy_name": signal.strategy_name,
            "reason": f"Brain V4 low regime clarity {clar:.2f} under sensitivity {sens:.2f}",
            "reason_code": "BRAIN_V4_ENTRY_SENSITIVITY",
        }
    return None


def apply_policy_size_breakdown(
    base_size_usd: float,
    v4: BrainV4CycleContext,
    *,
    symbol: str,
) -> tuple[float, dict[str, float]]:
    """
    Returns (final_after_modifier_usd, breakdown).
    breakdown: pre_modifier (=base), post_size_mult, post_stress, post_notional_cap, post_modifier (=final rounded).
    """
    cfg = _p1_cfg().get("sizing", {})
    pre = float(base_size_usd)
    mult = float(v4.policy.modifiers.size_multiplier)
    after_mult = pre * mult
    mode = v4.policy.active_policy_mode
    stress = float(v4.portfolio_stress_score)
    after_stress = after_mult
    if mode in ("DEFENSIVE", "CAPITAL_PRESERVATION") and stress > 0:
        sm = float(cfg.get("stress_defensive_mult", 0.5))
        after_stress = after_mult * max(0.25, 1.0 - sm * stress)
    after_cap = after_stress
    cap = cfg.get("max_notional_per_symbol_usd")
    if cap is not None:
        try:
            c = float(cap)
            if c > 0:
                after_cap = min(after_stress, c)
        except (TypeError, ValueError):
            pass
    final = max(0.0, round(after_cap, 2))
    del symbol
    return final, {
        "pre_modifier_usd": round(pre, 2),
        "post_size_mult_usd": round(after_mult, 2),
        "post_stress_usd": round(after_stress, 2),
        "post_notional_cap_usd": round(after_cap, 2),
        "post_modifier_usd": final,
    }


def apply_policy_size_modifier(
    base_size_usd: float,
    v4: BrainV4CycleContext,
    *,
    symbol: str,
) -> float:
    final, _ = apply_policy_size_breakdown(base_size_usd, v4, symbol=symbol)
    return final


def scale_in_policy_gate(
    v4: BrainV4CycleContext | None,
    symbol: str,
    *,
    change_point_score: float,
    position_state: str | None,
    prev_cp: float | None,
) -> bool:
    if not v4:
        return True
    if v4.policy.active_policy_mode == "EXIT_ONLY" or v4.policy.modifiers.size_multiplier <= 0:
        return False
    mode = v4.policy.active_policy_mode
    max_jump = float(_p1_cfg().get("scale_in", {}).get("max_cp_increase_for_allow", 0.15))
    if prev_cp is not None and change_point_score - prev_cp > max_jump:
        return False
    if mode in ("DEFENSIVE", "CAPITAL_PRESERVATION"):
        if (position_state or "") != "THESIS_HEALTHY":
            return False
        if change_point_score >= 0.45:
            return False
    return True


def merge_proactive_exit_overlay(pe_cfg: dict[str, Any], policy_mode: PolicyMode) -> dict[str, Any]:
    """Mutates copy of proactive_exit flat config (keys used by evaluate_position)."""
    if not _p1_cfg().get("proactive_overlay", {}).get("enabled", True):
        return pe_cfg
    out = copy.deepcopy(pe_cfg)
    if policy_mode == "DEFENSIVE":
        out["partial_1r_min_r"] = float(out.get("partial_1r_min_r", 1.0) or 1.0) * 0.85
        out["proactive_exit_threshold"] = float(out.get("proactive_exit_threshold", 0.6) or 0.6) - 0.05
    elif policy_mode == "AGGRESSIVE":
        out["partial_1r_min_r"] = float(out.get("partial_1r_min_r", 1.0) or 1.0) * 1.15
        out["proactive_exit_threshold"] = float(out.get("proactive_exit_threshold", 0.6) or 0.6) + 0.05
    elif policy_mode == "CAPITAL_PRESERVATION":
        out["partial_1r_min_r"] = float(out.get("partial_1r_min_r", 1.0) or 1.0) * 0.75
        out["proactive_exit_threshold"] = float(out.get("proactive_exit_threshold", 0.6) or 0.6) - 0.08
    return out
