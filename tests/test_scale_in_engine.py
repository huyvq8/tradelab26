"""
Tests for Smart Scale-In Engine (document/budget).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from core.position import ScaleInEngine, ScaleInAction, load_scale_in_config
from core.position.scale_in_engine import _position_age_seconds, _unrealized_pnl, _effective_quality_score
from core.portfolio.models import Position, Portfolio
from core.strategies.base import StrategySignal


class _MockPosition:
    """Minimal position-like object for tests."""
    def __init__(self, symbol="BTCUSDT", side="long", quantity=0.01, entry_price=50000.0, stop_loss=49000.0,
                 strategy_name="trend_following", scale_in_count=0, initial_entry_price=None, opened_at=None):
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.strategy_name = strategy_name
        self.scale_in_count = scale_in_count
        self.initial_entry_price = initial_entry_price
        self.opened_at = opened_at or datetime.now(timezone.utc)


def test_effective_quality_score_uses_confidence_when_quality_none():
    sig = StrategySignal(
        symbol="X", strategy_name="s", side="long", confidence=0.8,
        entry_price=1.0, stop_loss=0.9, take_profit=1.2, rationale="", regime="trend",
        quality_score=None,
    )
    assert _effective_quality_score(sig) == 0.8


def test_effective_quality_score_uses_quality_when_set():
    sig = StrategySignal(
        symbol="X", strategy_name="s", side="long", confidence=0.7,
        entry_price=1.0, stop_loss=0.9, take_profit=1.2, rationale="", regime="trend",
        quality_score=0.85,
    )
    assert _effective_quality_score(sig) == 0.85


def test_unrealized_pnl_long():
    pos = _MockPosition(side="long", quantity=1.0, entry_price=100.0)
    assert _unrealized_pnl(pos, 110.0) == 10.0
    assert _unrealized_pnl(pos, 90.0) == -10.0


def test_unrealized_pnl_short():
    pos = _MockPosition(side="short", quantity=1.0, entry_price=100.0)
    assert _unrealized_pnl(pos, 90.0) == 10.0
    assert _unrealized_pnl(pos, 110.0) == -10.0


def test_reject_when_scale_in_disabled():
    cfg = {"scale_in": {"enabled": False}}
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(scale_in_count=0, opened_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    port = Portfolio(name="p", capital_usd=10000.0)
    sig = StrategySignal("BTCUSDT", "trend", "long", 0.8, 50000.0, 49000.0, 52000.0, "r", "trend")
    decision = engine.evaluate(sig, pos, 50050.0, port, [pos])
    assert decision.action == ScaleInAction.REJECT_SCALE_IN
    assert "disabled" in decision.reason.lower()


def test_unlimited_max_scale_in_times_ignores_high_count():
    """max_scale_in_times=0 → không reject vì count (vẫn có thể reject vì risk/zone/...)."""
    cfg = load_scale_in_config()
    cfg["scale_in"] = {
        **cfg.get("scale_in", {}),
        "enabled": True,
        "max_scale_in_times": 0,
        "allow_scale_in_when_pnl_negative": True,
        "cooldown_between_scale_ins_seconds": 0,
    }
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(
        scale_in_count=10,
        quantity=0.1,
        entry_price=50000.0,
        stop_loss=48000.0,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    port = Portfolio(name="p", capital_usd=100000.0)
    sig = StrategySignal(
        "BTCUSDT", "trend", "long", 0.9, 50000.0, 48000.0, 52000.0, "r", "trend", quality_score=0.85,
    )
    # Giá hơi lệch entry để qua min_add_distance_pct; vốn lớn để qua min add.
    decision = engine.evaluate(sig, pos, 49850.0, port, [pos])
    assert decision.reason != "max_scale_in_reached"


def test_scale_in_cooldown_active():
    cfg = load_scale_in_config()
    cfg["scale_in"] = {
        **cfg.get("scale_in", {}),
        "enabled": True,
        "max_scale_in_times": 0,
        "cooldown_between_scale_ins_seconds": 900,
        "allow_scale_in_when_pnl_negative": True,
    }
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(
        scale_in_count=1,
        quantity=0.1,
        entry_price=50000.0,
        stop_loss=48000.0,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    port = Portfolio(name="p", capital_usd=100000.0)
    sig = StrategySignal(
        "BTCUSDT", "trend", "long", 0.9, 50000.0, 48000.0, 52000.0, "r", "trend", quality_score=0.85,
    )
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)
    decision = engine.evaluate(sig, pos, 49850.0, port, [pos], last_scale_in_at=recent)
    assert decision.action == ScaleInAction.REJECT_SCALE_IN
    assert "cooldown" in decision.reason.lower()


def test_reject_max_scale_in_reached():
    cfg = load_scale_in_config()
    cfg["scale_in"] = {**cfg.get("scale_in", {}), "enabled": True, "max_scale_in_times": 1}
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(scale_in_count=1, opened_at=datetime.now(timezone.utc) - timedelta(hours=1))
    port = Portfolio(name="p", capital_usd=10000.0)
    sig = StrategySignal("BTCUSDT", "trend", "long", 0.85, 50000.0, 49000.0, 52000.0, "r", "trend", quality_score=0.8)
    decision = engine.evaluate(sig, pos, 50050.0, port, [pos])
    assert decision.action == ScaleInAction.REJECT_SCALE_IN
    assert "max_scale_in" in decision.reason.lower()


def test_reject_negative_pnl_when_not_allowed():
    cfg = load_scale_in_config()
    cfg["scale_in"] = {**cfg.get("scale_in", {}), "enabled": True, "allow_scale_in_when_pnl_negative": False}
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(entry_price=50000.0, quantity=0.01, opened_at=datetime.now(timezone.utc) - timedelta(hours=1))
    port = Portfolio(name="p", capital_usd=10000.0)
    sig = StrategySignal("BTCUSDT", "trend", "long", 0.85, 49900.0, 49000.0, 52000.0, "r", "trend", quality_score=0.8)
    # current price below entry -> unrealized loss for long
    decision = engine.evaluate(sig, pos, 49500.0, port, [pos])
    assert decision.action == ScaleInAction.REJECT_SCALE_IN
    assert "negative" in decision.reason.lower() or "pnl" in decision.reason.lower()


def test_reject_opposite_side():
    cfg = load_scale_in_config()
    cfg["scale_in"] = {**cfg.get("scale_in", {}), "enabled": True}
    engine = ScaleInEngine(cfg)
    pos = _MockPosition(side="short", opened_at=datetime.now(timezone.utc) - timedelta(hours=1))
    port = Portfolio(name="p", capital_usd=10000.0)
    sig = StrategySignal("BTCUSDT", "trend", "long", 0.85, 50000.0, 49000.0, 52000.0, "r", "trend", quality_score=0.8)
    decision = engine.evaluate(sig, pos, 50050.0, port, [pos])
    assert decision.action == ScaleInAction.REJECT_SCALE_IN
    assert "opposite" in decision.reason.lower()
