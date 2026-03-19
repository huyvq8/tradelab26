"""Brain V4 P1: persist events + fetch for API/replay."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.brain.models import (
    BrainCycle,
    BrainSizingEvent,
    ChangePointEvent,
    PolicyModeEvent,
    ReflexActionEvent,
    StateInferenceEvent,
)
from core.brain.types import BrainV4CycleContext, ChangePointResult, PolicyDecision

_ROOT = Path(__file__).resolve().parent.parent.parent


def sha256_brain_v4_config() -> str:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        raw = p.read_bytes()
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return ""


def p1_persistence_enabled() -> bool:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        return bool(cfg.get("p1", {}).get("persistence", {}).get("enabled", True))
    except Exception:
        return True


def start_brain_cycle(
    db: Session,
    cycle_id: str,
    portfolio_id: int | None,
    config_hash: str,
    market_decision_trace_id: str | None = None,
) -> None:
    if not p1_persistence_enabled():
        return
    existing = db.get(BrainCycle, cycle_id)
    if existing:
        return
    db.add(
        BrainCycle(
            id=cycle_id,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            portfolio_id=portfolio_id,
            config_hash_v4=config_hash,
            trace_version="p1",
            market_decision_trace_id=market_decision_trace_id,
        )
    )


def insert_policy_mode_event(
    db: Session,
    *,
    cycle_id: str,
    decision_trace_id: str | None,
    previous_mode: str,
    policy: PolicyDecision,
    emergency_override: bool,
    cooldown_blocked: bool,
    config_hash: str,
) -> None:
    if not p1_persistence_enabled():
        return
    db.add(
        PolicyModeEvent(
            cycle_id=cycle_id,
            decision_trace_id=decision_trace_id,
            scope="market",
            symbol=None,
            ts_utc=datetime.now(timezone.utc).replace(tzinfo=None),
            previous_mode=previous_mode or "",
            new_mode=policy.active_policy_mode,
            policy_confidence=policy.policy_confidence,
            switch_reason_codes_json=json.dumps(policy.policy_reason_codes, ensure_ascii=False),
            cooldown_blocked=cooldown_blocked,
            ttl_sec=policy.policy_ttl_sec,
            re_evaluate_after_sec=policy.re_evaluate_after_sec,
            emergency_override=emergency_override,
            config_hash_v4=config_hash,
        )
    )


def insert_state_inference_event(
    db: Session,
    *,
    cycle_id: str,
    decision_trace_id: str | None,
    market_decision_trace_id: str | None,
    symbol: str,
    market_state: str,
    token_state: str,
    position_state: str | None,
    conf_m: float,
    conf_t: float,
    conf_p: float | None,
    feature_snapshot: dict[str, Any],
    reason_codes: list[str],
    config_hash: str,
) -> None:
    if not p1_persistence_enabled():
        return
    db.add(
        StateInferenceEvent(
            cycle_id=cycle_id,
            decision_trace_id=decision_trace_id,
            market_decision_trace_id=market_decision_trace_id,
            symbol=symbol,
            ts_utc=datetime.now(timezone.utc).replace(tzinfo=None),
            inferred_market_state=market_state,
            inferred_token_state=token_state,
            inferred_position_state=position_state,
            conf_market=conf_m,
            conf_token=conf_t,
            conf_position=conf_p,
            feature_snapshot_json=json.dumps(feature_snapshot, ensure_ascii=False),
            reason_codes_json=json.dumps(reason_codes, ensure_ascii=False),
            config_hash_v4=config_hash,
        )
    )


def insert_change_point_event(
    db: Session,
    *,
    cycle_id: str,
    decision_trace_id: str | None,
    market_decision_trace_id: str | None,
    symbol: str,
    cp: ChangePointResult,
    config_hash: str,
) -> int | None:
    if not p1_persistence_enabled():
        return None
    row = ChangePointEvent(
        cycle_id=cycle_id,
        decision_trace_id=decision_trace_id,
        market_decision_trace_id=market_decision_trace_id,
        symbol=symbol,
        ts_utc=datetime.now(timezone.utc).replace(tzinfo=None),
        structure_score=cp.detector_scores.get("structure", 0.0),
        participation_score=cp.detector_scores.get("participation", 0.0),
        btc_leader_score=cp.detector_scores.get("btc_leader", 0.0),
        crowding_score=cp.detector_scores.get("crowding", 0.0),
        shock_score=cp.detector_scores.get("shock", 0.0),
        change_point_score=cp.change_point_score,
        context_break_flag=cp.context_break_flag,
        shift_type=cp.shift_type,
        urgency_level=cp.urgency_level,
        recommended_action=cp.recommended_protective_action,
        reason_codes_json=json.dumps(cp.reason_codes, ensure_ascii=False),
        config_hash_v4=config_hash,
    )
    db.add(row)
    db.flush()
    return row.id


def insert_reflex_action_event(
    db: Session,
    *,
    cycle_id: str,
    decision_trace_id: str | None,
    market_decision_trace_id: str | None = None,
    symbol: str,
    position_id: int | None,
    urgency: str,
    reflex_action: str,
    preconditions: dict[str, Any],
    action_reason: str,
    result: str,
    linked_trade_ids: list[int],
    change_point_event_id: int | None = None,
) -> None:
    if not p1_persistence_enabled():
        return
    db.add(
        ReflexActionEvent(
            cycle_id=cycle_id,
            decision_trace_id=decision_trace_id,
            market_decision_trace_id=market_decision_trace_id,
            symbol=symbol,
            position_id=position_id,
            ts_utc=datetime.now(timezone.utc).replace(tzinfo=None),
            urgency_level=urgency,
            reflex_action=reflex_action,
            preconditions_json=json.dumps(preconditions, ensure_ascii=False),
            action_reason=action_reason,
            result=result,
            linked_trade_ids_json=json.dumps(linked_trade_ids, ensure_ascii=False),
            change_point_event_id=change_point_event_id,
        )
    )


def insert_brain_sizing_event(
    db: Session,
    *,
    cycle_id: str,
    decision_trace_id: str | None,
    market_decision_trace_id: str | None,
    symbol: str,
    strategy_name: str,
    side: str,
    post_risk_engine_usd: float,
    pre_modifier_usd: float,
    post_modifier_usd: float,
    final_executable_usd: float,
    available_cash_usd: float | None,
    modifier_breakdown: dict[str, Any],
    config_hash: str,
) -> None:
    if not p1_persistence_enabled():
        return
    db.add(
        BrainSizingEvent(
            cycle_id=cycle_id,
            decision_trace_id=decision_trace_id,
            market_decision_trace_id=market_decision_trace_id,
            symbol=symbol,
            strategy_name=strategy_name,
            side=side,
            ts_utc=datetime.now(timezone.utc).replace(tzinfo=None),
            post_risk_engine_usd=float(post_risk_engine_usd),
            pre_modifier_usd=float(pre_modifier_usd),
            post_modifier_usd=float(post_modifier_usd),
            final_executable_usd=float(final_executable_usd),
            available_cash_usd=available_cash_usd,
            modifier_breakdown_json=json.dumps(modifier_breakdown, ensure_ascii=False),
            config_hash_v4=config_hash,
        )
    )


def fetch_latest_cycle_summary(db: Session) -> dict[str, Any] | None:
    c = db.scalar(select(BrainCycle).order_by(BrainCycle.started_at.desc()).limit(1))
    if not c:
        return None
    return fetch_cycle_bundle(db, c.id)


def fetch_cycle_bundle(db: Session, cycle_id: str) -> dict[str, Any]:
    c = db.get(BrainCycle, cycle_id)
    if not c:
        return {"error": "cycle_not_found", "cycle_id": cycle_id}
    states = list(db.scalars(select(StateInferenceEvent).where(StateInferenceEvent.cycle_id == cycle_id)))
    cps = list(db.scalars(select(ChangePointEvent).where(ChangePointEvent.cycle_id == cycle_id)))
    pols = list(db.scalars(select(PolicyModeEvent).where(PolicyModeEvent.cycle_id == cycle_id)))
    reflex = list(db.scalars(select(ReflexActionEvent).where(ReflexActionEvent.cycle_id == cycle_id)))
    sizing = list(db.scalars(select(BrainSizingEvent).where(BrainSizingEvent.cycle_id == cycle_id)))
    return {
        "cycle": {
            "id": c.id,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "portfolio_id": c.portfolio_id,
            "config_hash_v4": c.config_hash_v4,
            "market_decision_trace_id": c.market_decision_trace_id,
        },
        "state_inference_events": [_state_row(s) for s in states],
        "change_point_events": [_cp_row(x) for x in cps],
        "policy_mode_events": [_pol_row(p) for p in pols],
        "reflex_action_events": [_rx_row(r) for r in reflex],
        "sizing_events": [_sz_row(z) for z in sizing],
    }


def _state_row(s: StateInferenceEvent) -> dict[str, Any]:
    return {
        "decision_trace_id": s.decision_trace_id,
        "market_decision_trace_id": s.market_decision_trace_id,
        "symbol": s.symbol,
        "market_state": s.inferred_market_state,
        "token_state": s.inferred_token_state,
        "position_state": s.inferred_position_state,
        "conf_market": s.conf_market,
        "conf_token": s.conf_token,
        "feature_snapshot": json.loads(s.feature_snapshot_json or "{}"),
    }


def _cp_row(x: ChangePointEvent) -> dict[str, Any]:
    return {
        "decision_trace_id": x.decision_trace_id,
        "market_decision_trace_id": x.market_decision_trace_id,
        "symbol": x.symbol,
        "change_point_score": x.change_point_score,
        "context_break": x.context_break_flag,
        "urgency": x.urgency_level,
        "shift_type": x.shift_type,
        "detectors": {
            "structure": x.structure_score,
            "participation": x.participation_score,
            "btc_leader": x.btc_leader_score,
            "crowding": x.crowding_score,
            "shock": x.shock_score,
        },
    }


def _pol_row(p: PolicyModeEvent) -> dict[str, Any]:
    return {
        "decision_trace_id": p.decision_trace_id,
        "previous_mode": p.previous_mode,
        "new_mode": p.new_mode,
        "confidence": p.policy_confidence,
        "emergency": p.emergency_override,
    }


def _rx_row(r: ReflexActionEvent) -> dict[str, Any]:
    return {
        "decision_trace_id": r.decision_trace_id,
        "market_decision_trace_id": r.market_decision_trace_id,
        "symbol": r.symbol,
        "position_id": r.position_id,
        "urgency": r.urgency_level,
        "action": r.reflex_action,
        "result": r.result,
        "reason": r.action_reason,
    }


def fetch_symbol_recent(db: Session, symbol: str, limit: int = 20) -> dict[str, Any]:
    sym = symbol.strip().upper()
    st = list(
        db.scalars(
            select(StateInferenceEvent)
            .where(StateInferenceEvent.symbol == sym)
            .order_by(StateInferenceEvent.ts_utc.desc())
            .limit(limit)
        )
    )
    cp = list(
        db.scalars(
            select(ChangePointEvent)
            .where(ChangePointEvent.symbol == sym)
            .order_by(ChangePointEvent.ts_utc.desc())
            .limit(limit)
        )
    )
    return {"symbol": sym, "inference": [_state_row(s) for s in st], "change_points": [_cp_row(x) for x in cp]}


def fetch_by_decision_trace_id(db: Session, decision_trace_id: str) -> dict[str, Any]:
    """All persisted rows tagged with this decision_trace_id (cross-table)."""
    tid = (decision_trace_id or "").strip()
    if not tid:
        return {"error": "empty_trace_id"}
    st = list(
        db.scalars(
            select(StateInferenceEvent).where(StateInferenceEvent.decision_trace_id == tid)
        )
    )
    cps = list(
        db.scalars(select(ChangePointEvent).where(ChangePointEvent.decision_trace_id == tid))
    )
    pols = list(
        db.scalars(select(PolicyModeEvent).where(PolicyModeEvent.decision_trace_id == tid))
    )
    rx = list(
        db.scalars(select(ReflexActionEvent).where(ReflexActionEvent.decision_trace_id == tid))
    )
    sz = list(
        db.scalars(select(BrainSizingEvent).where(BrainSizingEvent.decision_trace_id == tid))
    )
    return {
        "decision_trace_id": tid,
        "state_inference_events": [_state_row(s) for s in st],
        "change_point_events": [_cp_row(x) for x in cps],
        "policy_mode_events": [_pol_row(p) for p in pols],
        "reflex_action_events": [_rx_row(r) for r in rx],
        "sizing_events": [_sz_row(z) for z in sz],
    }


def fetch_position_reflex(db: Session, position_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(ReflexActionEvent)
            .where(ReflexActionEvent.position_id == position_id)
            .order_by(ReflexActionEvent.ts_utc.desc())
            .limit(limit)
        )
    )
    return [_rx_row(r) for r in rows]


def _sz_row(z: BrainSizingEvent) -> dict[str, Any]:
    return {
        "decision_trace_id": z.decision_trace_id,
        "market_decision_trace_id": z.market_decision_trace_id,
        "symbol": z.symbol,
        "strategy_name": z.strategy_name,
        "side": z.side,
        "post_risk_engine_usd": z.post_risk_engine_usd,
        "pre_modifier_usd": z.pre_modifier_usd,
        "post_modifier_usd": z.post_modifier_usd,
        "final_executable_usd": z.final_executable_usd,
        "available_cash_usd": z.available_cash_usd,
        "modifier_breakdown": json.loads(z.modifier_breakdown_json or "{}"),
    }
