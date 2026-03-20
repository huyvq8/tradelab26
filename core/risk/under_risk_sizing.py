"""
Post-modifier sizing clamp: respect risk-engine notional ceiling and optional under-risk lift.

Risk engine returns ``post_risk_engine_usd`` ≈ notional that matches target risk % at the
signal stop. Downstream modifiers (regime, bot-edge, brain policy) can shrink that heavily
("under-risk"). This module:

1. Caps at ``post_risk_engine_usd`` (never above risk-approved size).
2. Optionally, if size is still ≥ ``eff_min_trade_usd`` but below
   ``fraction * post_risk_engine_usd``, lifts toward that floor (capped by cash + ceiling).

Config (``profit_cfg["sizing"]``):

- ``under_risk_min_fraction_of_risk_ceiling`` (float, default 0): e.g. ``0.65`` means
  when modifiers yield notional < 65% of risk ceiling (and ≥ min trade), bump up to that
  fraction of the ceiling (still ≤ ceiling and ≤ available cash).
"""
from __future__ import annotations

from typing import Any


def apply_risk_ceiling_and_under_risk_floor(
    *,
    final_size_usd: float,
    post_risk_engine_usd: float,
    eff_min_trade_usd: float,
    available_cash: float,
    sizing_cfg: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    """
    Returns (adjusted_notional_usd, meta) where meta may include ``under_risk_rescale``.
    """
    meta: dict[str, Any] = {}
    risk_ceiling = max(0.0, float(post_risk_engine_usd))
    x = float(final_size_usd)
    x = min(x, risk_ceiling) if risk_ceiling > 0 else x

    sc = sizing_cfg or {}
    try:
        frac = float(sc.get("under_risk_min_fraction_of_risk_ceiling", 0) or 0)
    except (TypeError, ValueError):
        frac = 0.0

    if frac > 0 and risk_ceiling > 0:
        floor_from_risk = round(min(risk_ceiling * frac, risk_ceiling), 2)
        eff_min = float(eff_min_trade_usd)
        if x >= eff_min - 1e-9 and x < floor_from_risk - 1e-9:
            cash = max(0.0, float(available_cash))
            x_new = min(risk_ceiling, cash, max(x, floor_from_risk))
            x_new = round(x_new, 2)
            if x_new > x + 1e-9:
                meta["under_risk_rescale"] = {
                    "from_usd": round(x, 4),
                    "to_usd": x_new,
                    "risk_ceiling_usd": round(risk_ceiling, 4),
                    "fraction_threshold": frac,
                    "floor_from_risk_usd": floor_from_risk,
                }
            x = x_new

    return x, meta
