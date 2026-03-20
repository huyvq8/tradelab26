import json

from core.observability.decision_log_tail import tail_decision_log_entries


def test_tail_decision_log_filters(tmp_path, monkeypatch):
    p = tmp_path / "decision_log.jsonl"
    rows = [
        {"ts": "1", "event": "entry_opened", "symbol": "BTC", "strategy_name": "trend_following", "reason_code": "ENTRY_OPENED"},
        {"ts": "2", "event": "entry_rejected", "symbol": "SIREN", "strategy_name": "mean_reversion", "reason_code": "VOL_X"},
        {"ts": "3", "event": "entry_rejected", "symbol": "BTC", "strategy_name": "trend_following", "reason_code": "X"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr("core.observability.decision_log_tail._LOG_PATH", p)

    out = tail_decision_log_entries(limit=10, symbols={"SIREN"}, events={"entry_rejected"})
    assert len(out) == 1
    assert out[0]["symbol"] == "SIREN"

    p2 = tmp_path / "decision_log2.jsonl"
    rows2 = [
        {"ts": "1", "event": "entry_rejected", "symbol": "BTC", "strategy_name": "x", "reason_code": "X"},
        {
            "ts": "2",
            "event": "cycle_execution_summary",
            "symbol": None,
            "strategy_name": "mean_reversion",
            "reason_code": "CYCLE_SUMMARY",
            "payload": {"opened_symbols": ["FOO"]},
        },
    ]
    p2.write_text("\n".join(json.dumps(r) for r in rows2), encoding="utf-8")
    monkeypatch.setattr("core.observability.decision_log_tail._LOG_PATH", p2)
    out2 = tail_decision_log_entries(
        limit=10,
        symbols={"SIREN"},
        events={"entry_rejected", "cycle_execution_summary"},
        always_include_events={"cycle_execution_summary"},
    )
    assert len(out2) == 1
    assert out2[0]["event"] == "cycle_execution_summary"
