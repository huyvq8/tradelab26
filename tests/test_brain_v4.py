"""Unit tests: Brain V4 deterministic layers."""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def test_aggregate_change_point_high():
    from core.brain.change_point import aggregate_change_point

    r = aggregate_change_point(
        structure=0.9,
        structure_failed_breakout=True,
        participation=0.8,
        btc_leader=0.85,
        crowding=0.3,
        shock=0.9,
        cfg={"weights": {}, "false_positive_min_detectors": 2, "urgency_medium": 0.7, "urgency_high": 0.85, "urgency_low": 0.55},
    )
    assert r.change_point_score >= 0.5
    assert r.context_break_flag is True
    assert r.urgency_level in ("HIGH", "MEDIUM")


def test_infer_market_state_risk_off():
    from core.brain.state_inference import infer_market_state

    st, conf = infer_market_state(
        -8.0,
        50_000_000,
        ["risk_off", "risk_off"],
        shock_change_point=0.1,
        cfg={},
    )
    assert st == "RISK_OFF"
    assert conf >= 0.5


def test_policy_exit_only_emergency():
    from core.brain.change_point import ChangePointResult
    from core.brain.meta_policy import choose_policy_mode
    from core.brain.runtime_state import RuntimeStateV4

    cp = ChangePointResult(
        change_point_score=0.95,
        context_break_flag=True,
        shift_type="VOLATILITY_SHOCK",
        urgency_level="HIGH",
        recommended_protective_action="FORCE_EXIT",
    )
    p = choose_policy_mode(
        market_state="SHOCK_UNSTABLE",
        portfolio_stress_score=0.1,
        change_point_market=cp,
        btc_context_score=0.2,
        regime_stability_proxy=0.3,
        kill_risk_near_limit=False,
        rt=RuntimeStateV4(),
        cfg={},
    )
    assert p.active_policy_mode == "EXIT_ONLY"


def test_reflex_force_exit():
    from core.brain.change_point import ChangePointResult
    from core.brain.reflex import resolve_reflex

    cp = ChangePointResult(0.9, True, "FAILED_BREAKOUT", "HIGH", "FORCE_EXIT")  # type: ignore[arg-type]
    r = resolve_reflex(cp, "THESIS_HEALTHY", "NORMAL", side="long", btc_risk_off_long=False)
    assert r is not None
    assert r.primary_action == "FORCE_EXIT"


def test_hysteresis_emergency_bypass():
    from core.brain.runtime_state import hysteresis_pick

    s, _ = hysteresis_pick(
        "SHOCK_UNSTABLE",
        0.9,
        "BALANCED",
        0.8,
        switch_margin=0.2,
        emergency_states=frozenset({"SHOCK_UNSTABLE"}),
    )
    assert s == "SHOCK_UNSTABLE"


def test_should_block_exit_only():
    from core.brain.context import should_block_cycle_symbol
    from core.brain.meta_policy import choose_policy_mode
    from core.brain.types import BrainV4CycleContext, ChangePointResult

    cp = ChangePointResult(0.96, True, "VOLATILITY_SHOCK", "HIGH", "FORCE_EXIT")  # type: ignore[arg-type]
    pol = choose_policy_mode(
        market_state="SHOCK_UNSTABLE",
        portfolio_stress_score=0.0,
        change_point_market=cp,
        btc_context_score=0.15,
        regime_stability_proxy=0.2,
        kill_risk_near_limit=True,
        cfg={"meta_policy": {}},
    )
    ctx = BrainV4CycleContext(
        enabled=True,
        market_state="SHOCK_UNSTABLE",
        market_state_confidence=0.9,
        policy=pol,
        change_point_market=cp,
        portfolio_stress_score=0.0,
        kill_risk_near_limit=True,
        btc_regime="risk_off",
        regime_stability_proxy=0.2,
        trace_id="t",
    )
    assert should_block_cycle_symbol(ctx, "ETH") is True
    assert should_block_cycle_symbol(ctx, "BTC") is True
