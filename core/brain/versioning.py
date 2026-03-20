"""Recorded config / override versions (audit)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.brain.p2_models import AppliedConfigVersion


def list_config_versions(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AppliedConfigVersion)
            .order_by(desc(AppliedConfigVersion.recorded_at))
            .limit(max(1, min(limit, 200)))
        )
    )
    return [
        {
            "id": r.id,
            "config_name": r.config_name,
            "version_label": r.version_label,
            "content_hash": r.content_hash,
            "proposal_public_id": r.proposal_public_id,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in rows
    ]
