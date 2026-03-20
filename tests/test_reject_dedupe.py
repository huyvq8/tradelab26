"""Reject dedupe + classification."""
from __future__ import annotations

from core.observability.reject_classification import classify_entry_reject
from core.observability.reject_dedupe import reset_dedupe_memory_state, should_emit_entry_reject
from core.observability.decision_log import log_decision
from pathlib import Path
import json


def test_classify_buckets():
    assert classify_entry_reject("ENTRY_LOW_VOLUME_SPIKE") == "good_reject"
    assert classify_entry_reject("CONTEXT_GATE_DISTANCE_FROM_HIGH") == "good_reject"
    assert classify_entry_reject("SCALE_IN_REJECTED") == "policy_reject"
    assert classify_entry_reject("SIZE_TOO_SMALL_POST_SIZING") == "sizing_reject"
    assert classify_entry_reject("BELOW_INTERNAL_MIN_TRADE_USD") == "sizing_reject"
    assert classify_entry_reject("REDUCED_TOO_MUCH_BY_POLICY") == "sizing_reject"
    assert classify_entry_reject("NOTIONAL_CAPPED_BY_POLICY") == "sizing_reject"
    assert classify_entry_reject("MR_NO_REVERSAL_CONFIRMATION") == "good_reject"
    assert classify_entry_reject("STOP_DISTANCE_TOO_TIGHT") == "good_reject"
    assert classify_entry_reject("PRE_SIZING_BELOW_MIN_EXECUTABLE") == "sizing_reject"


def test_dedupe_same_state_skips_second():
    reset_dedupe_memory_state()
    pl = {
        "side": "long",
        "confirmation_volume_spike_ratio": 0.01,
        "confirmation_body_range_ratio": 0.3,
        "candle_id": "1h:100",
    }
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl) is True
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl) is False


def test_dedupe_new_candle_re_emits():
    reset_dedupe_memory_state()
    pl1 = {
        "side": "long",
        "confirmation_volume_spike_ratio": 0.01,
        "confirmation_body_range_ratio": 0.3,
        "candle_id": "1h:111",
    }
    pl2 = {**pl1, "candle_id": "1h:222"}
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl1) is True
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl1) is False
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl2) is True


def test_dedupe_state_change_re_emits():
    reset_dedupe_memory_state()
    pl1 = {
        "side": "long",
        "confirmation_volume_spike_ratio": 0.01,
        "confirmation_body_range_ratio": 0.3,
        "candle_id": "1h:300",
    }
    pl2 = {
        "side": "long",
        "confirmation_volume_spike_ratio": 0.99,
        "confirmation_body_range_ratio": 0.3,
        "candle_id": "1h:300",
    }
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl1) is True
    assert should_emit_entry_reject("BTC", "trend_following", "ENTRY_LOW_VOLUME_SPIKE", pl2) is True


def test_never_dedupe_size():
    reset_dedupe_memory_state()
    pl = {"side": "long", "sizing_trace": {"post_all_modifiers_usd": 10}}
    assert should_emit_entry_reject("BTC", "trend_following", "BELOW_INTERNAL_MIN_TRADE_USD", pl) is True
    assert should_emit_entry_reject("BTC", "trend_following", "BELOW_INTERNAL_MIN_TRADE_USD", pl) is True


def test_log_decision_adds_reject_bucket(tmp_path, monkeypatch):
    reset_dedupe_memory_state()
    log_file = tmp_path / "decision_log.jsonl"
    monkeypatch.setattr("core.observability.decision_log._LOG_PATH", log_file)
    log_decision(
        "entry_rejected",
        {"side": "long", "confirmation_volume_spike_ratio": 0.5},
        symbol="ETH",
        strategy_name="mean_reversion",
        reason_code="ENTRY_COOLDOWN",
        skip_entry_reject_dedupe=True,
    )
    line = log_file.read_text(encoding="utf-8").strip()
    row = json.loads(line)
    assert row.get("reject_bucket") == "good_reject"
