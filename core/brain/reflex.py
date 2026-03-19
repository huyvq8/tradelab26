"""V4 protective reflex: map change-point + position state → single primary action."""
from __future__ import annotations

from dataclasses import dataclass

from core.brain.types import (
    ChangePointResult,
    PolicyMode,
    PositionState,
    ReflexActionType,
    UrgencyLevel,
)


@dataclass
class ReflexResult:
    primary_action: ReflexActionType
    urgency: UrgencyLevel
    reduce_fraction: float
    thesis_broken_before_sl: bool
    reason_codes: list[str]
    block_scale_in: bool
    suspend_new_entries_symbol: bool


def resolve_reflex(
    cp: ChangePointResult,
    pos_state: PositionState,
    policy_mode: PolicyMode,
    *,
    side: str,
    btc_risk_off_long: bool,
) -> ReflexResult | None:
    """Returns None if no reflex; else caller may execute FORCE_EXIT / PARTIAL_REDUCE."""
    reasons: list[str] = []
    block_si = False
    susp = False
    reduce_f = 0.0
    thesis_pre_sl = cp.shift_type == "THESIS_INVALIDATION_PRE_SL" or pos_state in (
        "THESIS_BROKEN",
        "EXIT_URGENT",
    )

    # HIGH
    if (
        cp.change_point_score >= 0.85
        or pos_state in ("THESIS_BROKEN", "EXIT_URGENT")
        or (cp.shift_type == "BTC_LED_BREAK" and cp.detector_scores.get("structure", 0) >= 0.65)
    ):
        return ReflexResult(
            primary_action="FORCE_EXIT",
            urgency="HIGH",
            reduce_fraction=1.0,
            thesis_broken_before_sl=thesis_pre_sl,
            reason_codes=reasons + ["reflex_high", cp.shift_type],
            block_scale_in=True,
            suspend_new_entries_symbol=True,
        )

    # MEDIUM
    if cp.change_point_score >= 0.7 or (btc_risk_off_long and side == "long"):
        reduce_f = 0.35
        susp = True
        return ReflexResult(
            primary_action="PARTIAL_REDUCE",
            urgency="MEDIUM",
            reduce_fraction=reduce_f,
            thesis_broken_before_sl=False,
            reason_codes=reasons + ["reflex_medium"],
            block_scale_in=True,
            suspend_new_entries_symbol=susp,
        )

    # LOW
    if cp.change_point_score >= 0.55 and pos_state not in ("THESIS_HEALTHY", "PROFIT_PROTECTED"):
        block_si = True
        return ReflexResult(
            primary_action="BLOCK_SCALE_IN",
            urgency="LOW",
            reduce_fraction=0.0,
            thesis_broken_before_sl=False,
            reason_codes=reasons + ["reflex_low"],
            block_scale_in=block_si,
            suspend_new_entries_symbol=False,
        )

    if cp.change_point_score >= 0.55 and policy_mode == "AGGRESSIVE":
        return ReflexResult(
            primary_action="POLICY_DOWNGRADE",
            urgency="LOW",
            reduce_fraction=0.0,
            thesis_broken_before_sl=False,
            reason_codes=["reflex_downgrade_aggressive"],
            block_scale_in=True,
            suspend_new_entries_symbol=False,
        )

    return None
