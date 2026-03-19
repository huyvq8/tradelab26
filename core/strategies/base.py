from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class StrategySignal:
    symbol: str
    strategy_name: str
    side: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    rationale: str
    regime: str
    # Scale-in (document/budget): quality_score dung cho min_signal_score_to_scale_in; created_at cho signal freshness.
    quality_score: Optional[float] = None  # None = dung confidence thay the
    created_at: Optional[datetime] = None  # None = coi nhu "vua tao" (fresh)


class BaseStrategy:
    name = "base"

    def evaluate(
        self,
        symbol: str,
        price: float,
        change_24h: float,
        volume_24h: float,
        regime: str,
    ) -> StrategySignal | None:
        raise NotImplementedError
