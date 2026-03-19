"""Entry timing, signal level adjust, combo multiplier helpers."""
from __future__ import annotations

import json

from core.strategies.base import StrategySignal
from core.signals.entry_timing import evaluate_entry_timing, record_entry_opened
from core.profit.signal_level_adjust import adjust_signal_sl_tp
from core.profit.strategy_weight_engine import get_combo_multiplier


def _c(o, h, low, c):
    class K:
        pass

    k = K()
    k.open, k.high, k.low, k.close = o, h, low, c
    return k


def test_entry_timing_cooldown_reject(tmp_path, monkeypatch):
    from core.signals import entry_timing as et_mod

    monkeypatch.setattr(et_mod, "_ROOT", tmp_path)
    cfg = {
        "enabled": True,
        "apply_to_strategies": ["trend_following"],
        "extended_candle": {"enabled": False},
        "pullback": {"enabled": False},
        "cooldown": {
            "enabled": True,
            "seconds_between_entries_per_symbol": 3600,
            "storage_relative": "storage/cd.json",
        },
    }
    record_entry_opened("TEST", cfg)
    klines = [_c(99, 101, 98.5, 100.2)]
    r = evaluate_entry_timing(
        strategy_name="trend_following",
        symbol="TEST",
        side="long",
        price_now=100.0,
        klines_1h=klines,
        cfg=cfg,
    )
    assert r.ok is False
    assert r.reason_code == "ENTRY_COOLDOWN"


def test_signal_level_adjust_caps_tp():
    sig = StrategySignal("X", "trend_following", "long", 0.7, 100.0, 97.0, 110.0, "x", "high_momentum")
    klines = [_c(99, 101, 99.2, 100.0) for _ in range(20)]
    meta = adjust_signal_sl_tp(
        sig,
        klines,
        {
            "enabled": True,
            "atr_period": 14,
            "sl_atr_mult": 1.0,
            "tp_atr_mult": 2.0,
            "min_sl_pct": 0.9,
            "max_sl_pct": 4.0,
            "max_tp_pct": 4.0,
            "min_tp_pct": 1.0,
        },
    )
    assert meta.get("adjusted") is True
    tp_pct = abs(sig.take_profit - 100.0) / 100.0 * 100.0
    assert tp_pct <= 4.05


def test_get_combo_multiplier():
    m = {"trend_following|BTC": 0.0, "mean_reversion|BTC": 0.5}
    assert get_combo_multiplier(m, "trend_following", "BTC") == 0.0
    assert get_combo_multiplier(m, "mean_reversion", "BTC") == 0.5
    assert get_combo_multiplier({}, "trend_following", "ETH") == 1.0


def test_get_combo_multiplier_regime_precedence():
    m = {
        "trend_following|BTC|high_momentum": 0.0,
        "trend_following|BTC|risk_off": 1.0,
        "trend_following|BTC": 0.5,
    }
    assert get_combo_multiplier(m, "trend_following", "BTC", "high_momentum") == 0.0
    assert get_combo_multiplier(m, "trend_following", "BTC", "risk_off") == 1.0
    assert get_combo_multiplier(m, "trend_following", "BTC", "balanced") == 0.5


def test_get_combo_multiplier_side_precedence_over_regime_pair():
    m = {
        "trend_following|BTC|high_momentum|long": 0.25,
        "trend_following|BTC|high_momentum": 0.5,
        "trend_following|BTC": 1.0,
    }
    assert (
        get_combo_multiplier(m, "trend_following", "BTC", "high_momentum", side="long") == 0.25
    )
    assert get_combo_multiplier(m, "trend_following", "BTC", "high_momentum", side="short") == 0.5


def test_log_rejected_has_reason_code(tmp_path, monkeypatch):
    from core import rejected_signals_log as rsl

    bf = tmp_path / "blocked_signals.json"
    monkeypatch.setattr(rsl, "BLOCKED_FILE", bf)
    rsl.log_rejected("BTC", "tf", "no", reason_code="RISK_X", meta={"k": 1})
    data = json.loads(bf.read_text(encoding="utf-8"))
    assert data[-1].get("reason_code") == "RISK_X"
    assert data[-1].get("meta", {}).get("k") == 1
