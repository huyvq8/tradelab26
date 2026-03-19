"""Brain V4 P1: policy overlay, gates, persistence helpers."""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def test_merge_proactive_exit_overlay_defensive():
    from core.brain.policy_apply import merge_proactive_exit_overlay

    base = {"partial_1r_min_r": 1.0, "proactive_exit_threshold": 0.6}
    out = merge_proactive_exit_overlay(base, "DEFENSIVE")
    assert out["partial_1r_min_r"] < 1.0
    assert out["proactive_exit_threshold"] < 0.6


def test_scale_in_policy_gate_defensive_blocks_high_cp():
    from core.brain.policy_apply import scale_in_policy_gate
    from core.brain.types import BrainV4CycleContext, ChangePointResult, PolicyDecision, PolicyModifiers

    cp_m = ChangePointResult(0.2, False, "NONE", "NONE", "NONE")  # type: ignore[arg-type]
    pol = PolicyDecision(
        "DEFENSIVE",
        0.7,
        [],
        120,
        60,
        PolicyModifiers(),
    )
    ctx = BrainV4CycleContext(
        enabled=True,
        market_state="BALANCED",
        market_state_confidence=0.5,
        policy=pol,
        change_point_market=cp_m,
        portfolio_stress_score=0.0,
        kill_risk_near_limit=False,
        btc_regime="balanced",
        regime_stability_proxy=0.5,
        trace_id="t",
    )
    assert scale_in_policy_gate(
        ctx,
        "ETH",
        change_point_score=0.5,
        position_state="THESIS_HEALTHY",
        prev_cp=0.1,
    ) is False


def test_apply_policy_size_breakdown_stress_step():
    from core.brain.policy_apply import apply_policy_size_breakdown
    from core.brain.types import BrainV4CycleContext, ChangePointResult, PolicyDecision, PolicyModifiers

    cp_m = ChangePointResult(0.2, False, "NONE", "NONE", "NONE")  # type: ignore[arg-type]
    mods = PolicyModifiers(size_multiplier=1.0)
    pol = PolicyDecision("DEFENSIVE", 0.7, [], 120, 60, mods)
    ctx = BrainV4CycleContext(
        enabled=True,
        market_state="BALANCED",
        market_state_confidence=0.5,
        policy=pol,
        change_point_market=cp_m,
        portfolio_stress_score=0.5,
        kill_risk_near_limit=False,
        btc_regime="balanced",
        regime_stability_proxy=0.5,
        trace_id="t",
    )
    final, bd = apply_policy_size_breakdown(100.0, ctx, symbol="BTC")
    assert bd["pre_modifier_usd"] == 100.0
    assert bd["post_stress_usd"] < bd["post_size_mult_usd"]
    assert final == bd["post_modifier_usd"]


def test_apply_policy_size_modifier_respects_multiplier():
    from core.brain.policy_apply import apply_policy_size_modifier
    from core.brain.types import BrainV4CycleContext, ChangePointResult, PolicyDecision, PolicyModifiers

    cp_m = ChangePointResult(0.2, False, "NONE", "NONE", "NONE")  # type: ignore[arg-type]
    mods = PolicyModifiers(size_multiplier=0.5)
    pol = PolicyDecision("NORMAL", 0.6, [], 120, 60, mods)
    ctx = BrainV4CycleContext(
        enabled=True,
        market_state="BALANCED",
        market_state_confidence=0.5,
        policy=pol,
        change_point_market=cp_m,
        portfolio_stress_score=0.0,
        kill_risk_near_limit=False,
        btc_regime="balanced",
        regime_stability_proxy=0.5,
        trace_id="t",
    )
    assert apply_policy_size_modifier(100.0, ctx, symbol="BTC") == 50.0


def test_symbol_policy_override_rules():
    from core.brain.symbol_policy import symbol_policy_override_allowed

    assert symbol_policy_override_allowed(
        context_break=True, position_state="THESIS_HEALTHY"
    )
    assert symbol_policy_override_allowed(
        context_break=False, position_state="THESIS_BROKEN"
    )
    assert not symbol_policy_override_allowed(
        context_break=False, position_state="THESIS_HEALTHY"
    )


def test_fetch_cycle_bundle_missing():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from core.db import Base
    from core.brain.persistence import fetch_cycle_bundle

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    try:
        import core.brain.models  # noqa: F401
    except ImportError:
        pass
    Base.metadata.create_all(bind=eng)
    Sess = sessionmaker(bind=eng)
    with Sess() as db:
        db: Session
        b = fetch_cycle_bundle(db, "00000000-0000-0000-0000-000000000000")
    assert b.get("error") == "cycle_not_found"
