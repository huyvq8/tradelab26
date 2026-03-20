from __future__ import annotations

from core.risk.entry_guardrails import (
    apply_notional_cap,
    evaluate_stop_floor_r_guard,
    mr_long_has_reversal_confirmation,
)
from core.strategies.base import StrategySignal


def _sig(entry: float, sl: float, tp: float) -> StrategySignal:
    return StrategySignal(
        symbol="BARD",
        strategy_name="mean_reversion",
        side="long",
        confidence=0.6,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        rationale="t",
        regime="risk_off",
    )


def test_stop_floor_applied():
    s = _sig(100.0, 99.5, 104.0)  # 0.5%
    out = evaluate_stop_floor_r_guard(
        s,
        guard_cfg={"min_stop_distance_pct_by_strategy_regime": {"mean_reversion": {"risk_off": 0.025}}},
        regime="risk_off",
        min_candidate_r=0.8,
    )
    assert out["stop_floor_applied"] is True
    assert round(float(out["stop_floor"]["new_pct"]), 4) == 0.025


def test_stop_floor_reject_when_planned_r_bad():
    s = _sig(100.0, 99.0, 100.6)
    out = evaluate_stop_floor_r_guard(
        s,
        guard_cfg={"min_stop_distance_pct_default": 0.03},
        regime="risk_off",
        min_candidate_r=0.8,
    )
    assert out["stop_floor_applied"] is True
    assert out["reject_low_r_after_floor"] is True


def test_notional_cap_applied():
    cap = apply_notional_cap(120.0, 80.0)
    assert cap["applied"] is True
    assert cap["final_size_usd"] == 80.0


def test_mr_no_reversal_confirmation():
    klines = [
        [0, 100, 101, 96, 97, 1000],
        [1, 97, 98, 94, 94.2, 900],
        [2, 94.2, 94.5, 92.8, 93.0, 850],
    ]
    ok, _ = mr_long_has_reversal_confirmation(klines, cfg={})
    assert ok is False
