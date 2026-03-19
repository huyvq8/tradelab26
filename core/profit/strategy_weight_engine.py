"""
Phase 3 v6: Strategy weight engine — weight 0.25–1.5 từ rolling PF, win rate.
Dùng để nhân vào position size: strategy tốt -> size lớn hơn, strategy yếu -> size nhỏ hơn.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Trade


def compute_strategy_weights(
    db: Session,
    portfolio_id: int | None = None,
    lookback_days: int = 30,
    min_sample: int = 5,
    weight_min: float = 0.25,
    weight_max: float = 1.5,
) -> dict[str, float]:
    """
    Với mỗi strategy: lấy rolling lệnh đóng (lookback_days), tính win_rate và profit_factor.
    weight = weight_min + (win_rate * 0.4 + min(PF, 2) / 2 * 0.6) * (weight_max - weight_min), clamp.
    Nếu sample < min_sample -> weight = 1.0 (trung tính).
    """
    since = datetime.utcnow() - timedelta(days=lookback_days)
    q = select(Trade).where(
        Trade.action == "close",
        Trade.created_at >= since,
    )
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    trades = list(db.scalars(q))
    if not trades:
        return {}

    by_strategy: dict[str, list[float]] = {}
    for t in trades:
        s = (t.strategy_name or "unknown").strip() or "unknown"
        pnl = float(t.pnl_usd or 0)
        if s not in by_strategy:
            by_strategy[s] = []
        by_strategy[s].append(pnl)

    out = {}
    for strategy, pnls in by_strategy.items():
        if len(pnls) < min_sample:
            out[strategy] = 1.0
            continue
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
        pf_capped = min(pf, 2.0)
        raw = win_rate * 0.4 + (pf_capped / 2.0) * 0.6
        weight = weight_min + raw * (weight_max - weight_min)
        weight = max(weight_min, min(weight_max, weight))
        out[strategy] = round(weight, 2)
    return out


def get_strategy_weight(weights: dict[str, float], strategy_name: str) -> float:
    """Lấy weight cho strategy; không có thì 1.0."""
    return float(weights.get((strategy_name or "").strip() or "unknown", 1.0))
