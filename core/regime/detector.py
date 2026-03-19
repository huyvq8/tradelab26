def detect_regime(volatility: float, trend: float) -> str:
    """Classify from volatility/trend inputs (e.g. for backtest)."""
    if volatility > 2 and trend > 0:
        return "high_vol_trend"
    if volatility < 1:
        return "range"
    return "mixed"


def derive_regime(change_24h: float, volume_24h: float) -> str:
    """Derive regime from 24h quote (used in live cycle). Matches strategy expectations."""
    if change_24h > 5 and volume_24h > 5_000_000:
        return "high_momentum"
    if change_24h < -5:
        return "risk_off"
    return "balanced"
