"""Candidate-level quality gates and diagnostics before full execution."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.risk.trade_r_metrics import planned_r_multiple
from core.strategies.base import StrategySignal

_ROOT = Path(__file__).resolve().parents[2]
_CFG_PATH = _ROOT / "config" / "candidate_quality.v1.json"


def load_candidate_quality_config() -> dict[str, Any]:
    if not _CFG_PATH.exists():
        return {"enabled": True, "min_candidate_r_multiple": 0.8, "hide_low_r_in_dashboard": False}
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": True, "min_candidate_r_multiple": 0.8, "hide_low_r_in_dashboard": False}


def candidate_planned_r(signal: StrategySignal) -> float | None:
    return planned_r_multiple(signal)


def is_low_r_candidate(signal: StrategySignal, cfg: dict[str, Any] | None = None) -> tuple[bool, float | None, float]:
    cfg = cfg or load_candidate_quality_config()
    min_r = float(cfg.get("min_candidate_r_multiple", 0.8) or 0.8)
    pr = candidate_planned_r(signal)
    if pr is None:
        return False, None, min_r
    return pr < min_r, pr, min_r
