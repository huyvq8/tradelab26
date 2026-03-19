"""Uoc luong size USD toi thieu truoc full sizing — giam spam reject."""
from __future__ import annotations

from core.config import settings
from core.risk.engine import effective_risk_capital_usd
from core.strategies.base import StrategySignal


def estimate_max_size_usd_from_risk(
    signal: StrategySignal,
    *,
    available_cash: float,
    capital_usd_for_risk: float | None,
    risk_pct: float | None,
) -> float:
    cap = effective_risk_capital_usd(capital_usd_for_risk)
    rp = float(risk_pct) if risk_pct is not None and 0 < float(risk_pct) < 1 else float(settings.default_risk_pct)
    stop_distance = abs(signal.entry_price - signal.stop_loss) / max(signal.entry_price, 1e-9)
    if stop_distance <= 0:
        return 0.0
    risk_dollars = cap * rp
    return min(float(available_cash), risk_dollars / stop_distance)


def is_likely_below_min_position_usd(
    signal: StrategySignal,
    *,
    available_cash: float,
    capital_usd_for_risk: float | None,
    risk_pct: float | None,
    min_usd: float = 25.0,
    buffer: float = 2.0,
) -> bool:
    est = estimate_max_size_usd_from_risk(
        signal,
        available_cash=available_cash,
        capital_usd_for_risk=capital_usd_for_risk,
        risk_pct=risk_pct,
    )
    return est < (min_usd + buffer)
