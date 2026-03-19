"""SQLAlchemy models for Brain V4 P1 event persistence."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class BrainCycle(Base):
    __tablename__ = "brain_cycles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"), nullable=True, index=True)
    config_hash_v4: Mapped[str] = mapped_column(String(64), default="")
    trace_version: Mapped[str] = mapped_column(String(8), default="p1")
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class StateInferenceEvent(Base):
    __tablename__ = "state_inference_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    inferred_market_state: Mapped[str] = mapped_column(String(32), default="")
    inferred_token_state: Mapped[str] = mapped_column(String(32), default="")
    inferred_position_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conf_market: Mapped[float] = mapped_column(Float, default=0.0)
    conf_token: Mapped[float] = mapped_column(Float, default=0.0)
    conf_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    config_hash_v4: Mapped[str] = mapped_column(String(64), default="")


class ChangePointEvent(Base):
    __tablename__ = "change_point_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    structure_score: Mapped[float] = mapped_column(Float, default=0.0)
    participation_score: Mapped[float] = mapped_column(Float, default=0.0)
    btc_leader_score: Mapped[float] = mapped_column(Float, default=0.0)
    crowding_score: Mapped[float] = mapped_column(Float, default=0.0)
    shock_score: Mapped[float] = mapped_column(Float, default=0.0)
    change_point_score: Mapped[float] = mapped_column(Float, default=0.0)
    context_break_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    shift_type: Mapped[str] = mapped_column(String(40), default="NONE")
    urgency_level: Mapped[str] = mapped_column(String(16), default="NONE")
    recommended_action: Mapped[str] = mapped_column(String(32), default="NONE")
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    config_hash_v4: Mapped[str] = mapped_column(String(64), default="")


class PolicyModeEvent(Base):
    __tablename__ = "policy_mode_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="market")
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    previous_mode: Mapped[str] = mapped_column(String(24), default="")
    new_mode: Mapped[str] = mapped_column(String(24), default="")
    policy_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    switch_reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    cooldown_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    ttl_sec: Mapped[int] = mapped_column(Integer, default=0)
    re_evaluate_after_sec: Mapped[int] = mapped_column(Integer, default=0)
    emergency_override: Mapped[bool] = mapped_column(Boolean, default=False)
    config_hash_v4: Mapped[str] = mapped_column(String(64), default="")


class ReflexActionEvent(Base):
    __tablename__ = "reflex_action_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="")
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    urgency_level: Mapped[str] = mapped_column(String(16), default="")
    reflex_action: Mapped[str] = mapped_column(String(32), default="")
    preconditions_json: Mapped[str] = mapped_column(Text, default="{}")
    action_reason: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(24), default="skipped")
    linked_trade_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    change_point_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BrainSizingEvent(Base):
    """Audit trail: risk → pre policy modifier → post modifier → final executable."""

    __tablename__ = "brain_sizing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(36), index=True)
    decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market_decision_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str] = mapped_column(String(50), default="")
    side: Mapped[str] = mapped_column(String(10), default="")
    ts_utc: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    post_risk_engine_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pre_modifier_usd: Mapped[float] = mapped_column(Float, default=0.0)
    post_modifier_usd: Mapped[float] = mapped_column(Float, default=0.0)
    final_executable_usd: Mapped[float] = mapped_column(Float, default=0.0)
    available_cash_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    modifier_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    config_hash_v4: Mapped[str] = mapped_column(String(64), default="")
