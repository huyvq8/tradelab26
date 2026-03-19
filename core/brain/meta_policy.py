"""V4 meta-policy: choose active_policy_mode from inferred context."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.brain.policy_templates import modifiers_for
from core.brain.runtime_state import RuntimeStateV4, load_runtime_state, policy_cooldown_ok, save_runtime_state
from core.brain.types import ChangePointResult, MarketState, PolicyDecision, PolicyMode

_ROOT = Path(__file__).resolve().parent.parent.parent


def _cfg() -> dict:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def choose_policy_mode(
    *,
    market_state: MarketState,
    portfolio_stress_score: float,
    change_point_market: ChangePointResult,
    btc_context_score: float,
    regime_stability_proxy: float,
    kill_risk_near_limit: bool,
    rt: RuntimeStateV4 | None = None,
    cfg: dict[str, Any] | None = None,
) -> PolicyDecision:
    cfg = (cfg or _cfg()).get("meta_policy", {})
    ttl = int(cfg.get("policy_ttl_sec", 120))
    re_eval = int(cfg.get("re_evaluate_after_sec", 60))
    min_cooldown = float(cfg.get("policy_switch_cooldown_sec", 45))
    rt = rt or load_runtime_state()

    emergency = (
        kill_risk_near_limit
        or market_state == "SHOCK_UNSTABLE"
        or change_point_market.change_point_score >= float(cfg.get("cp_force_exit_only", 0.92))
    )
    if emergency:
        mode: PolicyMode = "EXIT_ONLY"
        reasons = ["emergency", f"market={market_state}", f"cp={change_point_market.change_point_score:.2f}"]
        conf = 0.92
    elif portfolio_stress_score > float(cfg.get("stress_capital_preservation", 0.78)):
        mode = "CAPITAL_PRESERVATION"
        reasons = [f"portfolio_stress={portfolio_stress_score:.2f}"]
        conf = 0.82
    elif (
        market_state == "RISK_ON_TRENDING"
        and regime_stability_proxy > float(cfg.get("stability_aggressive", 0.65))
        and change_point_market.change_point_score < float(cfg.get("cp_max_aggressive", 0.38))
    ):
        mode = "AGGRESSIVE"
        reasons = ["trending_stable_low_cp"]
        conf = 0.72
    elif market_state in ("BALANCED", "RISK_ON_EXHAUSTING"):
        mode = "DEFENSIVE"
        reasons = [f"market={market_state}"]
        conf = 0.68
    elif market_state == "RISK_OFF":
        mode = "CAPITAL_PRESERVATION"
        reasons = ["risk_off"]
        conf = 0.8
    else:
        mode = "NORMAL"
        reasons = ["default"]
        conf = 0.6

    if not policy_cooldown_ok(rt, mode, min_ttl_sec=min_cooldown, emergency=emergency):
        mode = rt.policy_mode if rt.policy_mode in TEMPLATES_NAMES else "NORMAL"
        reasons.append("cooldown_kept_previous")

    mods = modifiers_for(mode)
    return PolicyDecision(
        active_policy_mode=mode,
        policy_confidence=conf,
        policy_reason_codes=reasons,
        policy_ttl_sec=ttl,
        re_evaluate_after_sec=re_eval,
        modifiers=mods,
    )


TEMPLATES_NAMES = frozenset(
    {"DEFENSIVE", "NORMAL", "AGGRESSIVE", "CAPITAL_PRESERVATION", "EXIT_ONLY"}
)


def btc_context_score_from_regime(btc_regime: str) -> float:
    if btc_regime == "high_momentum":
        return 0.85
    if btc_regime == "risk_off":
        return 0.12
    return 0.5
