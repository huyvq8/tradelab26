"""Delayed decision evaluation on trade close (P2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.brain.p2_models import DecisionEvaluation
from core.journal.models import JournalEntry
from core.portfolio.models import Position, Trade

_ROOT = Path(__file__).resolve().parents[2]


def load_brain_learning_config() -> dict[str, Any]:
    p = _ROOT / "config" / "brain_learning.v1.json"
    if not p.exists():
        return {"delayed_evaluation_on_close": False}
    return json.loads(p.read_text(encoding="utf-8"))


def maybe_record_delayed_p2_evaluation(
    db: Session,
    position: Position,
    close_trade: Trade,
    open_trade: Trade,
    journal: JournalEntry,
) -> None:
    cfg = load_brain_learning_config()
    if not cfg.get("delayed_evaluation_on_close", True):
        return

    defaults = (cfg.get("decision_evaluation_defaults") or {}) if isinstance(cfg, dict) else {}
    payload = {
        "exit_reason": getattr(journal, "exit_reason", None),
        "result_r": getattr(journal, "result_r", None),
        "thesis_state_at_close": getattr(position, "thesis_state", None),
        "zone_shift_risk_score": getattr(position, "zone_shift_risk_score", None),
        "thesis_type": getattr(position, "thesis_type", None),
    }
    ev = DecisionEvaluation(
        brain_cycle_id=getattr(close_trade, "brain_cycle_id", None),
        decision_trace_id=getattr(close_trade, "decision_trace_id", None),
        market_decision_trace_id=None,
        trade_id=close_trade.id,
        position_id=position.id,
        symbol=position.symbol,
        evaluation_phase="delayed",
        decision_quality_score=defaults.get("decision_quality_score"),
        follow_plan_score=defaults.get("follow_plan_score"),
        edge_realization_score=defaults.get("edge_realization_score"),
        thesis_management_quality_score=defaults.get("thesis_management_quality_score"),
        zone_shift_detection_score=defaults.get("zone_shift_detection_score"),
        damage_prevented_score=None,
        missed_opportunity_cost=None,
        false_alarm_cost=None,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(ev)
