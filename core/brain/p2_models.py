"""SQLAlchemy models for Brain V4 P2 (evaluations, thesis, learning, portfolio state)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class DecisionEvaluation(Base):
    __tablename__ = "decision_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brain_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="", index=True)
    evaluation_phase: Mapped[str] = mapped_column(String(24), default="delayed")
    decision_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    follow_plan_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_realization_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis_management_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_shift_detection_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    damage_prevented_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    missed_opportunity_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    false_alarm_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ThesisStateEvent(Base):
    __tablename__ = "thesis_state_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brain_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="", index=True)
    previous_state: Mapped[str] = mapped_column(String(16), default="")
    new_state: Mapped[str] = mapped_column(String(16), default="")
    thesis_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_shift_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_shift_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ThesisActionEvent(Base):
    __tablename__ = "thesis_action_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brain_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(32), default="hold")
    action_strength: Mapped[float] = mapped_column(Float, default=0.0)
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(32), default="thesis_monitor")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class LearningArtifact(Base):
    __tablename__ = "learning_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brain_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="", index=True)
    artifact_type: Mapped[str] = mapped_column(String(48), default="trade_close_summary")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=1)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    promotion_status: Mapped[str] = mapped_column(String(20), default="none", index=True)
    promoted_proposal_public_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PortfolioStateEvent(Base):
    __tablename__ = "portfolio_state_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    brain_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    state_label: Mapped[str] = mapped_column(String(32), default="HEALTHY")
    portfolio_stress_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BrainProposal(Base):
    """
    Governance proposal: never writes base config files; activates only via RuntimeConfigOverride.
    Status: proposed | shadow | approved | active | rolled_back | rejected | expired
    """

    __tablename__ = "brain_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    risk_class: Mapped[str] = mapped_column(String(4), default="B", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    evidence_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    before_values_json: Mapped[str] = mapped_column(Text, default="{}")
    after_values_json: Mapped[str] = mapped_column(Text, default="{}")
    target_config_name: Mapped[str] = mapped_column(String(128), default="thesis_management.v1", index=True)
    rollout_mode: Mapped[str] = mapped_column(String(16), default="shadow")
    rollback_conditions_json: Mapped[str] = mapped_column(Text, default="{}")
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    source_learning_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_artifacts.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProposalEvidenceLink(Base):
    __tablename__ = "proposal_evidence_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("brain_proposals.id"), index=True)
    learning_artifact_id: Mapped[int] = mapped_column(ForeignKey("learning_artifacts.id"), index=True)
    link_role: Mapped[str] = mapped_column(String(32), default="primary")
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class ProposalReview(Base):
    __tablename__ = "proposal_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("brain_proposals.id"), index=True)
    decision: Mapped[str] = mapped_column(String(24), default="comment")
    reviewer_label: Mapped[str] = mapped_column(String(128), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RuntimeConfigOverride(Base):
    """Runtime merge patch applied on top of file config (TTL + status)."""

    __tablename__ = "runtime_config_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("brain_proposals.id"), index=True)
    proposal_public_id: Mapped[str] = mapped_column(String(36), index=True)
    target_config_name: Mapped[str] = mapped_column(String(128), default="thesis_management.v1", index=True)
    merge_patch_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    rollout_mode: Mapped[str] = mapped_column(String(16), default="full")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RollbackEvent(Base):
    __tablename__ = "rollback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("brain_proposals.id"), index=True)
    override_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_config_overrides.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AppliedConfigVersion(Base):
    """Audit trail of promoted / observed config hashes (does not replace files)."""

    __tablename__ = "applied_config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_name: Mapped[str] = mapped_column(String(128), index=True)
    version_label: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    proposal_public_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
