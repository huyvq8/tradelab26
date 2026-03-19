from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


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
    # Structural levels (sinh từ strategy + ATR; cycle có thể bỏ post-adjust khi levels_from_structure=True)
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    take_profit_extended: Optional[float] = None
    levels_from_structure: bool = False
    # Native structure diagnostics (không phụ thuộc cycle post-process)
    atr_estimate_1h: Optional[float] = None
    structure_meta: Optional[dict[str, Any]] = None
    # Native signal architecture (measurement / logs; scale-in may still use quality_score)
    setup_quality: Optional[float] = None  # 0..1 structural grade for this setup path
    entry_style: Optional[str] = None  # categorical: trend_continuation, breakout_expansion, ...
    extension_score: Optional[float] = None  # 0..1 position in recent range (see signal_structure)
    # Scale-in (document/budget): quality_score dung cho min_signal_score_to_scale_in; created_at cho signal freshness.
    quality_score: Optional[float] = None  # None = dung confidence thay the; often mirrors setup_quality
    created_at: Optional[datetime] = None  # None = coi nhu "vua tao" (fresh)
    # Capital split: "core" | "fast" — gán bởi router sau evaluate (core/portfolio/capital_split.py)
    capital_bucket: str = "core"


class BaseStrategy:
    name = "base"

    def evaluate(
        self,
        symbol: str,
        price: float,
        change_24h: float,
        volume_24h: float,
        regime: str,
        klines_1h: list | None = None,
    ) -> StrategySignal | None:
        raise NotImplementedError
