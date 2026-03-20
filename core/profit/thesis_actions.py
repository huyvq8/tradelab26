"""Map thesis state to recommended action (merged later with reflex / policy)."""
from __future__ import annotations

from typing import Any


def thesis_state_to_action(
    thesis_state: str,
    zone_level: str,
    *,
    force_close_on_invalid: bool,
) -> dict[str, Any]:
    st = (thesis_state or "NORMAL").upper()
    zl = (zone_level or "low").lower()
    if st == "INVALID" and force_close_on_invalid:
        return {
            "action_type": "force_close",
            "action_strength": 1.0,
            "reason_codes": ["thesis_invalid_force_close"],
        }
    if st == "INVALID":
        return {
            "action_type": "hold",
            "action_strength": 0.0,
            "reason_codes": ["thesis_invalid_monitor_only"],
        }
    if st == "DANGER" or zl == "critical":
        return {
            "action_type": "tighten_sl",
            "action_strength": 0.65,
            "reason_codes": ["thesis_danger_or_critical_zone"],
        }
    if st == "WARNING" or zl == "high":
        return {
            "action_type": "reduce",
            "action_strength": 0.35,
            "reason_codes": ["thesis_warning_or_high_zone"],
        }
    return {"action_type": "hold", "action_strength": 0.0, "reason_codes": []}
