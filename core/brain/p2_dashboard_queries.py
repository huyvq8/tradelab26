"""Read-only aggregates for Brain P2 dashboard (§8.2 style)."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.brain.p2_models import BrainProposal, LearningArtifact, PortfolioStateEvent, RuntimeConfigOverride
from core.brain.portfolio_clusters import cluster_for_symbol
from core.portfolio.models import Portfolio, Position


def _portfolio_by_name(db: Session, name: str) -> Portfolio | None:
    return db.scalar(select(Portfolio).where(Portfolio.name == name))


def fetch_thesis_open_health(db: Session, portfolio_name: str) -> dict[str, Any]:
    p = _portfolio_by_name(db, portfolio_name)
    if not p:
        return {"error": "no_portfolio"}
    rows = list(
        db.scalars(select(Position).where(Position.is_open == True, Position.portfolio_id == p.id))
    )
    inv = sum(1 for x in rows if (x.thesis_state or "").upper() == "INVALID")
    out = []
    for pos in rows:
        out.append(
            {
                "symbol": pos.symbol,
                "side": pos.side,
                "strategy": pos.strategy_name,
                "thesis_state": pos.thesis_state,
                "thesis_type": pos.thesis_type,
                "warning_count": pos.thesis_warning_count,
                "danger_count": pos.thesis_danger_count,
                "zone_shift_score": pos.zone_shift_risk_score,
                "zone_shift_level": pos.zone_shift_risk_level,
                "last_reason": pos.thesis_last_reason,
                "invalid_risk": 1.0 if (pos.thesis_state or "").upper() == "INVALID" else 0.0,
            }
        )
    return {
        "open_count": len(rows),
        "invalid_count": inv,
        "positions": out,
    }


def fetch_strategy_thesis_fit_stats(db: Session, *, limit: int = 300) -> dict[str, Any]:
    """Aggregate recent learning artifacts by thesis_type from payload."""
    rows = list(
        db.scalars(
            select(LearningArtifact).order_by(desc(LearningArtifact.created_at)).limit(max(50, min(limit, 500)))
        )
    )
    by_t: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "losses": 0, "invalid_mentions": 0, "zone_sum": 0.0, "zone_n": 0}
    )
    for r in rows:
        try:
            pl = json.loads(r.payload_json or "{}")
        except Exception:
            pl = {}
        tt = str(pl.get("thesis_type") or "unknown")
        stt = str(pl.get("thesis_state") or "")
        pnl = float(pl.get("pnl_usd") or 0)
        z = pl.get("zone_shift_risk_score")
        b = by_t[tt]
        b["n"] += 1
        if pnl >= 0:
            b["wins"] += 1
        else:
            b["losses"] += 1
        if "INVALID" in stt.upper():
            b["invalid_mentions"] += 1
        if z is not None:
            try:
                b["zone_sum"] += float(z)
                b["zone_n"] += 1
            except (TypeError, ValueError):
                pass
    summary = {}
    for tt, b in by_t.items():
        n = b["n"] or 1
        summary[tt] = {
            "trades": b["n"],
            "win_rate": round(b["wins"] / n, 3),
            "invalidation_rate": round(b["invalid_mentions"] / n, 3),
            "avg_zone_shift_at_close": round(b["zone_sum"] / b["zone_n"], 4) if b["zone_n"] else None,
        }
    return {"by_thesis_type": summary, "sample_artifacts": len(rows)}


def fetch_learning_artifacts_board(db: Session, *, limit: int = 40) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(LearningArtifact).order_by(desc(LearningArtifact.created_at)).limit(max(5, min(limit, 200)))
        )
    )
    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "symbol": r.symbol,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "confidence": r.confidence,
                "sample_size": r.sample_size,
                "promotion_status": r.promotion_status,
                "proposal": r.promoted_proposal_public_id,
                "artifact_type": r.artifact_type,
            }
        )
    promoted = sum(1 for r in rows if (r.promotion_status or "") == "promoted")
    pending = sum(1 for r in rows if (r.promotion_status or "") in ("none", "proposed"))
    return {"recent": items, "promoted_in_window": promoted, "not_promoted_in_window": pending}


def fetch_proposal_governance_board(db: Session) -> dict[str, Any]:
    pending = list(
        db.scalars(
            select(BrainProposal)
            .where(BrainProposal.status.in_(["proposed", "shadow", "approved"]))
            .order_by(desc(BrainProposal.created_at))
            .limit(80)
        )
    )
    active_ov = list(
        db.scalars(
            select(RuntimeConfigOverride).where(RuntimeConfigOverride.status == "active").limit(80)
        )
    )
    rollback_watch = [
        json.loads(p.rollback_conditions_json or "{}")
        for p in pending[:10]
        if p.rollback_conditions_json
    ]
    return {
        "pending_proposals": [p.public_id for p in pending[:15]],
        "pending_detail": [
            {
                "public_id": p.public_id,
                "status": p.status,
                "risk_class": p.risk_class,
                "title": p.title,
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in pending[:15]
        ],
        "active_overrides": len(active_ov),
        "override_targets": list({o.target_config_name for o in active_ov}),
        "rollback_conditions_sample": rollback_watch[:3],
    }


def fetch_portfolio_brain_board(db: Session, portfolio_name: str) -> dict[str, Any]:
    p = _portfolio_by_name(db, portfolio_name)
    if not p:
        return {"error": "no_portfolio"}
    latest = db.scalar(
        select(PortfolioStateEvent)
        .where(PortfolioStateEvent.portfolio_id == p.id)
        .order_by(desc(PortfolioStateEvent.created_at))
        .limit(1)
    )
    rows = list(
        db.scalars(select(Position).where(Position.is_open == True, Position.portfolio_id == p.id))
    )
    sym_count: dict[str, int] = defaultdict(int)
    cluster_count: dict[str, int] = defaultdict(int)
    for pos in rows:
        sym_count[pos.symbol] += 1
        cluster_count[cluster_for_symbol(pos.symbol)] += 1
    max_sym = max(sym_count.values()) if sym_count else 0
    concentration = round(max_sym / max(len(rows), 1), 3)
    thesis_div = sum(
        1
        for pos in rows
        if (pos.thesis_state or "").upper() in ("WARNING", "DANGER", "INVALID")
    )
    return {
        "latest_state": latest.state_label if latest else None,
        "latest_stress": latest.portfolio_stress_score if latest else None,
        "latest_at": latest.created_at.isoformat() if latest and latest.created_at else None,
        "open_positions": len(rows),
        "concentration_max_symbol_share": concentration,
        "cluster_exposure": dict(cluster_count),
        "thesis_divergence_count": thesis_div,
    }
