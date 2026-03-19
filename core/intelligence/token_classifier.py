# Deterministic classifier: features -> token_type, tiers; merge with policy for full TokenProfile
from __future__ import annotations

from core.intelligence.token_profile import TokenProfile
from core.intelligence.strategy_policy_registry import get_policy_for_token_type, load_routing_config


def classify_token_type(features: dict, config: dict) -> tuple[str, str, str, str, str, dict]:
    cfg = config or {}
    if not cfg.get("enabled", True):
        return ("mid_cap_alt", "medium", "medium", "medium", "mixed", features.get("debug_metrics", {}))
    symbol = (features.get("symbol") or "").strip().upper()
    major_list = [s.strip().upper() for s in cfg.get("major_symbols", ["BTC", "ETH"])]
    if symbol in major_list:
        return ("major", "high", "low", "low", "clean", {**features.get("debug_metrics", {}), "reason": "major_symbols"})

    vol = float(features.get("avg_daily_volume", 0) or 0)
    atr_pct = features.get("atr_pct")
    wick = features.get("wick_ratio")
    pump_freq = int(features.get("pump_frequency_proxy", 0) or 0)

    vt = cfg.get("volume_tiers") or {}
    high_min = float(vt.get("high_min", 50_000_000))
    medium_min = float(vt.get("medium_min", 5_000_000))
    if vol >= high_min:
        liquidity_tier = "high"
    elif vol >= medium_min:
        liquidity_tier = "medium"
    else:
        liquidity_tier = "low"

    at = cfg.get("atr_pct_tiers") or {}
    low_max_atr = float(at.get("low_max", 2.0))
    medium_max_atr = float(at.get("medium_max", 4.0))
    high_max_atr = float(at.get("high_max", 8.0))
    if atr_pct is None:
        volatility_tier = "medium"
    elif atr_pct <= low_max_atr:
        volatility_tier = "low"
    elif atr_pct <= medium_max_atr:
        volatility_tier = "medium"
    elif atr_pct <= high_max_atr:
        volatility_tier = "high"
    else:
        volatility_tier = "extreme"

    wick_noisy_min = float(cfg.get("wick_ratio_noisy_min", 2.0))
    trend_cleanliness = "noisy" if (wick is not None and wick >= wick_noisy_min) else ("clean" if liquidity_tier == "high" else "mixed")

    pump_low = int(cfg.get("pump_frequency_low_cap_min", 4))
    pump_meme = int(cfg.get("pump_frequency_meme_min", 6))
    if volatility_tier == "extreme" and (wick or 0) >= wick_noisy_min and pump_freq >= pump_meme:
        token_type = "meme"
        manipulation_risk = "high"
    elif liquidity_tier == "low" and pump_freq >= pump_low:
        token_type = "low_cap"
        manipulation_risk = "high"
    elif liquidity_tier == "low":
        token_type = "low_cap"
        manipulation_risk = "medium"
    elif liquidity_tier == "high" and volatility_tier in ("low", "medium"):
        token_type = "large_cap_alt"
        manipulation_risk = "low"
    elif liquidity_tier == "medium":
        token_type = "mid_cap_alt"
        manipulation_risk = "medium"
    else:
        token_type = "mid_cap_alt"
        manipulation_risk = "medium"

    debug = {**features.get("debug_metrics", {}), "liquidity_tier": liquidity_tier, "volatility_tier": volatility_tier}
    return (token_type, liquidity_tier, volatility_tier, manipulation_risk, trend_cleanliness, debug)


def classify_token(
    symbol: str,
    features: dict,
    classification_config: dict,
    routing_config: dict | None = None,
) -> TokenProfile:
    routing_config = routing_config or load_routing_config()
    token_type, liq, vol, manip, trend, debug = classify_token_type(features, classification_config)
    policy = get_policy_for_token_type(token_type, routing_config)
    return TokenProfile(
        symbol=symbol,
        token_type=token_type,
        liquidity_tier=liq,
        volatility_tier=vol,
        manipulation_risk=manip,
        trend_cleanliness=trend,
        shortability=policy.get("shortability", "allowed"),
        hedge_policy=policy.get("hedge_policy", "restricted"),
        short_min_score_override=policy.get("short_min_score_override"),
        preferred_strategies=policy.get("allowed_strategies", []),
        banned_strategies=policy.get("banned_strategies", []),
        risk_profile=policy.get("risk_profile", {}),
        debug_metrics=debug,
    )
