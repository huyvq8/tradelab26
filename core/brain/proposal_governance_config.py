"""Load proposal_governance.v1.json (human-owned; bot never writes this file)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def load_proposal_governance_config() -> dict[str, Any]:
    p = _ROOT / "config" / "proposal_governance.v1.json"
    if not p.exists():
        return {"version": "0", "auto_apply": False, "classes": {}}
    return json.loads(p.read_text(encoding="utf-8"))
