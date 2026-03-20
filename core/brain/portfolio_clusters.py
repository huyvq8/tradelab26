"""Symbol → cluster / sector for portfolio brain."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def load_cluster_config() -> dict[str, Any]:
    p = _ROOT / "config" / "correlation_sectors.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "correlation_sectors.v1.example.json"
    if not p.exists():
        return {"base_to_sector": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def cluster_for_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if s.endswith("USDT"):
        s = s[:-4]
    cfg = load_cluster_config()
    m = cfg.get("base_to_sector") or {} if isinstance(cfg, dict) else {}
    return str(m.get(s, "unclassified"))
