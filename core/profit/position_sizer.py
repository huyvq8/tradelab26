"""
Phase 2 v6: Dynamic position sizing — nhân base size với confidence_mult và regime_score.
Strategy weight và portfolio heat sẽ thêm ở Phase 3 (tạm dùng 1.0).
"""
from __future__ import annotations

from core.profit.volatility_guard import load_profit_config


def get_confidence_multiplier(confidence: float, config: dict | None = None) -> float:
    """
    Map signal.confidence (vd. 0.55–0.72) sang multiplier theo config.
    Mặc định: < 0.6 -> 0.75, 0.6–0.7 -> 1.0, >= 0.7 -> 1.2.
    """
    cfg = config or load_profit_config()
    sizing = cfg.get("sizing") or {}
    buckets = sizing.get("confidence_multipliers") or [
        {"max": 0.6, "mult": 0.75},
        {"max": 0.7, "mult": 1.0},
        {"max": 1.0, "mult": 1.2},
    ]
    for b in buckets:
        if confidence <= float(b.get("max", 1.0)):
            return float(b.get("mult", 1.0))
    return 1.0


def get_regime_score(
    regime: str,
    config: dict | None = None,
    *,
    strategy_name: str | None = None,
) -> float:
    """
    Map regime (high_momentum, risk_off, balanced) sang score 0.3–1.2.
    risk_off -> nhỏ (0.4), balanced -> 0.85, high_momentum -> 1.0.

    Optional override: ``sizing.regime_scores_by_strategy.<strategy_name>.<regime>``.
    """
    cfg = config or load_profit_config()
    sizing = cfg.get("sizing") or {}
    sn = (strategy_name or "").strip()
    by_strat = sizing.get("regime_scores_by_strategy") or {}
    if sn and isinstance(by_strat.get(sn), dict):
        m = by_strat[sn]
        if regime in m:
            try:
                return float(m[regime])
            except (TypeError, ValueError):
                pass
    scores = sizing.get("regime_scores") or {
        "high_momentum": 1.0,
        "risk_off": 0.4,
        "balanced": 0.85,
    }
    return float(scores.get(regime, scores.get("balanced", 0.85)))


def apply_dynamic_sizing(
    base_size_usd: float,
    confidence: float,
    regime: str,
    config: dict | None = None,
    *,
    strategy_weight: float = 1.0,
    portfolio_heat_mult: float = 1.0,
    strategy_name: str | None = None,
) -> float:
    """
    Áp dụng công thức v6: final_size = base_size × confidence_mult × regime_score × strategy_weight × portfolio_heat_mult.
    Phase 2 chỉ dùng confidence_mult và regime_score; strategy_weight và portfolio_heat_mult mặc định 1.0.
    """
    conf_mult = get_confidence_multiplier(confidence, config)
    reg_score = get_regime_score(regime, config, strategy_name=strategy_name)
    final = base_size_usd * conf_mult * reg_score * strategy_weight * portfolio_heat_mult
    return round(max(0.0, final), 2)
