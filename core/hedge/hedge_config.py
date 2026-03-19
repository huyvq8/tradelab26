# Load hedge config from config/hedge_strategy.v1.json
from __future__ import annotations

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HEDGE_STRATEGY_PATH = _PROJECT_ROOT / "config" / "hedge_strategy.v1.json"


def load_hedge_config() -> dict:
    if _HEDGE_STRATEGY_PATH.exists():
        try:
            return json.loads(_HEDGE_STRATEGY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False}
