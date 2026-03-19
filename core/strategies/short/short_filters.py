# Bo loc short: khong short neu HTF uptrend manh, chua break structure, entry qua xa invalidation
from __future__ import annotations


def apply_short_filters(
    symbol: str,
    setup_type: str,
    entry_price: float,
    stop_loss: float,
    current_price: float,
    htf_bullish: bool,
    htf_strong_bull: bool,
    has_structure_break: bool,
    config: dict | None = None,
) -> tuple[bool, str]:
    cfg = config or {}
    if htf_strong_bull:
        return (False, "HTF_uptrend_strong")
    if htf_bullish and not cfg.get("allow_short_vs_weak_htf_bull", False):
        return (False, "HTF_uptrend")
    if setup_type in ("pump_exhaustion", "bull_trap") and not has_structure_break:
        return (False, "no_structure_break")
    invalidation_distance_pct = abs(entry_price - stop_loss) / max(entry_price, 1e-9)
    max_inv_pct = float(cfg.get("max_invalidation_distance_pct", 0.05))
    if invalidation_distance_pct > max_inv_pct:
        return (False, "entry_too_far_from_invalidation")
    entry_distance_pct = abs(current_price - entry_price) / max(entry_price, 1e-9)
    if entry_distance_pct > 0.03:
        return (False, "price_too_far_from_entry")
    return (True, "")


def has_structure_break(metrics: dict, setup_type: str) -> bool:
    if setup_type == "pump_exhaustion":
        return "break_structure_down" in (metrics.get("reasons") or [])
    if setup_type == "bull_trap":
        return metrics.get("close_below", False)
    if setup_type == "trend_pullback":
        return metrics.get("break_down", False)
    return False
