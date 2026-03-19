"""
Token features for classification: liquidity, volatility, noise (wick ratio), pump frequency proxy.
Deterministic, explainable; all in debug_metrics.
"""
from __future__ import annotations

from typing import Any


def _ohlcv(c: Any) -> tuple[float, float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else 0)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else 0)
    lo = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else 0)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else 0)
    v = getattr(c, "volume", None) or (c.get("volume") if isinstance(c, dict) else 0)
    return (float(o or 0), float(h or 0), float(lo or 0), float(cl or 0), float(v or 0))


def _atr(candles: list, period: int = 14) -> float | None:
    if not candles or len(candles) < 2 or period < 1:
        return None
    tr_list = []
    prev_close = None
    for c in candles:
        o, h, lo, cl, _ = _ohlcv(c)
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        tr_list.append(tr)
        prev_close = cl
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else None
    return sum(tr_list[-period:]) / period


def _wick_ratio(candles: list, lookback: int = 5) -> float | None:
    """(upper_wick + lower_wick) / body avg; high = noisy."""
    if not candles or lookback < 1:
        return None
    total_ratio = 0.0
    n = 0
    for c in candles[-lookback:]:
        o, h, lo, cl, _ = _ohlcv(c)
        body = abs(cl - o) or 1e-10
        upper = h - max(o, cl)
        lower = min(o, cl) - lo
        total_ratio += (upper + lower) / body
        n += 1
    return total_ratio / n if n else None


def _pump_frequency_proxy(candles: list, pct_threshold: float = 5.0, lookback: int = 20) -> int:
    """Count candles with large move (|close-open|/open >= pct_threshold)."""
    if not candles or lookback < 1:
        return 0
    count = 0
    for c in candles[-lookback:]:
        o, _, _, cl, _ = _ohlcv(c)
        if o and abs(cl - o) / o * 100 >= pct_threshold:
            count += 1
    return count


def build_token_features(
    symbol: str,
    quote: Any,
    klines: list,
    config: dict | None = None,
) -> dict:
    """
    Build features dict: avg_daily_volume, atr, atr_pct, wick_ratio, pump_frequency_proxy.
    All values also in debug_metrics for logging.
    """
    cfg = config or {}
    lookback = int(cfg.get("feature_lookback", 20))
    price = getattr(quote, "price", None) or (quote.get("price") if isinstance(quote, dict) else 0)
    volume_24h = getattr(quote, "volume_24h", None) or (quote.get("volume_24h") if isinstance(quote, dict) else 0)
    avg_daily_volume = float(volume_24h or 0)
    atr = _atr(klines, 14)
    atr_pct = (atr / price * 100) if (atr and price) else None
    wick_ratio = _wick_ratio(klines, min(lookback, len(klines)))
    pump_freq = _pump_frequency_proxy(klines, pct_threshold=float(cfg.get("pump_pct_threshold", 5.0)), lookback=lookback)
    debug_metrics = {
        "symbol": symbol,
        "avg_daily_volume": avg_daily_volume,
        "atr": atr,
        "atr_pct": atr_pct,
        "wick_ratio": wick_ratio,
        "pump_frequency_proxy": pump_freq,
        "lookback": lookback,
    }
    return {
        "symbol": symbol,
        "avg_daily_volume": avg_daily_volume,
        "atr": atr,
        "atr_pct": atr_pct,
        "wick_ratio": wick_ratio,
        "pump_frequency_proxy": pump_freq,
        "debug_metrics": debug_metrics,
    }
