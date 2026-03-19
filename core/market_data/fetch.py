"""Fetch prices via CMC client; used by worker/orchestration."""
from core.market_data.client import get_market_client


def fetch_prices(symbols: list[str] | None = None) -> dict[str, float]:
    """Return symbol -> price for use in simple contexts. For full quote use client.get_quotes."""
    symbols = symbols or ["BTC", "ETH"]
    client = get_market_client()
    quotes = client.get_quotes(symbols)
    return {s: q.price for s, q in quotes.items()}
