"""Wire Brain V4 into cycle / review without bloating orchestration."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.market_data.client import get_klines_1h, get_quotes_with_fallback
from core.observability.decision_log import log_decision
from core.portfolio.models import Portfolio, Position
from core.regime.detector import derive_regime

from core.brain.change_point import compute_change_point_for_symbol
from core.brain.reflex import resolve_reflex
from core.brain.runtime_state import load_runtime_state, reflex_cooldown_active, set_reflex_cooldown
from core.brain.state_inference import infer_position_state, infer_token_state

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent.parent


def _brain_v4_enabled() -> bool:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("enabled", True))
    except Exception:
        return False


def _reflex_cooldown_sec() -> float:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return float(json.loads(p.read_text(encoding="utf-8")).get("reflex", {}).get("cooldown_sec", 90))
    except Exception:
        return 90.0


def try_brain_v4_reflex_for_position(
    db: Session,
    portfolio: Portfolio,
    pos: Position,
    price_now: float,
    quotes: dict[str, Any],
    executor: Any,
    paper: Any,
    brain_cycle_id: str | None = None,
    market_decision_trace_id: str | None = None,
) -> tuple[bool, dict | None]:
    """
    Run V4 reflex before other management logic.
    Returns (handled, action_dict) where handled=True means caller should `continue` loop
    (position closed or partial done).
    """
    if not _brain_v4_enabled():
        return False, None

    key = f"reflex:{pos.id}"
    rt = load_runtime_state()
    if reflex_cooldown_active(rt, key):
        return False, None

    market_state = str(rt.market_state or "BALANCED")
    policy_mode = str(rt.policy_mode or "NORMAL")

    try:
        klines = get_klines_1h(pos.symbol, limit=24)
    except Exception:
        klines = []
    if len(klines) < 8:
        return False, None

    q = quotes.get(pos.symbol)
    chg = float(q.percent_change_24h or 0) if q else 0.0
    token_state, _ = infer_token_state(chg, klines, market_state)  # type: ignore[arg-type]

    qbtc = quotes.get("BTC") or (get_quotes_with_fallback(["BTC"]).get("BTC"))
    btc_reg = "balanced"
    if qbtc:
        btc_reg = derive_regime(float(qbtc.percent_change_24h or 0), float(qbtc.volume_24h or 0))

    cp = compute_change_point_for_symbol(
        klines,
        pos.side or "long",
        prev_btc_regime=rt.last_btc_regime or btc_reg,
        curr_btc_regime=btc_reg,
        funding_rate=None,
    )

    direction = 1 if pos.side == "long" else -1
    risk_usd = None
    if pos.stop_loss is not None and pos.quantity:
        risk_usd = abs(float(pos.entry_price) - float(pos.stop_loss)) * float(pos.quantity)
    pnl_usd = (price_now - float(pos.entry_price)) * direction * float(pos.quantity or 0)
    unrealized_r = (pnl_usd / risk_usd) if risk_usd and risk_usd > 0 else 0.0

    pos_state, _ = infer_position_state(
        side=pos.side or "long",
        entry_price=float(pos.entry_price),
        stop_loss=float(pos.stop_loss) if pos.stop_loss is not None else None,
        price_now=price_now,
        unrealized_r=unrealized_r,
        token_state=token_state,
        market_state=market_state,  # type: ignore[arg-type]
        change_point_score=cp.change_point_score,
    )

    btc_risk_off_long = btc_reg == "risk_off"
    reflex = resolve_reflex(
        cp,
        pos_state,
        policy_mode,  # type: ignore[arg-type]
        side=pos.side or "long",
        btc_risk_off_long=btc_risk_off_long,
    )
    if reflex is None:
        return False, None

    log_decision(
        "brain_v4_reflex",
        {
            "trace": "review",
            "urgency": reflex.urgency,
            "primary_action": reflex.primary_action,
            "cp": cp.change_point_score,
            "token_state": token_state,
            "position_state": pos_state,
            "thesis_broken_pre_sl": reflex.thesis_broken_before_sl,
            "detectors": cp.detector_scores,
        },
        symbol=pos.symbol,
        reason_code=reflex.primary_action,
    )

    set_reflex_cooldown(rt, key, _reflex_cooldown_sec())
    from core.brain.runtime_state import save_runtime_state

    save_runtime_state(rt)

    reflex_decision_trace_id = str(uuid.uuid4())

    def _persist_reflex(result: str, linked: list[int]) -> None:
        if not brain_cycle_id:
            return
        try:
            from core.brain.persistence import insert_reflex_action_event, p1_persistence_enabled

            if not p1_persistence_enabled():
                return
            insert_reflex_action_event(
                db,
                cycle_id=brain_cycle_id,
                decision_trace_id=reflex_decision_trace_id,
                market_decision_trace_id=market_decision_trace_id,
                symbol=pos.symbol,
                position_id=pos.id,
                urgency=str(reflex.urgency),
                reflex_action=str(reflex.primary_action),
                preconditions={
                    "cp": cp.change_point_score,
                    "token_state": token_state,
                    "position_state": pos_state,
                    "policy_mode": policy_mode,
                },
                action_reason=cp.shift_type,
                result=result,
                linked_trade_ids=linked,
            )
        except Exception:
            pass

    if reflex.primary_action == "FORCE_EXIT":
        try:
            ct = executor.close_position(
                db,
                pos,
                price_now,
                note=f"BrainV4 reflex HIGH: {cp.shift_type} cp={cp.change_point_score:.2f}",
            )
            _persist_reflex("CLOSE", [ct.id] if ct is not None else [])
            return True, {"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": "brain_v4_reflex_exit"}
        except Exception:
            try:
                ct = paper.close_position(
                    db,
                    pos,
                    price_now,
                    note=f"BrainV4 reflex HIGH: {cp.shift_type}",
                )
                _persist_reflex("CLOSE", [ct.id] if ct is not None else [])
                return True, {"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": "brain_v4_reflex_exit"}
            except Exception as e:
                logger.exception("brain_v4 reflex exit failed: %s", e)
                return False, None

    if reflex.primary_action == "PARTIAL_REDUCE" and reflex.reduce_fraction > 0:
        reduce_qty = round(float(pos.quantity) * reflex.reduce_fraction, 8)
        if 0 < reduce_qty < float(pos.quantity or 0):
            try:
                if hasattr(executor, "reduce_position"):
                    pt = executor.reduce_position(db, pos, reduce_qty, price_now, note="BrainV4 reflex MEDIUM partial")
                else:
                    pt = paper.reduce_position(db, pos, reduce_qty, price_now, note="BrainV4 reflex MEDIUM partial")
                _persist_reflex("PARTIAL", [pt.id] if pt is not None else [])
                return True, {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "action": "PARTIAL_TP",
                    "reason": "brain_v4_reflex_partial",
                }
            except Exception:
                pass

    return False, None
