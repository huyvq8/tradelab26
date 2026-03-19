"""RiskEngine + capital_scope (core/fast bucket)."""
from __future__ import annotations

from core.risk.engine import RiskEngine
from core.strategies.base import StrategySignal


def _sig():
    return StrategySignal(
        symbol="BTCUSDT",
        strategy_name="trend_following",
        side="long",
        confidence=0.8,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        rationale="test",
        regime="high_momentum",
    )


def test_legacy_assess_unchanged_when_no_scope():
    eng = RiskEngine()
    s = _sig()
    d = eng.assess(s, 10_000.0, 0, 0.0, daily_realized_r=0.0, consecutive_loss_count=0, capital_usd_for_risk=10_000.0)
    assert d.approved is True
    assert d.size_usd >= 25


def test_fast_scope_blocks_when_max_concurrent():
    eng = RiskEngine()
    s = _sig()
    d = eng.assess(
        s,
        10_000.0,
        5,
        0.0,
        daily_realized_r=0.0,
        consecutive_loss_count=0,
        capital_usd_for_risk=10_000.0,
        capital_scope="fast",
        open_positions_in_scope=3,
        daily_realized_pnl_in_scope=0.0,
        risk_capital_for_scope=3000.0,
        max_concurrent_in_scope=3,
        max_daily_loss_pct_in_scope=0.03,
        consecutive_loss_in_scope=0,
        max_consecutive_loss_for_scope=0,
    )
    assert d.approved is False
    assert "concurrent" in d.reason.lower()


def test_fast_scope_daily_loss():
    eng = RiskEngine()
    s = _sig()
    d = eng.assess(
        s,
        10_000.0,
        0,
        0.0,
        daily_realized_r=0.0,
        consecutive_loss_count=0,
        capital_usd_for_risk=10_000.0,
        capital_scope="fast",
        open_positions_in_scope=0,
        daily_realized_pnl_in_scope=-100.0,
        risk_capital_for_scope=3000.0,
        max_concurrent_in_scope=5,
        max_daily_loss_pct_in_scope=0.025,
        consecutive_loss_in_scope=0,
        max_consecutive_loss_for_scope=0,
    )
    assert d.approved is False
    assert "daily" in d.reason.lower()
