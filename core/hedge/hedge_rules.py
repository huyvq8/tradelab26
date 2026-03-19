# Hedge rules: only when in profit or MTF
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HedgeRules:
    enabled: bool = False
    only_if_in_profit: bool = True
    min_profit_to_hedge: float = 0.0
    min_profit_r: float = 1.0
    allow_multi_timeframe_hedge: bool = True
    max_hedge_ratio: float = 0.5
    min_hedge_ratio: float = 0.2
    max_hedge_duration_bars: int = 24
    reject_if_no_unwind_plan: bool = True

    @classmethod
    def from_config(cls, config: dict | None) -> "HedgeRules":
        if not config:
            return cls(enabled=False)
        return cls(
            enabled=bool(config.get("enabled", False)),
            only_if_in_profit=bool(config.get("only_if_in_profit", True)),
            min_profit_to_hedge=float(config.get("min_profit_to_hedge", 0)),
            min_profit_r=float(config.get("min_profit_r", 1.0)),
            allow_multi_timeframe_hedge=bool(config.get("allow_mtf", config.get("allow_multi_timeframe_hedge", True))),
            max_hedge_ratio=float(config.get("max_hedge_ratio", 0.5)),
            min_hedge_ratio=float(config.get("min_hedge_ratio", 0.2)),
            max_hedge_duration_bars=int(config.get("max_hedge_duration_bars", 24)),
            reject_if_no_unwind_plan=bool(config.get("reject_if_no_unwind_plan", True)),
        )


def may_hedge_when_in_profit(
    pnl_usd: float,
    risk_usd: float | None,
    min_profit_usd: float,
    min_profit_r: float,
) -> bool:
    if pnl_usd < min_profit_usd:
        return False
    if risk_usd and risk_usd > 0 and min_profit_r > 0:
        r = pnl_usd / risk_usd
        if r < min_profit_r:
            return False
    return True


def may_hedge_multi_timeframe(
    htf_bullish: bool,
    ltf_bearish_pullback: bool,
    position_side: str,
) -> bool:
    if position_side == "long" and htf_bullish and ltf_bearish_pullback:
        return True
    if position_side == "short" and not htf_bullish and ltf_bearish_pullback is False:
        return True
    return False
