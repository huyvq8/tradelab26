"""Dedupe Telegram alerts for the same signal firing every cycle (symbol + strategy + rounded levels)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from time import monotonic
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CFG_PATH = _ROOT / "config" / "telegram_signal_dedupe.v1.json"

_lock = threading.Lock()
_last: dict[str, tuple[float, str]] = {}


def _cfg() -> dict[str, Any]:
    if not _CFG_PATH.exists():
        return {"enabled": True, "cooldown_seconds": 180}
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True, "cooldown_seconds": 180}


def should_send_signal_telegram(sig: dict[str, Any]) -> bool:
    c = _cfg()
    if not c.get("enabled", True):
        return True
    sym = str(sig.get("symbol") or "").strip().upper()
    strat = str(sig.get("strategy_name") or sig.get("strategy") or "").strip()
    if not sym or not strat:
        return True
    ep = sig.get("entry_price")
    sl = sig.get("stop_loss")
    blob = json.dumps(
        {
            "e": round(float(ep), 6) if isinstance(ep, (int, float)) else ep,
            "s": round(float(sl), 6) if isinstance(sl, (int, float)) else sl,
        },
        sort_keys=True,
    )
    key = f"{sym}|{strat}"
    cd = float(c.get("cooldown_seconds", 180))
    now = monotonic()
    with _lock:
        prev = _last.get(key)
        if prev is None:
            _last[key] = (now, blob)
            return True
        last_t, last_b = prev
        if blob != last_b:
            _last[key] = (now, blob)
            return True
        if (now - last_t) >= cd:
            _last[key] = (now, blob)
            return True
    return False


def reset_telegram_signal_dedupe_memory() -> None:
    with _lock:
        _last.clear()
