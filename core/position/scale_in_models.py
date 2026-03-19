# Models for Smart Scale-In (spec document/budget).
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScaleInAction(str, Enum):
    ADD_TO_POSITION = "ADD_TO_POSITION"
    HOLD_EXISTING = "HOLD_EXISTING"
    REJECT_SCALE_IN = "REJECT_SCALE_IN"


@dataclass
class ScaleInDecision:
    action: ScaleInAction
    reason: str
    add_qty: float = 0.0
    add_notional: float = 0.0
    expected_avg_entry: float = 0.0
    new_total_qty: float = 0.0
    new_total_notional: float = 0.0
    risk_snapshot: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
