"""Approval rules from proposal_governance.v1.json."""
from __future__ import annotations

from core.brain.proposal_governance_config import load_proposal_governance_config
from core.brain.proposal_service import approval_required_for_class, min_evidence_samples_for_class


def governance_classes() -> dict:
    return load_proposal_governance_config().get("classes") or {}


def approval_rules() -> dict:
    return load_proposal_governance_config().get("approval_rules") or {}
