"""Unit tests for thesis zone-shift heuristic."""
from __future__ import annotations

from datetime import datetime

from core.market_data.client import Kline1h
from core.portfolio.models import Position
from core.profit.thesis_monitor import compute_zone_shift_and_state


def _make_cfg():
    return {
        "zone_level_thresholds": {"low": 0.35, "elevated": 0.55, "high": 0.75},
        "profiles": {
            "default": {
                "warning_zone_shift": 0.45,
                "danger_zone_shift": 0.65,
                "invalid_zone_shift": 0.85,
                "sl_extension_invalid_mult": 2.0,
            }
        },
    }


def test_long_normal_when_flat_klines():
    pos = Position(
        portfolio_id=1,
        symbol="BTC",
        side="long",
        strategy_name="momentum",
        entry_price=100.0,
        quantity=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=0.5,
        opened_at=datetime.utcnow(),
        is_open=True,
        scale_in_count=0,
        initial_entry_price=100.0,
        capital_bucket="core",
        thesis_type="generic",
        thesis_state="NORMAL",
    )
    klines = [Kline1h(100, 101, 99.5, 100.2, 1e6, 0) for _ in range(20)]
    r = compute_zone_shift_and_state(pos, 100.5, klines, _make_cfg())
    assert r["thesis_state"] == "NORMAL"
    assert r["zone_shift_risk_level"] in ("low", "elevated")


def test_long_invalid_when_price_far_below_sl_plane():
    pos = Position(
        portfolio_id=1,
        symbol="BTC",
        side="long",
        strategy_name="momentum",
        entry_price=100.0,
        quantity=1.0,
        stop_loss=98.0,
        take_profit=110.0,
        confidence=0.5,
        opened_at=datetime.utcnow(),
        is_open=True,
        scale_in_count=0,
        initial_entry_price=100.0,
        capital_bucket="core",
        thesis_type="generic",
        thesis_state="NORMAL",
    )
    klines = [Kline1h(100, 101, 99, 100.1, 1e6, 0) for _ in range(20)]
    # entry - 2 * (100-98) = 96
    r = compute_zone_shift_and_state(pos, 95.5, klines, _make_cfg())
    assert r["thesis_state"] == "INVALID"
    assert "deep_beyond_sl_plane" in r["reason_codes"]
