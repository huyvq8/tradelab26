from core.observability.reject_reason_codes import (
    classify_risk_reject_reason_code,
    normalize_entry_reject_reason_code_for_summary,
)


def test_classify_bucket_scope_max_concurrent():
    reason = "Maximum concurrent trades reached (bucket scope)."
    assert classify_risk_reject_reason_code(reason) == "MAX_CONCURRENT_TRADES_BUCKET"


def test_summary_mapping_uses_bucket_scope_code_when_missing_reason_code():
    row = {
        "symbol": "C",
        "strategy_name": "mean_reversion",
        "reason": "Maximum concurrent trades reached (bucket scope).",
    }
    assert normalize_entry_reject_reason_code_for_summary(row) == "MAX_CONCURRENT_TRADES_BUCKET"
