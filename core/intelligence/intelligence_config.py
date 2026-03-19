# Load token_classification.v1.json and strategy_routing.v1.json
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_CLASS_PATH = _ROOT / "config" / "token_classification.v1.json"
_ROUTING_PATH = _ROOT / "config" / "strategy_routing.v1.json"


def load_classification_config() -> dict:
    if _CLASS_PATH.exists():
        try:
            return json.loads(_CLASS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False}
