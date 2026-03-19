# Hedge decision: allow only when in profit or MTF alignment
from __future__ import annotations

from typing import Any

from core.hedge.hedge_rules import HedgeRules, may_hedge_when_in_profit, may_hedge_multi_timeframe


class HedgeDecisionEngine:
    def __init__(self, config: dict | None = None):
        self.rules = HedgeRules.from_config(config or {})

    def allow_hedge(
        self,
        position: Any,
        pnl_usd: float,
        risk_usd: float | None,
        htf_bullish: bool | None = None,
        ltf_bearish_pullback: bool | None = None,
    ) -> tuple[bool, str]:
        """
        Return (allowed, reason). Hedge only when rules.enabled and (in profit or MTF).
        """
        if not self.rules.enabled:
            return (False, "hedge_disabled")
        if position is None:
            return (False, "no_position")
        side = getattr(position, "side", None) or ""
        if pnl_usd < 0:
            if self.rules.only_if_in_profit:
                return (False, "HEDGE_REJECTED_NOT_IN_PROFIT")
            if not self.rules.allow_multi_timeframe_hedge:
                return (False, "HEDGE_REJECTED_NO_MTF")
            if htf_bullish is None or ltf_bearish_pullback is None:
                return (False, "HEDGE_REJECTED_NO_MTF_ALIGNMENT")
            if not may_hedge_multi_timeframe(htf_bullish, ltf_bearish_pullback or False, side):
                return (False, "HEDGE_REJECTED_NO_MTF_ALIGNMENT")
        else:
            if not may_hedge_when_in_profit(
                pnl_usd,
                risk_usd,
                self.rules.min_profit_to_hedge,
                self.rules.min_profit_r,
            ):
                return (False, "HEDGE_REJECTED_MIN_PROFIT_NOT_MET")
        return (True, "HEDGE_ALLOWED")


def hedge_allowed_for_position(
    position: Any,
    pnl_usd: float,
    risk_usd: float | None,
    config: dict | None = None,
    htf_bullish: bool | None = None,
    ltf_bearish_pullback: bool | None = None,
) -> tuple[bool, str]:
    engine = HedgeDecisionEngine(config)
    return engine.allow_hedge(position, pnl_usd, risk_usd, htf_bullish, ltf_bearish_pullback)
