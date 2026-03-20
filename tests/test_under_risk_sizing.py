"""Risk ceiling + optional under-risk lift after modifiers."""
from __future__ import annotations

from core.risk.under_risk_sizing import apply_risk_ceiling_and_under_risk_floor


def test_caps_above_risk_ceiling():
    out, meta = apply_risk_ceiling_and_under_risk_floor(
        final_size_usd=500.0,
        post_risk_engine_usd=100.0,
        eff_min_trade_usd=25.0,
        available_cash=1000.0,
        sizing_cfg={},
    )
    assert out == 100.0
    assert meta == {}


def test_under_risk_lift_when_below_fraction():
    out, meta = apply_risk_ceiling_and_under_risk_floor(
        final_size_usd=20.0,
        post_risk_engine_usd=100.0,
        eff_min_trade_usd=12.0,
        available_cash=1000.0,
        sizing_cfg={"under_risk_min_fraction_of_risk_ceiling": 0.65},
    )
    assert out == 65.0
    assert meta.get("under_risk_rescale", {}).get("to_usd") == 65.0


def test_no_lift_when_below_eff_min():
    out, meta = apply_risk_ceiling_and_under_risk_floor(
        final_size_usd=10.0,
        post_risk_engine_usd=100.0,
        eff_min_trade_usd=12.0,
        available_cash=1000.0,
        sizing_cfg={"under_risk_min_fraction_of_risk_ceiling": 0.65},
    )
    assert out == 10.0
    assert "under_risk_rescale" not in meta


def test_respect_available_cash():
    out, meta = apply_risk_ceiling_and_under_risk_floor(
        final_size_usd=20.0,
        post_risk_engine_usd=100.0,
        eff_min_trade_usd=12.0,
        available_cash=50.0,
        sizing_cfg={"under_risk_min_fraction_of_risk_ceiling": 0.65},
    )
    assert out == 50.0
    assert meta["under_risk_rescale"]["to_usd"] == 50.0
