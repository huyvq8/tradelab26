"""Structured root-cause for post-sizing rejects (internal floor, policy chain, exchange lot)."""
from __future__ import annotations

from typing import Any

from core.strategies.base import StrategySignal


def effective_internal_min_trade_usd(
    profit_cfg: dict | None,
    *,
    single_strategy_mode: str | None = None,
) -> float:
    """
    Default internal minimum USD before ``open_position`` (paper and pre-flight).
    When ``single_strategy_mode == mean_reversion`` (dashboard ``single_strategy`` override)
    and ``mr_only_min_trade_usd`` is set, that lower floor applies; otherwise
    ``internal_min_trade_usd`` applies for all strategies.
    """
    sizing = (profit_cfg or {}).get("sizing") or {}
    base = float(sizing.get("internal_min_trade_usd", 25) or 25)
    mr_floor = sizing.get("mr_only_min_trade_usd")
    if (single_strategy_mode or "").strip() == "mean_reversion" and mr_floor is not None:
        try:
            v = float(mr_floor)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return max(1.0, base)


def build_sizing_stage_diagnostics(
    *,
    post_risk_engine_usd: float,
    size_after_vol: float,
    after_dynamic_usd: float,
    after_combo_usd: float,
    pre_modifier_usd: float,
    post_policy_usd: float,
    post_modifier_usd: float,
    confidence_mult: float,
    regime_score: float,
    strategy_weight: float,
    portfolio_heat_mult: float,
) -> dict[str, float]:
    """Linear decomposition matching ``apply_dynamic_sizing`` factor order."""
    s0 = float(size_after_vol)
    s1 = round(s0 * float(confidence_mult), 4)
    s2 = round(s1 * float(regime_score), 4)
    s3 = round(s2 * float(strategy_weight), 4)
    s4 = round(s3 * float(portfolio_heat_mult), 4)
    return {
        "base_size_usd": round(float(post_risk_engine_usd), 4),
        "after_volatility_size_usd": round(s0, 4),
        "after_confidence_size_usd": s1,
        "after_regime_size_usd": s2,
        "after_strategy_weight_size_usd": s3,
        "after_portfolio_heat_size_usd": s4,
        "after_dynamic_sizing_usd": round(float(after_dynamic_usd), 4),
        "after_combo_size_usd": round(float(after_combo_usd), 4),
        "after_policy_size_usd": round(float(post_policy_usd), 4),
        "final_size_usd": round(float(post_modifier_usd), 4),
    }


def exchange_qty_preview(
    *,
    post_notional_usd: float,
    entry_price: float,
    lot: dict[str, Any],
) -> dict[str, Any]:
    """Rounded qty / notional vs Binance-style LOT_SIZE (for logs; optional)."""
    from core.execution.binance_futures import _quantity_to_string

    ep = float(entry_price or 0)
    out: dict[str, Any] = {
        "min_qty": float(lot.get("minQty") or 0),
        "step_size": str(lot.get("stepSize") or "0.01"),
        "exchange_min_notional": float(lot.get("minNotional") or 0),
    }
    if ep <= 0:
        out["error"] = "invalid_entry_price"
        return out
    qty_raw = float(post_notional_usd) / ep
    qty_str = _quantity_to_string(qty_raw, lot)
    try:
        qty_f = float(qty_str)
    except (TypeError, ValueError):
        qty_f = 0.0
    notional = qty_f * ep
    flags: list[str] = []
    if qty_f <= 0:
        flags.append("ZERO_AFTER_STEP_ROUNDING")
    elif out["min_qty"] > 0 and qty_f + 1e-12 < out["min_qty"]:
        flags.append("BELOW_MIN_QTY")
    elif out["exchange_min_notional"] > 0 and notional + 1e-6 < out["exchange_min_notional"]:
        flags.append("BELOW_EXCHANGE_MIN_NOTIONAL")
    out["qty_raw"] = round(qty_raw, 8)
    out["rounded_qty"] = qty_f
    out["rounded_qty_str"] = qty_str
    out["rounded_notional_usd"] = round(notional, 4)
    out["exchange_flags"] = flags
    return out


def classify_post_sizing_reject(
    *,
    post_modifier_usd: float,
    pre_modifier_usd: float,
    post_policy_usd: float,
    internal_min_trade_usd: float,
    mod_breakdown: dict[str, Any] | None,
    exchange_preview: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """
    Single primary reason_code for post-sizing reject (replaces generic SIZE_TOO_SMALL_POST_SIZING).

    Priority when ``post_modifier_usd >= internal_min_trade_usd``: exchange lot constraints first.
    When below internal minimum: distinguish policy compression vs plain floor breach.
    """
    post = float(post_modifier_usd)
    internal = float(internal_min_trade_usd)
    pre = float(pre_modifier_usd)
    pol = float(post_policy_usd)
    detail: dict[str, Any] = {
        "required_min_usd": round(internal, 4),
        "final_size_usd": round(post, 4),
        "blocking_stage": "",
    }

    if exchange_preview and "error" not in exchange_preview:
        exf = list(exchange_preview.get("exchange_flags") or [])
        if post >= internal - 1e-9:
            if "ZERO_AFTER_STEP_ROUNDING" in exf:
                detail["blocking_stage"] = "exchange_lot_rounding"
                return "ZERO_AFTER_STEP_ROUNDING", detail
            if "BELOW_MIN_QTY" in exf:
                detail["blocking_stage"] = "exchange_min_qty"
                return "BELOW_MIN_QTY", detail
            if "BELOW_EXCHANGE_MIN_NOTIONAL" in exf:
                detail["blocking_stage"] = "exchange_min_notional"
                return "BELOW_EXCHANGE_MIN_NOTIONAL", detail

    # Callers normally only classify when post < internal; if not, avoid mislabeled codes.
    if post >= internal - 1e-9:
        detail["blocking_stage"] = "internal_min_satisfied_unexpected_classify"
        return "SIZING_CLASSIFY_UNEXPECTED_GE_MIN", detail

    policy_shrank = (pre >= internal * 0.95) and (pol < pre * 0.88)
    brain_active = bool(mod_breakdown) and len(mod_breakdown) > 0

    if (policy_shrank or brain_active) and pre >= internal * 0.95:
        detail["blocking_stage"] = "brain_v4_policy_and_or_bot_edge_after_viable_size"
        return "REDUCED_TOO_MUCH_BY_POLICY", detail

    detail["blocking_stage"] = "below_internal_min_trade_usd"
    return "BELOW_INTERNAL_MIN_TRADE_USD", detail


def diagnose_size_too_small(
    *,
    post_risk_engine_usd: float,
    size_after_vol: float,
    after_dynamic_usd: float,
    pre_policy_usd: float,
    post_policy_usd: float,
    post_modifier_usd: float,
    estimate_max_from_risk_usd: float,
    min_notional_usd: float,
    signal: StrategySignal,
    entry_combo_mult: float,
    confidence_mult: float,
    regime_score: float,
    strategy_weight: float,
    portfolio_heat_mult: float,
    bot_edge_mult: float,
    mod_breakdown: dict[str, Any] | None,
) -> dict[str, Any]:
    ep = float(signal.entry_price or 0)
    sl = signal.stop_loss
    stop_dist_pct = 0.0
    if ep > 0 and sl is not None:
        stop_dist_pct = abs(ep - float(sl)) / ep

    flags: list[str] = []
    primary = "UNKNOWN"

    buffer = 2.0
    if estimate_max_from_risk_usd < (min_notional_usd + buffer):
        flags.append("risk_model_max_below_min_notional")
        primary = "RISK_OR_STOP_TOO_WIDE_FOR_MIN_SIZE"
        if stop_dist_pct >= 0.04:
            flags.append("wide_stop_distance_pct")

    vol_shrink = size_after_vol < post_risk_engine_usd * 0.92 and size_after_vol < post_risk_engine_usd
    if vol_shrink and post_risk_engine_usd >= min_notional_usd:
        flags.append("volatility_guard_shrank_size")

    dyn_shrink = after_dynamic_usd < size_after_vol * 0.92
    if dyn_shrink:
        flags.append("dynamic_sizing_shrank")

    combo_shrink = entry_combo_mult < 0.999
    if combo_shrink:
        flags.append("entry_combo_multiplier_lt_1")

    pre_pol = float(pre_policy_usd)
    pol_shrink = post_policy_usd < pre_pol * 0.92
    if pol_shrink or (mod_breakdown and len(mod_breakdown) > 0):
        flags.append("brain_v4_policy_sizing")

    edge_shrink = bot_edge_mult < 0.999
    if edge_shrink:
        flags.append("bot_edge_risk_mult")

    chain = post_modifier_usd < min_notional_usd
    if chain and primary == "UNKNOWN":
        primary = "MULTIPLIERS_REDUCED_BELOW_MIN_NOTIONAL"
    if chain and estimate_max_from_risk_usd >= (min_notional_usd + buffer) and primary != "RISK_OR_STOP_TOO_WIDE_FOR_MIN_SIZE":
        if not flags:
            flags.append("multiplier_chain_only")
        primary = "MULTIPLIERS_REDUCED_BELOW_MIN_NOTIONAL"

    return {
        "primary_cause": primary,
        "flags": flags,
        "stop_distance_pct": round(stop_dist_pct, 6),
        "multipliers_applied": {
            "entry_combo_mult": round(float(entry_combo_mult), 4),
            "confidence_mult": round(float(confidence_mult), 4),
            "regime_score": round(float(regime_score), 4),
            "strategy_weight": round(float(strategy_weight), 4),
            "portfolio_heat_mult": round(float(portfolio_heat_mult), 4),
            "bot_edge_mult": round(float(bot_edge_mult), 4),
        },
        "usd_stages_vs_min": {
            "min_notional_usd": float(min_notional_usd),
            "estimate_max_from_risk_usd": round(float(estimate_max_from_risk_usd), 4),
            "post_risk_engine_usd": round(float(post_risk_engine_usd), 4),
            "post_all_modifiers_usd": round(float(post_modifier_usd), 4),
        },
    }
