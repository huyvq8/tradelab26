from core.observability.context_gate_metrics import analyze_entry_context_gates


def test_analyze_entry_context_gates_counts():
    rows = [
        {"event": "entry_opened", "symbol": "SIREN", "reason_code": "ENTRY_OPENED"},
        {"event": "entry_rejected", "symbol": "SIREN", "strategy_name": "trend_following", "reason_code": "CONTEXT_GATE_DISTANCE_FROM_HIGH"},
        {"event": "entry_rejected", "symbol": "BTC", "strategy_name": "trend_following", "reason_code": "ENTRY_COOLDOWN"},
        {"event": "entry_rejected", "symbol": "SIREN", "strategy_name": "breakout_momentum", "reason_code": "CONTEXT_GATE_BARS_SINCE_HIGH"},
    ]
    m = analyze_entry_context_gates(rows)
    assert m["context_gate_reject_count"] == 2
    assert m["entry_rejected_total"] == 3
    assert m["by_reason_code"]["CONTEXT_GATE_DISTANCE_FROM_HIGH"] == 1
    assert m["filter_cost_vs_funnel"]["entry_funnel_total"] == 4


def test_should_log_context_pass():
    from core.signals.entry_context_gates import should_log_context_pass

    assert should_log_context_pass("SIREN", "trend_following", {"log_pass_snapshot": True}) is True
    assert should_log_context_pass("SIREN", "trend_following", {"log_pass_snapshot": False}) is False
    cfg = {
        "log_pass_snapshot": False,
        "pass_snapshot_debug": {"enabled": True, "symbols": ["SIREN"], "strategies": ["trend_following"]},
    }
    assert should_log_context_pass("SIREN", "trend_following", cfg) is True
    assert should_log_context_pass("BTC", "trend_following", cfg) is False
    assert should_log_context_pass("SIREN", "mean_reversion", cfg) is False
    cfg2 = {
        "pass_snapshot_debug": {"enabled": True, "symbols": [], "strategies": ["trend_following"]},
    }
    assert should_log_context_pass("ANY", "trend_following", cfg2) is True
