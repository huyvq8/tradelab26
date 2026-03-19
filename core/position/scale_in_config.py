# Load scale-in config from config/scale_in.v1.json (spec document/budget).
from __future__ import annotations

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCALE_IN_PATH = _PROJECT_ROOT / "config" / "scale_in.v1.json"


def load_scale_in_config() -> dict:
    if _SCALE_IN_PATH.exists():
        try:
            return json.loads(_SCALE_IN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "scale_in": {"enabled": False},
        "scale_in_risk": {},
        "scale_in_price_rules": {},
        "scale_in_position_rules": {},
        "scale_in_strategy_rules": {},
        "scale_in_sizing": {},
    }
