"""Read recent lines from data/decision_log.jsonl for dashboard / ops."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_PATH = _ROOT / "data" / "decision_log.jsonl"


def tail_decision_log_entries(
    *,
    limit: int = 120,
    symbols: set[str] | None = None,
    events: set[str] | None = None,
) -> list[dict]:
    """
    Last `limit` lines of JSONL (newest at end of file — we read tail of list after full read for small files).
    If symbols given, filter rows where symbol (upper) is in set.
    If events given, filter by event name (e.g. entry_rejected, entry_opened).
    """
    if not _LOG_PATH.exists():
        return []
    try:
        raw = _LOG_PATH.read_text(encoding="utf-8")
    except Exception:
        return []
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    sym_u = {s.strip().upper() for s in symbols} if symbols else None
    ev_l = {e.strip() for e in events} if events else None
    out = []
    for r in rows:
        if sym_u is not None:
            su = (r.get("symbol") or "").strip().upper()
            if su not in sym_u:
                continue
        if ev_l is not None and (r.get("event") or "") not in ev_l:
            continue
        out.append(r)
    return out[-max(1, limit) :]
