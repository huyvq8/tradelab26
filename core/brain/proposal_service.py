"""Proposals from learning artifacts → reviews → runtime overrides (no base config writes)."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.brain.p2_models import (
    AppliedConfigVersion,
    BrainProposal,
    LearningArtifact,
    ProposalEvidenceLink,
    ProposalReview,
    RollbackEvent,
    RuntimeConfigOverride,
)
from core.brain.proposal_governance_config import load_proposal_governance_config
from core.brain.runtime_overrides import expire_stale_runtime_overrides
from core.profit.thesis_profiles import _load_thesis_management_file


def _utcnow() -> datetime:
    return datetime.utcnow()


def approval_required_for_class(risk_class: str) -> bool:
    cfg = load_proposal_governance_config()
    c = (cfg.get("classes") or {}).get((risk_class or "B").upper())
    if c and "requires_approval" in c:
        return bool(c["requires_approval"])
    rules = (cfg.get("approval_rules") or {}).get((risk_class or "B").upper(), {})
    return bool(rules.get("requires_human_approval", True))


def min_evidence_samples_for_class(risk_class: str) -> int:
    cfg = load_proposal_governance_config()
    rules = (cfg.get("approval_rules") or {}).get((risk_class or "B").upper(), {})
    return int(rules.get("min_evidence_samples", 1))


def _default_ttl_seconds(risk_class: str) -> int:
    cfg = load_proposal_governance_config()
    m = cfg.get("default_ttl_seconds") or {}
    return int(m.get((risk_class or "B").upper(), m.get(risk_class, 7200)))


def _profile_patch_diff(
    before_p: dict[str, Any],
    after_p: dict[str, Any],
) -> dict[str, Any]:
    patch_profiles: dict[str, Any] = {}
    for name, a_prof in after_p.items():
        if not isinstance(a_prof, dict):
            continue
        b_prof = before_p.get(name) or {}
        if not isinstance(b_prof, dict):
            b_prof = {}
        delta = {k: v for k, v in a_prof.items() if b_prof.get(k) != v}
        if delta:
            patch_profiles[name] = delta
    return patch_profiles


def _build_thesis_patch_from_loss_invalid(
    before_profiles: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Conservative demo patch: slightly tighten default.warning_zone_shift after bad outcome."""
    after_profiles = copy.deepcopy(before_profiles)
    d = after_profiles.setdefault("default", {})
    w = float(d.get("warning_zone_shift", 0.45))
    d["warning_zone_shift"] = round(max(0.2, w - 0.02), 3)
    patch = {"profiles": {"default": {"warning_zone_shift": d["warning_zone_shift"]}}}
    return after_profiles, patch


def create_proposal_from_learning_artifact(
    db: Session,
    artifact_id: int,
    *,
    risk_class: str = "B",
    force: bool = False,
) -> BrainProposal | None:
    """
    Turn one learning artifact into a proposed runtime patch (evidence-linked).
    Respects min_evidence_samples unless force=True (e.g. manual API).
    """
    art = db.get(LearningArtifact, artifact_id)
    if not art:
        return None
    rc = (risk_class or "B").upper()
    min_n = min_evidence_samples_for_class(rc)
    if not force and int(art.sample_size or 1) < min_n:
        return None

    base = _load_thesis_management_file()
    before_profiles = copy.deepcopy(base.get("profiles") or {})
    payload = json.loads(art.payload_json or "{}")
    pnl = float(payload.get("pnl_usd") or 0)
    th = str(payload.get("thesis_state") or "")

    if pnl < 0 and th.upper() == "INVALID":
        after_profiles, merge_patch = _build_thesis_patch_from_loss_invalid(before_profiles)
        title = "Tighten thesis warning threshold (post INVALID loss)"
        desc = "Evidence: losing trade closed with thesis INVALID; propose slightly earlier WARNING."
    else:
        after_profiles = copy.deepcopy(before_profiles)
        merge_patch = {}
        title = "Observe-only proposal (no automatic patch)"
        desc = "Artifact did not match loss+INVALID heuristic; empty merge_patch for human edit."

    gov = load_proposal_governance_config()
    ttl = _default_ttl_seconds(rc)
    expires = _utcnow() + timedelta(seconds=ttl)
    rollback = gov.get("rollback_triggers") or {}

    public_id = str(uuid.uuid4())
    ev = {
        "artifact_id": art.id,
        "symbol": art.symbol,
        "payload_summary": {
            "pnl_usd": pnl,
            "thesis_state": th,
            "thesis_type": payload.get("thesis_type"),
        },
        "merge_patch": merge_patch,
    }
    prop = BrainProposal(
        public_id=public_id,
        status="proposed",
        risk_class=rc,
        title=title[:256],
        description=desc,
        evidence_snapshot_json=json.dumps(ev, ensure_ascii=False),
        before_values_json=json.dumps({"profiles": before_profiles}, ensure_ascii=False),
        after_values_json=json.dumps({"profiles": after_profiles}, ensure_ascii=False),
        target_config_name="thesis_management.v1",
        rollout_mode="shadow",
        rollback_conditions_json=json.dumps(rollback, ensure_ascii=False),
        ttl_seconds=ttl,
        valid_from=None,
        expires_at=expires,
        source_learning_artifact_id=art.id,
    )
    db.add(prop)
    db.flush()
    db.add(
        ProposalEvidenceLink(
            proposal_id=prop.id,
            learning_artifact_id=art.id,
            link_role="primary",
            weight=1.0,
        )
    )
    art.promotion_status = "proposed"
    art.promoted_proposal_public_id = public_id
    return prop


def try_auto_propose_from_artifact(db: Session, artifact_id: int) -> BrainProposal | None:
    gov = load_proposal_governance_config()
    if not gov.get("auto_create_proposal_from_artifact"):
        return None
    if gov.get("auto_apply"):
        # Never auto-apply; still allow auto-create as proposal only
        pass
    return create_proposal_from_learning_artifact(db, artifact_id, force=False)


def transition_proposal_status(
    db: Session,
    public_id: str,
    new_status: str,
    *,
    reviewer_label: str = "",
    notes: str = "",
) -> BrainProposal | None:
    allowed = {
        "proposed",
        "shadow",
        "approved",
        "active",
        "rolled_back",
        "rejected",
        "expired",
    }
    if new_status not in allowed:
        return None
    prop = db.scalar(select(BrainProposal).where(BrainProposal.public_id == public_id))
    if not prop:
        return None
    prop.status = new_status
    prop.updated_at = _utcnow()
    if reviewer_label or notes:
        db.add(
            ProposalReview(
                proposal_id=prop.id,
                decision=new_status,
                reviewer_label=reviewer_label or "system",
                notes=notes,
            )
        )
    return prop


def approve_proposal(db: Session, public_id: str, *, reviewer_label: str, notes: str = "") -> BrainProposal | None:
    prop = db.scalar(select(BrainProposal).where(BrainProposal.public_id == public_id))
    if not prop:
        return None
    rc = (prop.risk_class or "B").upper()
    if approval_required_for_class(rc) and not reviewer_label:
        return None
    prop.status = "approved"
    prop.updated_at = _utcnow()
    db.add(
        ProposalReview(
            proposal_id=prop.id,
            decision="approved",
            reviewer_label=reviewer_label,
            notes=notes,
        )
    )
    return prop


def activate_proposal_runtime_override(
    db: Session,
    public_id: str,
    *,
    rollout_mode: str = "full",
    reviewer_label: str = "",
) -> RuntimeConfigOverride | None:
    """
    Creates an active RuntimeConfigOverride from proposal.after_values_json vs file baseline.
    Stores merge_patch as diff-like structure: use after_values_json profiles slice as patch body.
    """
    prop = db.scalar(select(BrainProposal).where(BrainProposal.public_id == public_id))
    if not prop or prop.status in ("rejected", "rolled_back", "expired"):
        return None
    if prop.status != "approved":
        return None

    try:
        before = json.loads(prop.before_values_json or "{}")
        after = json.loads(prop.after_values_json or "{}")
        diff = _profile_patch_diff(before.get("profiles") or {}, after.get("profiles") or {})
        merge_patch = {"profiles": diff}
    except Exception:
        merge_patch = {"profiles": {}}

    if not merge_patch.get("profiles"):
        return None

    now = _utcnow()
    exp = prop.expires_at or (now + timedelta(seconds=prop.ttl_seconds or _default_ttl_seconds(prop.risk_class)))

    ov = RuntimeConfigOverride(
        proposal_id=prop.id,
        proposal_public_id=prop.public_id,
        target_config_name=prop.target_config_name,
        merge_patch_json=json.dumps(merge_patch, ensure_ascii=False),
        status="active",
        rollout_mode=rollout_mode or prop.rollout_mode or "full",
        activated_at=now,
        expires_at=exp,
    )
    db.add(ov)
    db.flush()
    prop.status = "active"
    prop.rollout_mode = rollout_mode or prop.rollout_mode
    prop.updated_at = now

    h = hashlib.sha256(prop.after_values_json.encode("utf-8", errors="ignore")).hexdigest()[:32]
    db.add(
        AppliedConfigVersion(
            config_name=prop.target_config_name,
            version_label=f"override:{prop.public_id[:8]}",
            content_hash=h,
            proposal_public_id=prop.public_id,
            notes="runtime override activated",
            payload_json=json.dumps({"runtime_config_override_id": ov.id}, ensure_ascii=False),
        )
    )

    if prop.source_learning_artifact_id:
        art = db.get(LearningArtifact, prop.source_learning_artifact_id)
        if art:
            art.promotion_status = "promoted"

    if reviewer_label:
        db.add(
            ProposalReview(
                proposal_id=prop.id,
                decision="activate",
                reviewer_label=reviewer_label,
                notes=f"rollout={ov.rollout_mode}",
            )
        )
    return ov


def rollback_proposal(db: Session, public_id: str, reason: str) -> bool:
    prop = db.scalar(select(BrainProposal).where(BrainProposal.public_id == public_id))
    if not prop:
        return False
    ovs = list(
        db.scalars(
            select(RuntimeConfigOverride).where(RuntimeConfigOverride.proposal_public_id == public_id)
        )
    )
    first_ov_id = None
    for ov in ovs:
        if ov.status == "active":
            ov.status = "rolled_back"
            if first_ov_id is None:
                first_ov_id = ov.id
    prop.status = "rolled_back"
    prop.updated_at = _utcnow()
    db.add(
        RollbackEvent(
            proposal_id=prop.id,
            override_id=first_ov_id,
            reason=reason,
            payload_json=json.dumps({"at": _utcnow().isoformat()}, ensure_ascii=False),
        )
    )
    if prop.source_learning_artifact_id:
        art = db.get(LearningArtifact, prop.source_learning_artifact_id)
        if art:
            art.promotion_status = "rolled_back"
    return True


def expire_stale_proposals(db: Session) -> tuple[int, int]:
    """Mark proposals past expires_at as expired; expire runtime overrides. Returns (proposals, overrides)."""
    now = _utcnow()
    n_prop = 0
    rows = list(
        db.scalars(
            select(BrainProposal).where(
                BrainProposal.expires_at.is_not(None),
                BrainProposal.expires_at < now,
                BrainProposal.status.not_in(["rolled_back", "rejected", "expired"]),
            )
        )
    )
    for p in rows:
        p.status = "expired"
        n_prop += 1
    n_ov = expire_stale_runtime_overrides(db, now=now)
    return n_prop, n_ov


def list_proposals(
    db: Session,
    *,
    statuses: list[str] | None = None,
    limit: int = 50,
) -> list[BrainProposal]:
    lim = max(1, min(limit, 200))
    q = select(BrainProposal)
    if statuses:
        q = q.where(BrainProposal.status.in_(statuses))
    q = q.order_by(BrainProposal.created_at.desc()).limit(lim)
    return list(db.scalars(q))


def get_proposal_by_public_id(db: Session, public_id: str) -> BrainProposal | None:
    return db.scalar(select(BrainProposal).where(BrainProposal.public_id == public_id))


def proposal_to_dict(p: BrainProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "public_id": p.public_id,
        "status": p.status,
        "risk_class": p.risk_class,
        "title": p.title,
        "description": p.description,
        "target_config_name": p.target_config_name,
        "rollout_mode": p.rollout_mode,
        "ttl_seconds": p.ttl_seconds,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "source_learning_artifact_id": p.source_learning_artifact_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
