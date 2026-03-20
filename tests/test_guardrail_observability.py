"""Guardrail dashboard helpers, dedupe, profile merge, MR kline adapter."""
from __future__ import annotations

import json
from pathlib import Path

from core.observability.guardrail_row import row_from_decision_event
from core.observability.reject_dedupe import reset_dedupe_memory_state, should_emit_entry_reject
from core.observability.telegram_signal_dedupe import reset_telegram_signal_dedupe_memory, should_send_signal_telegram
from core.risk.entry_guardrails import mr_long_has_reversal_confirmation
from core.strategies.base import StrategySignal


def test_row_from_decision_event_opened():
    r = row_from_decision_event(
        {
            "ts": "2026-01-01T00:00:00Z",
            "event": "entry_opened",
            "symbol": "BTC",
            "strategy_name": "mean_reversion",
            "reason_code": "ENTRY_OPENED",
            "payload": {
                "stop_distance_pct": 0.02,
                "final_notional_pct_of_equity": 0.05,
                "risk_efficiency_ratio": 0.8,
                "stop_floor_applied": True,
                "notional_cap": {"applied": False},
                "reversal_diagnostics": {"flags": {"lower_wick": True, "recovery_close": False}},
            },
        }
    )
    assert r["stop_floor_applied"] == "yes"
    assert r["mr_reversal_confirmation"] == "yes"


def test_mr_long_accepts_kline1h_dataclass():
    from core.market_data.client import Kline1h

    bars = [
        Kline1h(open=100.0, high=102.0, low=99.0, close=101.0, volume=1000.0, open_time_ms=0),
        Kline1h(open=101.0, high=101.5, low=100.0, close=100.2, volume=900.0, open_time_ms=1),
        Kline1h(open=100.2, high=100.4, low=99.5, close=100.35, volume=800.0, open_time_ms=2),
    ]
    ok, diag = mr_long_has_reversal_confirmation(bars, cfg={"lower_wick_to_body_min": 0.01})
    assert isinstance(ok, bool)
    assert "flags" in diag


def test_reject_dedupe_mr_respects_cooldown(monkeypatch, tmp_path):
    import core.observability.reject_dedupe as rd

    reset_dedupe_memory_state()
    cfg = {
        "enabled": True,
        "dedupe_key_includes_candle_id": False,
        "default_cooldown_seconds": 3600,
        "per_code_cooldown_seconds": {"MR_NO_REVERSAL_CONFIRMATION": 3600},
        "never_dedupe_codes": [],
        "state_change_key_paths": {
            "MR_NO_REVERSAL_CONFIRMATION": ["stop_distance_pct", "planned_r_multiple", "entry_price"]
        },
        "default_state_key_paths": [],
    }
    p = tmp_path / "reject_log_dedupe.v1.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(rd, "_DEDUPE_CFG", p)
    payload = {
        "side": "long",
        "stop_distance_pct": 0.03,
        "planned_r_multiple": 1.2,
        "entry_price": 50.0,
    }
    assert should_emit_entry_reject("BTC", "mean_reversion", "MR_NO_REVERSAL_CONFIRMATION", payload)
    assert not should_emit_entry_reject("BTC", "mean_reversion", "MR_NO_REVERSAL_CONFIRMATION", payload)


def test_telegram_signal_dedupe():
    reset_telegram_signal_dedupe_memory()
    sig = {"symbol": "ETH", "strategy_name": "trend_following", "entry_price": 100.0, "stop_loss": 99.0}
    assert should_send_signal_telegram(sig)
    assert not should_send_signal_telegram(sig)


def test_load_profit_config_resolved_merge(tmp_path, monkeypatch):
    from core.profit import profit_config_resolve as pcr
    from core.profit import volatility_guard as vg

    root = tmp_path
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True)
    active = {
        "entry_guardrail_profile": "MR_SAFE",
        "entry_guardrails": {"min_stop_distance_pct_default": 0.01},
    }
    (cfg_dir / "profit.active.json").write_text(json.dumps(active), encoding="utf-8")
    prof = {
        "MR_SAFE": {"entry_guardrails": {"min_stop_distance_pct_default": 0.99}},
    }
    (cfg_dir / "mr_guardrail_profiles.v1.json").write_text(json.dumps(prof), encoding="utf-8")
    monkeypatch.setattr(pcr, "_PROJECT_ROOT", root)
    monkeypatch.setattr(vg, "_PROFIT_ACTIVE_PATH", cfg_dir / "profit.active.json")
    out = pcr.load_profit_config_resolved()
    assert float(out["entry_guardrails"]["min_stop_distance_pct_default"]) == 0.99
