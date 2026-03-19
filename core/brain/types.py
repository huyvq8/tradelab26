"""V4 brain: string enums for JSON log / replay."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MarketState = Literal[
    "RISK_ON_TRENDING",
    "RISK_ON_EXHAUSTING",
    "BALANCED",
    "RISK_OFF",
    "SHOCK_UNSTABLE",
]

TokenState = Literal[
    "CONTINUATION",
    "EARLY_BREAKOUT",
    "LATE_BREAKOUT",
    "EXHAUSTION",
    "FAILED_BREAKOUT",
    "MEAN_REVERSION_CANDIDATE",
    "PANIC_UNWIND",
    "DEAD_CHOP",
]

PositionState = Literal[
    "THESIS_HEALTHY",
    "THESIS_STRETCHED",
    "THESIS_WEAK",
    "THESIS_BROKEN",
    "PROFIT_PROTECTED",
    "EXIT_URGENT",
]

PolicyMode = Literal[
    "DEFENSIVE",
    "NORMAL",
    "AGGRESSIVE",
    "CAPITAL_PRESERVATION",
    "EXIT_ONLY",
]

ShiftType = Literal[
    "NONE",
    "FAILED_BREAKOUT",
    "EXHAUSTION_BREAK",
    "BTC_LED_BREAK",
    "VOLATILITY_SHOCK",
    "LIQUIDITY_VACUUM",
    "CROWD_UNWIND",
    "THESIS_INVALIDATION_PRE_SL",
]

UrgencyLevel = Literal["NONE", "LOW", "MEDIUM", "HIGH"]

ReflexActionType = Literal[
    "NONE",
    "BLOCK_SCALE_IN",
    "REDUCE_ENTRY_SCORE",
    "ARM_TIGHTER_TRAIL",
    "PARTIAL_REDUCE",
    "TIGHTEN_STOP",
    "SUSPEND_NEW_ENTRIES_SYMBOL",
    "FORCE_REDUCE",
    "FORCE_EXIT",
    "POLICY_DOWNGRADE",
    "EXIT_ONLY_PORTFOLIO",
]


@dataclass
class PolicyModifiers:
    entry_strictness: float = 1.0
    score_threshold_offset: float = 0.0
    scale_in_allowed: bool = True
    trail_aggressiveness: float = 1.0
    partial_tp_speed: float = 1.0
    size_multiplier: float = 1.0
    no_trade_sensitivity: float = 1.0


@dataclass
class ChangePointResult:
    change_point_score: float
    context_break_flag: bool
    shift_type: ShiftType
    urgency_level: UrgencyLevel
    recommended_protective_action: ReflexActionType
    detector_scores: dict[str, float] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class PolicyDecision:
    active_policy_mode: PolicyMode
    policy_confidence: float
    policy_reason_codes: list[str]
    policy_ttl_sec: int
    re_evaluate_after_sec: int
    modifiers: PolicyModifiers


@dataclass
class BrainV4CycleContext:
    enabled: bool
    market_state: MarketState
    market_state_confidence: float
    policy: PolicyDecision
    change_point_market: ChangePointResult
    portfolio_stress_score: float
    kill_risk_near_limit: bool
    btc_regime: str
    regime_stability_proxy: float
    trace_id: str
    brain_cycle_id: str | None = None
    config_hash_v4: str = ""
    previous_policy_mode: str = ""
    symbol_change_points: dict[str, float] = field(default_factory=dict)
    symbol_change_point_results: dict[str, ChangePointResult] = field(default_factory=dict)
    market_decision_trace_id: str = ""
    symbol_decision_trace_ids: dict[str, str] = field(default_factory=dict)
