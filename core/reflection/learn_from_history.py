"""
Học từ dữ liệu cũ: đọc lịch sử Trade + Journal để nhận diện pattern sai lầm (SL quá nhanh, combo strategy+symbol thua nhiều).
Dùng cho reflection, báo cáo và (sau này) có thể điều chỉnh vào lệnh (vd. tạm tránh combo đang thua liên tục).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.journal.models import JournalEntry
from core.portfolio.models import Trade, Position


def _hold_minutes(open_trade: Trade, close_trade: Trade) -> float | None:
    if open_trade.created_at and close_trade.created_at:
        try:
            delta = close_trade.created_at - open_trade.created_at
            return delta.total_seconds() / 60.0
        except Exception:
            pass
    return None


def _exit_reason_from_note(note: str | None) -> str:
    if not note:
        return "unknown"
    n = (note or "").lower()
    if "sl " in n or "stop loss" in n or "stop_loss" in n:
        return "SL"
    if "tp " in n or "take profit" in n or "take_profit" in n:
        return "TP"
    if "đồng bộ" in n or "sync" in n:
        return "sync"
    if "đóng chủ động" in n or "proactive" in n:
        return "proactive"
    return "manual"


def learn_from_closed_trades(
    db: Session,
    portfolio_id: int | None = None,
    last_n_days: int = 30,
    min_trades_per_group: int = 2,
) -> dict:
    """
    Đọc tất cả lệnh đóng trong last_n_days, nhóm theo (strategy_name, symbol, side).
    Trả về: nhóm nào có win rate thấp, % SL rất nhanh (<5 phút), và gợi ý 'cảnh báo'.
    """
    since = datetime.utcnow() - timedelta(days=last_n_days)
    q = (
        select(Trade, Position)
        .where(
            Trade.action == "close",
            Trade.created_at >= since,
            Trade.position_id == Position.id,
        )
    )
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    rows = list(db.execute(q).all())

    # Lấy open trade để tính thời gian giữ
    open_trades_by_pos: dict[int, Trade] = {}
    for pos_id in {r[1].id for r in rows}:
        ot = db.scalar(
            select(Trade).where(
                Trade.position_id == pos_id,
                Trade.action == "open",
            )
        )
        if ot:
            open_trades_by_pos[pos_id] = ot

    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for close_trade, position in rows:
        key = (close_trade.strategy_name or "", close_trade.symbol or "", close_trade.side or "")
        open_trade = open_trades_by_pos.get(position.id) if position.id else None
        hold_min = _hold_minutes(open_trade, close_trade) if open_trade else None
        reason = _exit_reason_from_note(close_trade.note)
        groups[key].append({
            "pnl": float(close_trade.pnl_usd or 0),
            "hold_min": hold_min,
            "exit_reason": reason,
            "risk_usd": float(close_trade.risk_usd or 0) if close_trade.risk_usd else None,
        })

    summary = []
    warnings = []
    for (strategy, symbol, side), items in groups.items():
        if len(items) < min_trades_per_group:
            continue
        n = len(items)
        wins = sum(1 for x in items if x["pnl"] > 0)
        win_rate = wins / n
        total_pnl = sum(x["pnl"] for x in items)
        hold_mins = [x["hold_min"] for x in items if x["hold_min"] is not None]
        avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else None
        sl_fast_5 = sum(1 for x in items if x["exit_reason"] == "SL" and x["hold_min"] is not None and x["hold_min"] < 5)
        sl_fast_15 = sum(1 for x in items if x["exit_reason"] == "SL" and x["hold_min"] is not None and x["hold_min"] < 15)
        pct_sl_under_5 = sl_fast_5 / n if n else 0
        pct_sl_under_15 = sl_fast_15 / n if n else 0

        summary.append({
            "strategy": strategy,
            "symbol": symbol,
            "side": side,
            "trades": n,
            "win_rate": round(win_rate, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "avg_hold_min": round(avg_hold, 1) if avg_hold is not None else None,
            "pct_sl_under_5min": round(pct_sl_under_5, 2),
            "pct_sl_under_15min": round(pct_sl_under_15, 2),
        })
        if win_rate < 0.4:
            warnings.append({
                "type": "low_win_rate",
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "win_rate": win_rate,
                "trades": n,
                "message": f"{strategy} + {symbol} {side}: win rate {win_rate:.0%} ({n} lệnh) — cân nhắc nới SL hoặc tạm tránh combo này.",
            })
        if pct_sl_under_5 >= 0.5:
            warnings.append({
                "type": "sl_very_fast",
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "pct_sl_under_5min": pct_sl_under_5,
                "trades": n,
                "message": f"{strategy} + {symbol} {side}: {pct_sl_under_5:.0%} lệnh chạm SL trong <5 phút — SL có thể quá sát.",
            })
        # Đúng hướng nhưng lời rất ít — có thể vào muộn trong trend (entry gần đỉnh/đáy)
        wins_small = [x for x in items if x["pnl"] > 0 and x["pnl"] < 2.0]
        if wins >= 2 and len(wins_small) >= 2 and len(wins_small) >= 0.5 * wins:
            avg_small = sum(x["pnl"] for x in wins_small) / len(wins_small)
            warnings.append({
                "type": "win_but_small_profit",
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "trades": len(wins_small),
                "avg_win_usd": round(avg_small, 2),
                "message": f"{strategy} + {symbol} {side}: {len(wins_small)} lệnh thắng nhưng lời rất ít (trung bình ~{avg_small:.2f} USD) — có thể vào muộn trong trend.",
            })

    return {
        "by_group": summary,
        "warnings": warnings,
        "from_journal_mistakes": repeated_mistakes_from_journal(db, top_n=10),
    }


def repeated_mistakes_from_journal(db: Session, top_n: int = 10) -> list[dict]:
    """Đọc journal entries đã có mistakes, gộp theo cụm từ (để hệ thống 'tự nhận ra' sai lầm lặp lại)."""
    from core.reflection.engine import repeated_mistakes
    raw = repeated_mistakes(db, top_n=top_n)
    return [{"text": text, "count": count} for text, count in raw]
