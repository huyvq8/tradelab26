"""
Strategy router: TokenProfile -> allowed_strategies, blocked_strategies, short_policy, hedge_policy, risk_profile.
Deterministic, explainable.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.intelligence.token_profile import TokenProfile


@dataclass
class RouteResult:
    allowed_strategies: list
    blocked_strategies: list
    short_policy: str  # allowed, restricted, disabled
    hedge_policy: str
    short_min_score_override: int | None
    risk_profile: dict


class StrategyRouter:
    def __init__(self):
        pass

    def route(self, profile: TokenProfile, all_strategy_names: list[str] | None = None) -> RouteResult:
        """
        From profile.preferred_strategies and profile.banned_strategies compute allowed/blocked.
        If preferred_strategies is non-empty, allowed = preferred; else allowed = all - banned.
        """
        allowed = list(profile.preferred_strategies) if profile.preferred_strategies else []
        banned = list(profile.banned_strategies) if profile.banned_strategies else []
        if all_strategy_names and not allowed:
            allowed = [s for s in all_strategy_names if s not in banned]
        elif not allowed and all_strategy_names:
            allowed = list(all_strategy_names)
        blocked = [s for s in (all_strategy_names or []) if s not in allowed] if all_strategy_names else banned
        return RouteResult(
            allowed_strategies=allowed,
            blocked_strategies=blocked,
            short_policy=profile.shortability,
            hedge_policy=profile.hedge_policy,
            short_min_score_override=getattr(profile, "short_min_score_override", None) if hasattr(profile, "short_min_score_override") else None,
            risk_profile=dict(profile.risk_profile or {}),
        )


def route_for_profile(
    profile: TokenProfile,
    all_strategy_names: list[str] | None = None,
) -> RouteResult:
    router = StrategyRouter()
    return router.route(profile, all_strategy_names)
