"""Persist thesis state / action events; tick helper for cycle."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.brain.p2_models import ThesisActionEvent, ThesisStateEvent
from core.portfolio.models import Position, Trade
from core.profit.thesis_actions import thesis_state_to_action
from core.profit.thesis_monitor import compute_zone_shift_and_state, snapshot_json_for_eval
from core.profit.thesis_profiles import ensure_thesis_defaults_for_position, load_thesis_management_config


def _open_trade_id(db: Session, position_id: int) -> int | None:
    t = db.scalar(
        select(Trade.id).where(Trade.position_id == position_id, Trade.action == "open").limit(1)
    )
    return int(t) if t is not None else None


def persist_thesis_state_event(
    db: Session,
    *,
    position: Position,
    previous_state: str,
    new_state: str,
    eval_result: dict[str, Any],
    price_now: float,
    brain_cycle_id: str | None,
    decision_trace_id: str | None,
) -> None:
    tid = _open_trade_id(db, position.id)
    ev = ThesisStateEvent(
        brain_cycle_id=brain_cycle_id,
        decision_trace_id=decision_trace_id,
        trade_id=tid,
        position_id=position.id,
        symbol=position.symbol,
        previous_state=previous_state,
        new_state=new_state,
        thesis_score=eval_result.get("thesis_score"),
        zone_shift_risk_score=eval_result.get("zone_shift_risk_score"),
        zone_shift_risk_level=eval_result.get("zone_shift_risk_level"),
        reason_codes_json=json.dumps(eval_result.get("reason_codes") or [], ensure_ascii=False),
        snapshot_json=snapshot_json_for_eval(position, price_now, eval_result),
        created_at=datetime.utcnow(),
    )
    db.add(ev)


def persist_thesis_action_event(
    db: Session,
    *,
    action: dict[str, Any],
    brain_cycle_id: str | None,
    decision_trace_id: str | None,
    trade_id: int | None,
) -> None:
    db.add(
        ThesisActionEvent(
            brain_cycle_id=brain_cycle_id,
            decision_trace_id=decision_trace_id,
            trade_id=trade_id,
            action_type=str(action.get("action_type", "hold")),
            action_strength=float(action.get("action_strength", 0.0)),
            reason_codes_json=json.dumps(action.get("reason_codes") or [], ensure_ascii=False),
            source="thesis_monitor",
            created_at=datetime.utcnow(),
        )
    )


def thesis_tick_update_position(
    db: Session,
    position: Position,
    price_now: float,
    klines: list[Any],
    *,
    brain_cycle_id: str | None,
    decision_trace_id: str | None,
) -> dict[str, Any]:
    """
    Updates position thesis fields; persists state/action events on change.
    Returns { force_close: bool, close_note: str | None, action_logged: dict | None }
    """
    cfg = load_thesis_management_config(db)
    if not cfg.get("enabled", True):
        return {"force_close": False, "close_note": None, "action_logged": None}

    ensure_thesis_defaults_for_position(position)
    prev_state = position.thesis_state or "NORMAL"
    ev = compute_zone_shift_and_state(position, price_now, klines, cfg)
    new_state = ev["thesis_state"]

    position.zone_shift_risk_score = ev["zone_shift_risk_score"]
    position.zone_shift_risk_level = ev["zone_shift_risk_level"]
    position.thesis_last_score = ev["thesis_score"]
    position.thesis_last_reason = ",".join(ev.get("reason_codes") or [])[:500]

    if new_state != prev_state:
        if new_state == "WARNING":
            position.thesis_warning_count = int(position.thesis_warning_count or 0) + 1
        elif new_state == "DANGER":
            position.thesis_danger_count = int(position.thesis_danger_count or 0) + 1
        position.thesis_state = new_state
        persist_thesis_state_event(
            db,
            position=position,
            previous_state=prev_state,
            new_state=new_state,
            eval_result=ev,
            price_now=price_now,
            brain_cycle_id=brain_cycle_id,
            decision_trace_id=decision_trace_id,
        )
    else:
        position.thesis_state = new_state

    act = thesis_state_to_action(
        new_state,
        ev["zone_shift_risk_level"],
        force_close_on_invalid=bool(cfg.get("force_close_on_invalid")),
    )
    action_logged = None
    if new_state != prev_state:
        action_logged = act
        persist_thesis_action_event(
            db,
            action=act,
            brain_cycle_id=brain_cycle_id,
            decision_trace_id=decision_trace_id,
            trade_id=_open_trade_id(db, position.id),
        )

    fc = bool(cfg.get("force_close_on_invalid")) and new_state == "INVALID"
    note = f"Thesis INVALID (zone={ev['zone_shift_risk_score']})" if fc else None
    return {"force_close": fc, "close_note": note, "action_logged": action_logged, "eval": ev}
