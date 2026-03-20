"""
Append-only JSONL decision trail (entry rejects, combo blocks, key exits).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_PATH = _ROOT / "data" / "decision_log.jsonl"
_MAX_BYTES = 2_000_000


def _rotate_if_huge() -> None:
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_BYTES:
            bak = _LOG_PATH.with_suffix(".jsonl.bak")
            if bak.exists():
                bak.unlink()
            _LOG_PATH.rename(bak)
    except Exception:
        pass


def log_decision(
    event: str,
    payload: dict[str, Any] | None = None,
    *,
    symbol: str | None = None,
    strategy_name: str | None = None,
    reason_code: str | None = None,
    skip_entry_reject_dedupe: bool = False,
) -> None:
    pl = payload or {}
    if event == "entry_rejected" and not skip_entry_reject_dedupe:
        try:
            from core.observability.reject_dedupe import should_emit_entry_reject

            if not should_emit_entry_reject(symbol, strategy_name, reason_code, pl):
                return
        except Exception:
            pass
    try:
        from core.experiments.paths import experiment_labels

        exp = experiment_labels()
    except Exception:
        exp = {}
    reject_bucket = ""
    if event == "entry_rejected":
        try:
            from core.observability.reject_classification import classify_entry_reject

            reject_bucket = classify_entry_reject(reason_code)
        except Exception:
            reject_bucket = ""
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "symbol": symbol,
        "strategy_name": strategy_name,
        "reason_code": reason_code or "",
        "experiment": exp,
        "payload": pl,
    }
    if reject_bucket:
        row["reject_bucket"] = reject_bucket
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_huge()
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    if event in ("entry_rejected", "entry_opened"):
        try:
            from core.observability.guardrail_learning import append_guardrail_learning_from_decision_row

            append_guardrail_learning_from_decision_row(row)
        except Exception:
            pass
