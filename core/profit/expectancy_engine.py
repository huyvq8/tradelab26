"""
Phase 3 v6: Expectancy engine — expectancy R theo (strategy, regime, side) từ lệnh đóng + Journal.
Dùng cho strategy weight và (sau này) block combo âm expectancy.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.journal.models import JournalEntry
from core.portfolio.models import Trade
from core.risk.trade_r_metrics import trade_close_has_valid_risk


def compute_expectancy_map(
    db: Session,
    portfolio_id: int | None = None,
    last_n_days: int = 90,
    min_sample: int = 2,
) -> dict[tuple[str, str, str], dict]:
    """
    Nhóm lệnh đóng theo (strategy_name, regime, side); với mỗi nhóm tính expectancy_r, sample, win_rate.
    Regime lấy từ Journal (trade_id); nếu không có journal thì regime = "unknown".
    R = pnl_usd / risk_usd khi risk_usd > 0.
    Trả về: {(strategy, regime, side): {expectancy_r, sample, win_rate, total_pnl}, ...}
    """
    since = datetime.utcnow() - timedelta(days=last_n_days)
    q = select(Trade).where(
        Trade.action == "close",
        Trade.created_at >= since,
    )
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    trades = list(db.scalars(q))
    if not trades:
        return {}

    trade_ids = [t.id for t in trades]
    journal_by_trade: dict[int, JournalEntry] = {}
    jq = select(JournalEntry).where(JournalEntry.trade_id.in_(trade_ids))
    for je in db.scalars(jq):
        if je.trade_id:
            journal_by_trade[je.trade_id] = je

    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for t in trades:
        regime = "unknown"
        if t.id in journal_by_trade:
            je = journal_by_trade[t.id]
            regime = (je.regime or "unknown").strip() or "unknown"
        strategy = (t.strategy_name or "unknown").strip() or "unknown"
        side = (t.side or "long").strip().lower() or "long"
        pnl = float(t.pnl_usd or 0)
        r_usd = float(t.risk_usd) if trade_close_has_valid_risk(t) else None
        if trade_close_has_valid_risk(t):
            r_mult = float(t.realized_r_multiple) if t.realized_r_multiple is not None else (pnl / float(t.risk_usd))
        else:
            r_mult = None
        key = (strategy, regime, side)
        groups[key].append({
            "pnl": pnl,
            "r_mult": r_mult,
            "risk_usd": r_usd,
        })

    out = {}
    for key, items in groups.items():
        if len(items) < min_sample:
            continue
        r_vals = [x["r_mult"] for x in items if x["r_mult"] is not None]
        if not r_vals:
            continue
        wins = sum(1 for x in items if x["pnl"] > 0)
        total_pnl = sum(x["pnl"] for x in items)
        out[key] = {
            "expectancy_r": round(sum(r_vals) / len(r_vals), 4),
            "sample": len(items),
            "win_rate": round(wins / len(items), 4),
            "total_pnl_usd": round(total_pnl, 2),
        }
    return out


def get_expectancy_for_signal(
    expectancy_map: dict,
    strategy_name: str,
    regime: str,
    side: str,
) -> float | None:
    """
    Lấy expectancy R cho combo (strategy, regime, side). Fallback: (strategy, "unknown", side), rồi (strategy, *, side).
    """
    key = (strategy_name.strip(), regime.strip(), side.strip().lower())
    if key in expectancy_map:
        return expectancy_map[key].get("expectancy_r")
    key2 = (strategy_name.strip(), "unknown", side.strip().lower())
    if key2 in expectancy_map:
        return expectancy_map[key2].get("expectancy_r")
    for (s, r, sd), data in expectancy_map.items():
        if s == strategy_name.strip() and sd == side.strip().lower():
            return data.get("expectancy_r")
    return None
