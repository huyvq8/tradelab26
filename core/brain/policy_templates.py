"""V4 policy mode → runtime modifiers (replaces scattered thresholds)."""
from __future__ import annotations

from core.brain.types import PolicyMode, PolicyModifiers

TEMPLATES: dict[PolicyMode, PolicyModifiers] = {
    "DEFENSIVE": PolicyModifiers(
        entry_strictness=1.45,
        score_threshold_offset=0.08,
        scale_in_allowed=False,
        trail_aggressiveness=1.25,
        partial_tp_speed=1.15,
        size_multiplier=0.5,
        no_trade_sensitivity=1.2,
    ),
    "NORMAL": PolicyModifiers(
        entry_strictness=1.0,
        score_threshold_offset=0.0,
        scale_in_allowed=True,
        trail_aggressiveness=1.0,
        partial_tp_speed=1.0,
        size_multiplier=1.0,
        no_trade_sensitivity=1.0,
    ),
    "AGGRESSIVE": PolicyModifiers(
        entry_strictness=0.82,
        score_threshold_offset=-0.04,
        scale_in_allowed=True,
        trail_aggressiveness=0.92,
        partial_tp_speed=0.92,
        size_multiplier=1.25,
        no_trade_sensitivity=0.88,
    ),
    "CAPITAL_PRESERVATION": PolicyModifiers(
        entry_strictness=1.65,
        score_threshold_offset=0.12,
        scale_in_allowed=False,
        trail_aggressiveness=1.35,
        partial_tp_speed=1.35,
        size_multiplier=0.35,
        no_trade_sensitivity=1.35,
    ),
    "EXIT_ONLY": PolicyModifiers(
        entry_strictness=99.0,
        score_threshold_offset=0.5,
        scale_in_allowed=False,
        trail_aggressiveness=1.4,
        partial_tp_speed=1.4,
        size_multiplier=0.0,
        no_trade_sensitivity=2.0,
    ),
}


def modifiers_for(mode: PolicyMode) -> PolicyModifiers:
    return TEMPLATES.get(mode, TEMPLATES["NORMAL"])
