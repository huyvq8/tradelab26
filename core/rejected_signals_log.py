"""
Log rejected (blocked) signals for dashboard v4 Blocked Trades block.
Appends to data/blocked_signals.json, keeps last 100 entries.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BLOCKED_FILE = _DATA_DIR / "blocked_signals.json"
MAX_ENTRIES = 100


def log_rejected(
    symbol: str,
    strategy_name: str,
    reason: str,
    *,
    reason_code: str | None = None,
    meta: dict | None = None,
) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "symbol": symbol,
        "strategy_name": strategy_name,
        "reason": reason,
        "reason_code": (reason_code or "").strip(),
        "meta": meta if isinstance(meta, dict) else {},
    }
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if BLOCKED_FILE.exists():
        try:
            data = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = []
    else:
        data = []
    data.append(entry)
    data = data[-MAX_ENTRIES:]
    BLOCKED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_rejected_signals(limit: int = 30) -> list[dict]:
    if not BLOCKED_FILE.exists():
        return []
    try:
        data = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
        return data[-limit:] if isinstance(data, list) else []
    except Exception:
        return []
