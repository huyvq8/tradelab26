"""Aggregates for thesis health (dashboard / replay hooks)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Position


def count_thesis_states_open(db: Session, portfolio_id: int) -> dict[str, int]:
    rows = list(
        db.scalars(
            select(Position.thesis_state).where(
                Position.is_open == True,
                Position.portfolio_id == portfolio_id,
            )
        )
    )
    c: Counter[str] = Counter((r or "NORMAL") for r in rows)
    return dict(c)


def open_positions_thesis_snapshot(db: Session, portfolio_id: int, limit: int = 200) -> list[dict[str, Any]]:
    pos_list = list(
        db.scalars(
            select(Position)
            .where(Position.is_open == True, Position.portfolio_id == portfolio_id)
            .limit(limit)
        )
    )
    out: list[dict[str, Any]] = []
    for p in pos_list:
        out.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "strategy_name": p.strategy_name,
                "thesis_type": p.thesis_type,
                "thesis_state": p.thesis_state,
                "zone_shift_risk_score": p.zone_shift_risk_score,
                "zone_shift_risk_level": p.zone_shift_risk_level,
                "thesis_last_reason": p.thesis_last_reason,
            }
        )
    return out
