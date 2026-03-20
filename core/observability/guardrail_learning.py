"""Append-only learning log for guardrail-related entry_opened / entry_rejected events."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_LEARN_PATH = _ROOT / "data" / "guardrail_learning.jsonl"
_MAX_BYTES = 3_000_000


def _rotate_if_huge() -> None:
    try:
        if _LEARN_PATH.exists() and _LEARN_PATH.stat().st_size > _MAX_BYTES:
            bak = _LEARN_PATH.with_suffix(".jsonl.bak")
            if bak.exists():
                bak.unlink()
            _LEARN_PATH.rename(bak)
    except Exception:
        pass


def _extract(row: dict[str, Any]) -> dict[str, Any]:
    ev = str(row.get("event") or "")
    pl = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    sym = str(row.get("symbol") or "")
    strat = str(row.get("strategy_name") or "")
    rc = str(row.get("reason_code") or "")
    base: dict[str, Any] = {
        "ts": row.get("ts"),
        "event": ev,
        "symbol": sym,
        "strategy": strat,
        "reason_code": rc,
        "regime": pl.get("regime") or (pl.get("native_signal") or {}).get("regime"),
    }
    if ev == "entry_rejected":
        base.update(
            {
                "stop_distance_pct": pl.get("stop_distance_pct"),
                "planned_r_multiple": pl.get("planned_r_multiple"),
                "final_notional_pct_of_equity": pl.get("final_notional_pct_of_equity"),
                "reversal_diagnostics": pl.get("reversal_diagnostics"),
                "blocking_stage": pl.get("blocking_stage"),
            }
        )
    elif ev == "entry_opened":
        rd = pl.get("reversal_diagnostics") or {}
        flags = rd.get("flags") if isinstance(rd, dict) else {}
        base.update(
            {
                "stop_distance_pct": pl.get("stop_distance_pct"),
                "planned_r_at_entry": pl.get("planned_r_multiple") or pl.get("planned_r_at_entry"),
                "final_notional_pct_of_equity": pl.get("final_notional_pct_of_equity"),
                "stop_floor_applied": bool(pl.get("stop_floor_applied")),
                "notional_cap_applied": bool((pl.get("notional_cap") or {}).get("applied")),
                "mr_reversal_flags": flags if isinstance(flags, dict) else {},
                "mr_reversal_types": [k for k, v in (flags or {}).items() if v]
                if isinstance(flags, dict)
                else [],
            }
        )
    return base


def append_guardrail_learning_from_decision_row(row: dict[str, Any]) -> None:
    ev = str(row.get("event") or "")
    if ev not in ("entry_rejected", "entry_opened"):
        return
    rc = str(row.get("reason_code") or "")
    if ev == "entry_opened" and rc and rc != "ENTRY_OPENED":
        return
    out = _extract(row)
    _LEARN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_huge()
    try:
        with open(_LEARN_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
