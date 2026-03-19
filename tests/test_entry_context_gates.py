"""Entry context gates: phase1 native + phase2/3 metrics."""
from __future__ import annotations

from core.strategies.base import StrategySignal
from core.signals.entry_context_gates import (
    evaluate_entry_context_gates,
    _compute_phase2_metrics,
)


def _sig(**kw):
    base = dict(
        symbol="X",
        strategy_name="trend_following",
        side="long",
        confidence=0.7,
        entry_price=100.0,
        stop_loss=97.0,
        take_profit=106.0,
        rationale="t",
        regime="high_momentum",
    )
    base.update(kw)
    return StrategySignal(**base)


def test_phase1_rejects_high_extension():
    s = _sig(extension_score=0.95, setup_quality=0.7, entry_style="trend_continuation")
    cfg = {
        "phase1_native_signal": {
            "enabled": True,
            "apply_to_strategies": ["trend_following"],
            "long_only": True,
            "extension_score_max": 0.88,
            "setup_quality_min": 0.5,
            "entry_style_blocklist": [],
            "when_native_fields_missing": "pass",
        }
    }
    r = evaluate_entry_context_gates(s, symbol="S", strategy_name="trend_following", side="long", price_now=100.0, klines=[], cfg=cfg)
    assert r.ok is False
    assert r.reason_code == "CONTEXT_GATE_EXTENSION_SCORE"


def test_phase1_passes_within_bounds():
    s = _sig(extension_score=0.5, setup_quality=0.7, entry_style="trend_continuation")
    cfg = {
        "phase1_native_signal": {
            "enabled": True,
            "apply_to_strategies": ["trend_following"],
            "long_only": True,
            "extension_score_max": 0.88,
            "setup_quality_min": 0.5,
            "entry_style_blocklist": [],
            "when_native_fields_missing": "pass",
        },
        "phase2_recent_context": {"enabled": False},
        "phase3_pullback_quality": {"enabled": False},
    }
    r = evaluate_entry_context_gates(s, symbol="S", strategy_name="trend_following", side="long", price_now=100.0, klines=[], cfg=cfg)
    assert r.ok is True


def test_phase2_distance_from_high():
    class C:
        def __init__(self, o, h, l, c, v=1.0):
            self.open, self.high, self.low, self.close, self.volume = o, h, l, c, v

    # High at 100, now price 99.5 -> 0.5% off — reject if min 1.0%
    klines = [C(99, 99.5, 98.5, 99) for _ in range(60)]
    klines.append(C(99.5, 100, 99.4, 99.8))
    m = _compute_phase2_metrics(klines, 99.5, {"lookback_bars": 72})
    assert m.get("recent_high") == 100.0
    assert m.get("distance_from_recent_high_pct", 100) < 1.0

    cfg = {
        "phase1_native_signal": {"enabled": False},
        "phase2_recent_context": {
            "enabled": True,
            "apply_to_strategies": ["trend_following"],
            "long_only": True,
            "lookback_bars": 72,
            "min_distance_from_high_pct": 1.0,
        },
        "phase3_pullback_quality": {"enabled": False},
    }
    s = _sig(extension_score=0.3, setup_quality=0.7, entry_style="trend_continuation")
    r = evaluate_entry_context_gates(s, symbol="S", strategy_name="trend_following", side="long", price_now=99.5, klines=klines, cfg=cfg)
    assert r.ok is False
    assert r.reason_code == "CONTEXT_GATE_DISTANCE_FROM_HIGH"
