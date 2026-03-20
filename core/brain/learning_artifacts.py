"""Learning artifact rows from closed trade context."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.brain.p2_models import LearningArtifact
from core.journal.models import JournalEntry
from core.portfolio.models import Position, Trade
from core.risk.trade_r_metrics import trade_close_has_valid_risk

_ROOT = Path(__file__).resolve().parents[2]


def load_brain_learning_config() -> dict[str, Any]:
    p = _ROOT / "config" / "brain_learning.v1.json"
    if not p.exists():
        return {"persist_learning_artifact": False}
    return json.loads(p.read_text(encoding="utf-8"))


def maybe_persist_learning_artifact(
    db: Session,
    position: Position,
    close_trade: Trade,
    journal: JournalEntry,
) -> None:
    cfg = load_brain_learning_config()
    if not cfg.get("persist_learning_artifact", True):
        return
    if not trade_close_has_valid_risk(close_trade):
        return
    payload: dict[str, Any] = {
        "journal_id": journal.id,
        "trade_close_id": close_trade.id,
        "pnl_usd": close_trade.pnl_usd,
        "risk_usd": close_trade.risk_usd,
        "note": close_trade.note,
        "lessons": journal.lessons,
        "mistakes": journal.mistakes,
        "thesis_type": position.thesis_type,
        "thesis_state": position.thesis_state,
        "zone_shift_risk_score": position.zone_shift_risk_score,
    }
    rr = getattr(journal, "result_r", None)
    conf = None
    if rr is not None:
        try:
            conf = max(0.0, min(1.0, 0.5 + 0.12 * float(rr)))
        except (TypeError, ValueError):
            conf = 0.45
    else:
        conf = 0.4
    evidence = {
        "exit_reason": getattr(journal, "exit_reason", None),
        "result_r": rr,
        "capital_bucket": getattr(position, "capital_bucket", None),
    }
    art = LearningArtifact(
        brain_cycle_id=getattr(close_trade, "brain_cycle_id", None),
        trade_id=close_trade.id,
        position_id=position.id,
        symbol=position.symbol,
        artifact_type="trade_close_summary",
        payload_json=json.dumps(payload, ensure_ascii=False),
        confidence=conf,
        sample_size=max(1, int(cfg.get("default_artifact_sample_size") or 1)),
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        promotion_status="none",
    )
    db.add(art)
    db.flush()
    try:
        from core.brain.proposal_service import try_auto_propose_from_artifact

        try_auto_propose_from_artifact(db, art.id)
    except Exception:
        pass
