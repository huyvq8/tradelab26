"""TokenProfile: output of classification + policy (shortability, hedge_policy, risk_profile)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenProfile:
    symbol: str
    token_type: str  # major, large_cap_alt, mid_cap_alt, low_cap, meme, narrative
    liquidity_tier: str  # high, medium, low
    volatility_tier: str  # low, medium, high, extreme
    manipulation_risk: str  # low, medium, high
    trend_cleanliness: str  # clean, mixed, noisy
    shortability: str  # allowed, restricted, disabled
    hedge_policy: str  # allowed, restricted, disabled
    short_min_score_override: int | None  # if set, short engine uses this instead of global min_score
    preferred_strategies: list
    banned_strategies: list
    risk_profile: dict  # max_leverage, risk_per_trade_pct, min_rr, timeout_bars, tp_style
    debug_metrics: dict
