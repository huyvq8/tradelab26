"""Bot edge mode selection and helpers."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from core.profit.bot_edge_controller import (
    apply_tp_profile_to_signal,
    compute_bot_edge_decision,
    effective_min_signal_score,
    effective_signal_score,
    rolling_portfolio_profit_factor,
)
from core.strategies.base import StrategySignal


def test_effective_signal_score_prefers_quality():
    s = StrategySignal("X", "t", "long", 0.6, 100.0, 95.0, 110.0, "r", "balanced", quality_score=0.71)
    assert effective_signal_score(s) == 0.71


def test_apply_tp_profile_tight_long():
    s = StrategySignal("X", "t", "long", 0.7, 100.0, 95.0, 110.0, "r", "balanced")
    cfg = {"tp_profile_scales": {"tight": 0.5, "normal": 1.0}}
    apply_tp_profile_to_signal(s, "tight", cfg)
    assert s.take_profit == 105.0


def test_compute_bot_edge_defensive_on_daily_r(monkeypatch):
    monkeypatch.setattr(
        "core.profit.bot_edge_controller.load_bot_edge_config",
        lambda: {
            "enabled": True,
            "default_mode": "CORE",
            "selection": {
                "defensive": {"daily_r_max": -1.0},
                "fast": {"rolling_pf_min": 99.0, "anchor_regimes_allow": ["high_momentum"]},
            },
            "modes": {
                "DEFENSIVE": {
                    "risk_multiplier": 0.4,
                    "tp_profile": "tight",
                    "max_hold_minutes_fast": 30,
                    "max_hold_minutes_core": 60,
                    "min_signal_score": 0.8,
                    "allow_scale_in": False,
                    "allow_fast_bucket": False,
                    "max_concurrent_trades": 1,
                },
                "CORE": {
                    "risk_multiplier": 1.0,
                    "tp_profile": "normal",
                    "max_hold_minutes_fast": 90,
                    "max_hold_minutes_core": 0,
                    "min_signal_score": 0.5,
                    "allow_scale_in": True,
                    "allow_fast_bucket": True,
                    "max_concurrent_trades": 3,
                },
            },
        },
    )
    monkeypatch.setattr(
        "core.profit.bot_edge_controller.rolling_portfolio_profit_factor",
        lambda *a, **k: (1.5, 20),
    )
    db = MagicMock()
    d = compute_bot_edge_decision(
        db,
        1,
        quotes={"BTC": MagicMock(percent_change_24h=1.0, volume_24h=1e9)},
        daily_realized_r=-1.5,
        daily_realized_pnl_usd=-10.0,
        risk_capital_usd=1000.0,
    )
    assert d.selected_mode == "DEFENSIVE"
    assert d.max_concurrent_trades == 1
    assert d.allow_fast_bucket is False


def test_effective_min_signal_score_per_strategy():
    cfg = {
        "bot_edge_min_by_mode": {
            "DEFENSIVE": {"default": 0.78, "mean_reversion": 0.62},
            "CORE": {"default": 0.62},
        }
    }
    assert (
        effective_min_signal_score(
            cfg,
            selected_mode="DEFENSIVE",
            strategy_name="mean_reversion",
            mode_default_min=0.78,
        )
        == 0.62
    )
    assert (
        effective_min_signal_score(
            cfg,
            selected_mode="DEFENSIVE",
            strategy_name="trend_following",
            mode_default_min=0.78,
        )
        == 0.78
    )
    assert (
        effective_min_signal_score(
            cfg,
            selected_mode="CORE",
            strategy_name="mean_reversion",
            mode_default_min=0.62,
        )
        == 0.62
    )


def test_rolling_pf_empty_db():
    db = MagicMock()
    db.scalars = MagicMock(return_value=MagicMock(return_value=[]))
    pf, n = rolling_portfolio_profit_factor(db, 1, lookback_days=7)
    assert pf is None and n == 0
