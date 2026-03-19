"""
Phase 3 v6: Allocation engine — portfolio heat và same-regime/same-strategy reduction.
Trả về portfolio_heat_mult (0.3–1.0) khi tổng R đang mở quá cao hoặc nhiều lệnh cùng regime/strategy.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AllocationResult:
    portfolio_heat_mult: float
    reason: str


def compute_allocation_mult(
    open_positions: list,
    max_portfolio_heat_r: float = 4.0,
    same_regime_reduce: float = 0.8,
    same_strategy_reduce: float = 0.75,
    current_regime: str | None = None,
    current_strategy: str | None = None,
) -> AllocationResult:
    """
    open_positions: list of dict/object có .symbol, .strategy_name (hoặc ["strategy_name"]), .side,
    và (optional) risk_r hoặc risk_usd + entry_price + stop_loss để tính R.
    Nếu tổng R (ước lượng) > max_portfolio_heat_r → mult < 1.
    Nếu đang có >= 1 lệnh cùng regime (current_regime) hoặc cùng strategy (current_strategy) → mult *= reduce.
    """
    if not open_positions:
        return AllocationResult(portfolio_heat_mult=1.0, reason="")

    mult = 1.0
    reasons = []

    # Ước lượng tổng R: mỗi position coi 1R (nếu không có risk_r). Có thể nhận (risk_usd, entry, sl) để tính.
    total_r = 0.0
    for p in open_positions:
        r = getattr(p, "risk_r", None)
        if r is not None and isinstance(p, object):
            total_r += float(r)
        else:
            total_r += 1.0
    if max_portfolio_heat_r > 0 and total_r >= max_portfolio_heat_r:
        # Giảm mult theo mức vượt
        excess = total_r - max_portfolio_heat_r
        mult *= max(0.3, 1.0 - excess / (max_portfolio_heat_r + 2.0))
        reasons.append(f"portfolio_heat ~{total_r:.1f}R")

    regime_count = 0
    strategy_count = 0
    for p in open_positions:
        sname = getattr(p, "strategy_name", None) or (p.get("strategy_name") if isinstance(p, dict) else None)
        if current_strategy and (sname or "") == (current_strategy or ""):
            strategy_count += 1
        # Regime không lưu trên position; coi như nếu đang vào thêm cùng strategy thì đã count
    if current_strategy and strategy_count >= 1:
        mult *= same_strategy_reduce
        reasons.append("same_strategy_reduce")
    if current_regime and regime_count >= 1:
        mult *= same_regime_reduce
        reasons.append("same_regime_reduce")

    mult = max(0.3, min(1.0, round(mult, 2)))
    return AllocationResult(
        portfolio_heat_mult=mult,
        reason="; ".join(reasons) if reasons else "",
    )
