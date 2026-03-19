"""DB helpers cho Smart Scale-In (thời điểm add gần nhất)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Trade


def last_scale_in_at(db: Session, position_id: int) -> datetime | None:
    """Thời gian trade action='scale_in' gần nhất cho position (UTC)."""
    if not position_id:
        return None
    return db.scalar(
        select(Trade.created_at)
        .where(Trade.position_id == position_id, Trade.action == "scale_in")
        .order_by(Trade.created_at.desc())
        .limit(1)
    )
