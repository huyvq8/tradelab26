"""Post-sizing reject root-cause classification."""
from __future__ import annotations

from core.risk.sizing_reject_diagnosis import (
    classify_post_sizing_reject,
    effective_internal_min_trade_usd,
)


def test_effective_internal_mr_floor():
    cfg = {"sizing": {"internal_min_trade_usd": 25, "mr_only_min_trade_usd": 12}}
    assert effective_internal_min_trade_usd(cfg, single_strategy_mode="mean_reversion") == 12.0
    assert effective_internal_min_trade_usd(cfg, single_strategy_mode=None) == 25.0


def test_classify_below_internal():
    code, d = classify_post_sizing_reject(
        post_modifier_usd=10.0,
        pre_modifier_usd=50.0,
        post_policy_usd=48.0,
        internal_min_trade_usd=25.0,
        mod_breakdown=None,
        exchange_preview=None,
    )
    assert code == "BELOW_INTERNAL_MIN_TRADE_USD"
    assert d["blocking_stage"] == "below_internal_min_trade_usd"


def test_classify_reduced_by_policy():
    code, d = classify_post_sizing_reject(
        post_modifier_usd=10.0,
        pre_modifier_usd=100.0,
        post_policy_usd=30.0,
        internal_min_trade_usd=25.0,
        mod_breakdown={"post_modifier_usd": 10.0},
        exchange_preview=None,
    )
    assert code == "REDUCED_TOO_MUCH_BY_POLICY"
    assert "policy" in (d.get("blocking_stage") or "")


def test_classify_exchange_min_notional():
    prev = {
        "min_qty": 0.001,
        "step_size": "0.001",
        "exchange_min_notional": 100.0,
        "rounded_qty": 0.01,
        "rounded_notional_usd": 50.0,
        "exchange_flags": ["BELOW_EXCHANGE_MIN_NOTIONAL"],
    }
    code, d = classify_post_sizing_reject(
        post_modifier_usd=50.0,
        pre_modifier_usd=50.0,
        post_policy_usd=50.0,
        internal_min_trade_usd=25.0,
        mod_breakdown=None,
        exchange_preview=prev,
    )
    assert code == "BELOW_EXCHANGE_MIN_NOTIONAL"
    assert d["blocking_stage"] == "exchange_min_notional"
