"""Portfolio-level reflex (P2 placeholder — integrate with stress + BTC later)."""
from __future__ import annotations

from typing import Any

from core.market_data.client import MarketQuote
from core.portfolio.models import Position


def evaluate_portfolio_reflex_stub(
    open_positions: list[Position],
    quotes: dict[str, MarketQuote],
) -> dict[str, Any] | None:
    return None
