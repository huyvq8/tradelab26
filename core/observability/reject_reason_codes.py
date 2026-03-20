"""Canonical reason_code mapping for entry rejects and summaries."""
from __future__ import annotations

from typing import Any


def classify_risk_reject_reason_code(reason: str | None) -> str:
    txt = (reason or "").strip()
    if not txt:
        return "RISK_REJECTED"
    low = txt.lower()
    if "maximum concurrent trades reached" in low and "bucket scope" in low:
        return "MAX_CONCURRENT_TRADES_BUCKET"
    if "maximum concurrent trades reached" in low:
        return "MAX_CONCURRENT_TRADES"
    if "daily loss limit reached" in low and "bucket scope" in low:
        return "BUCKET_SCOPE_DAILY_LOSS_LIMIT"
    if "daily loss limit reached" in low:
        return "DAILY_LOSS_LIMIT"
    if "consecutive" in low and "bucket scope" in low:
        return "BUCKET_SCOPE_CONSECUTIVE_LOSS_STOP"
    return "RISK_REJECTED"


def normalize_entry_reject_reason_code_for_summary(row: dict[str, Any]) -> str:
    code = str(row.get("reason_code") or "").strip().upper()
    if code:
        return code
    return classify_risk_reject_reason_code(str(row.get("reason") or ""))
