"""Short signal model — output chuẩn cho Smart Short Engine."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ShortSignal:
    """Tín hiệu short có xác nhận (sau pattern + score + filter)."""
    symbol: str
    setup_type: str  # pump_exhaustion | bull_trap | trend_pullback
    entry_price: float
    stop_loss: float
    take_profit: float  # TP chính (có thể dùng TP1; TP2 trong take_profit_levels)
    take_profit_levels: list[float]  # [TP1, TP2, ...]
    confidence_score: float
    reasons: list[str]
    invalidation_reason: str
    debug_metrics: dict
    regime: str = ""

    @property
    def side(self) -> str:
        return "short"
