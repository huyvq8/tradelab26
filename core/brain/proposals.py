"""Proposal listing and creation (DB-backed P2)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.brain.proposal_service import (
    activate_proposal_runtime_override,
    approve_proposal,
    create_proposal_from_learning_artifact,
    get_proposal_by_public_id,
    list_proposals,
    proposal_to_dict,
    rollback_proposal,
)


def list_pending_proposals(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    rows = list_proposals(
        db, statuses=["proposed", "shadow", "approved"], limit=limit
    )
    return [proposal_to_dict(p) for p in rows]


def get_proposal(db: Session, public_id: str) -> dict[str, Any] | None:
    p = get_proposal_by_public_id(db, public_id)
    return proposal_to_dict(p) if p else None


def create_from_artifact(
    db: Session, artifact_id: int, *, force: bool = False, risk_class: str = "B"
):
    return create_proposal_from_learning_artifact(
        db, artifact_id, risk_class=risk_class, force=force
    )


def approve(db: Session, public_id: str, reviewer_label: str, notes: str = ""):
    return approve_proposal(db, public_id, reviewer_label=reviewer_label, notes=notes)


def activate(db: Session, public_id: str, *, rollout_mode: str = "full", reviewer_label: str = ""):
    return activate_proposal_runtime_override(
        db, public_id, rollout_mode=rollout_mode, reviewer_label=reviewer_label
    )


def rollback(db: Session, public_id: str, reason: str) -> bool:
    return rollback_proposal(db, public_id, reason)
