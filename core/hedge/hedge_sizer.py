# Hedge sizing: 20-50% of main position, never 1:1
from __future__ import annotations


def hedge_size_usd(
    main_position_size_usd: float,
    main_quantity: float,
    entry_price: float,
    pnl_usd: float,
    risk_usd: float | None,
    config: dict | None = None,
) -> float:
    """
    Return hedge size in USD. Between min_hedge_ratio and max_hedge_ratio of main.
    Can scale up with profit (more profit -> allow slightly larger hedge).
    """
    if not config:
        return 0.0
    max_r = float(config.get("max_hedge_ratio", 0.5))
    min_r = float(config.get("min_hedge_ratio", 0.2))
    if main_position_size_usd <= 0:
        return 0.0
    ratio = min_r
    if pnl_usd > 0 and risk_usd and risk_usd > 0:
        r_achieved = pnl_usd / risk_usd
        if r_achieved >= 1.0:
            ratio = min(max_r, min_r + 0.2)
        if r_achieved >= 2.0:
            ratio = max_r
    else:
        ratio = min_r
    return round(main_position_size_usd * ratio, 2)
