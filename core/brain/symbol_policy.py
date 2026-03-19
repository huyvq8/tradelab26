"""Rules for symbol-scoped policy vs market-wide policy (P1 contract)."""
from __future__ import annotations

from core.brain.types import ChangePointResult, PolicyDecision


def symbol_policy_override_allowed(
    *,
    context_break: bool,
    position_state: str | None,
    market_policy_mode: str = "",
) -> bool:
    """
    Symbol-level PolicyMode may differ from market-wide **only** when:
    - this symbol has an explicit context break (change-point / structure), or
    - position thesis state is clearly not healthy (open position review path).

    Otherwise we keep symbol effective policy = market policy to avoid conflicts
    (e.g. market NORMAL + symbol EXIT_ONLY) on sizing and dashboards.
    """
    _ = market_policy_mode
    if context_break:
        return True
    ps = (position_state or "").strip().upper()
    if ps and ps not in ("THESIS_HEALTHY", "PROFIT_PROTECTED", ""):
        return True
    return False


def effective_policy_mode_for_symbol(
    market_policy: PolicyDecision,
    *,
    symbol_cp: ChangePointResult | None = None,
    position_state: str | None = None,
) -> str:
    """
    P1 execution: sizing/gates use **market** policy only.
    Future: if symbol PolicyModeEvent is added, gate with symbol_policy_override_allowed(...).
    """
    _ = (symbol_cp, position_state)
    return str(market_policy.active_policy_mode)
