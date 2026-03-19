"""Compute professional metrics from trades and snapshots."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Trade, Position, DailySnapshot


def _exit_reason_from_note(note: str | None) -> str:
    """Suy lý do đóng lệnh từ Trade.note (để phân tích nguồn lợi nhuận)."""
    if not note:
        return "unknown"
    n = (note or "").lower()
    if "sl " in n or "stop loss" in n or "stop_loss" in n:
        return "sl_hit"
    if "tp " in n or "take profit" in n or "take_profit" in n:
        return "tp_hit"
    if "đồng bộ" in n or "sync" in n or "binance" in n:
        return "sync_binance"
    if "đóng chủ động" in n or "proactive" in n:
        return "proactive"
    return "manual"


def compute_metrics(db: Session, portfolio_id: int | None = None) -> dict:
    """
    Compute win rate, profit factor, expectancy, max drawdown, total trades, total PnL,
    avg R-multiple, Sharpe (simulated), and per-strategy accuracy.
    If portfolio_id is None, aggregate all trades.
    """
    q = select(Trade).where(Trade.action == "close")
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    trades = list(db.scalars(q))
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_usd": 0.0,
            "total_pnl_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_r_multiple": 0.0,
            "sharpe_simulated": 0.0,
            "strategy_accuracy": {},
        }
    total_pnl = sum(t.pnl_usd for t in trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    win_rate = len(wins) / len(trades)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    expectancy = total_pnl / len(trades)

    # Avg R-multiple: R = pnl_usd / risk_usd for trades with risk_usd > 0
    r_multiples = [
        t.pnl_usd / t.risk_usd
        for t in trades
        if getattr(t, "risk_usd", None) is not None and t.risk_usd and t.risk_usd > 0
    ]
    avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    # Sharpe from daily returns (equity curve)
    qs = select(DailySnapshot).order_by(DailySnapshot.snapshot_date)
    if portfolio_id is not None:
        qs = qs.where(DailySnapshot.portfolio_id == portfolio_id)
    snapshots = list(db.scalars(qs))
    max_dd_pct = 0.0
    sharpe_simulated = 0.0
    if snapshots:
        peak = snapshots[0].equity_usd
        for s in snapshots:
            if s.equity_usd >= peak:
                peak = s.equity_usd
            dd = (peak - s.equity_usd) / peak * 100 if peak > 0 else 0
            max_dd_pct = max(max_dd_pct, dd)
        # Daily returns for Sharpe
        returns = []
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1].equity_usd
            curr = snapshots[i].equity_usd
            if prev and prev > 0:
                returns.append((curr - prev) / prev)
        if len(returns) >= 2:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            std_ret = math.sqrt(variance) if variance > 0 else 1e-10
            sharpe_simulated = (mean_ret / std_ret) * math.sqrt(252) if std_ret else 0.0

    # Per-strategy accuracy (win rate by strategy_name)
    strategy_wins: dict[str, int] = {}
    strategy_totals: dict[str, int] = {}
    for t in trades:
        s = t.strategy_name or "unknown"
        strategy_totals[s] = strategy_totals.get(s, 0) + 1
        if t.pnl_usd > 0:
            strategy_wins[s] = strategy_wins.get(s, 0) + 1
    strategy_accuracy = {
        s: {
            "win_rate": strategy_wins.get(s, 0) / strategy_totals[s],
            "trades": strategy_totals[s],
            "wins": strategy_wins.get(s, 0),
        }
        for s in strategy_totals
    }

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_usd": round(expectancy, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_r_multiple": round(avg_r_multiple, 4),
        "sharpe_simulated": round(sharpe_simulated, 4),
        "strategy_accuracy": strategy_accuracy,
    }


def profit_by_exit_reason(
    db: Session,
    portfolio_id: int | None = None,
    last_n_days: int | None = None,
) -> dict:
    """
    Phân tích lợi nhuận theo lý do đóng lệnh: cắt lãi (tp_hit), cắt lỗ (sl_hit),
    đóng chủ động (proactive), đồng bộ Binance (sync_binance), thủ công (manual).
    Trả về: total_pnl_usd, by_exit_reason (pnl_usd, count, count_win), summary_text.
    """
    q = select(Trade).where(Trade.action == "close")
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    if last_n_days is not None and last_n_days > 0:
        since = datetime.utcnow() - timedelta(days=last_n_days)
        q = q.where(Trade.created_at >= since)
    trades = list(db.scalars(q))
    if not trades:
        return {
            "total_pnl_usd": 0.0,
            "total_trades": 0,
            "by_exit_reason": {},
            "summary_text": "Chưa có lệnh đóng.",
        }

    by_reason: dict[str, dict] = {}
    for t in trades:
        reason = _exit_reason_from_note(t.note)
        pnl = float(t.pnl_usd or 0)
        if reason not in by_reason:
            by_reason[reason] = {"pnl_usd": 0.0, "count": 0, "count_win": 0}
        by_reason[reason]["pnl_usd"] += pnl
        by_reason[reason]["count"] += 1
        if pnl > 0:
            by_reason[reason]["count_win"] += 1

    total_pnl = sum(t.pnl_usd for t in trades)
    # Làm tròn từng nhóm
    for r, d in by_reason.items():
        d["pnl_usd"] = round(d["pnl_usd"], 2)

    # Tóm tắt: lợi nhuận đến từ đâu (phần lớn là cắt lãi TP, ăn non, hay chiến thuật đúng)
    labels = {
        "tp_hit": "Chốt lãi (TP)",
        "sl_hit": "Cắt lỗ (SL)",
        "proactive": "Đóng chủ động",
        "sync_binance": "Đồng bộ Binance",
        "manual": "Thủ công",
        "unknown": "Không rõ",
    }
    parts = []
    for reason in ["tp_hit", "proactive", "sync_binance", "manual", "sl_hit", "unknown"]:
        if reason not in by_reason:
            continue
        d = by_reason[reason]
        label = labels.get(reason, reason)
        pct = (d["pnl_usd"] / total_pnl * 100) if total_pnl != 0 else 0
        parts.append(f"{label}: {d['pnl_usd']:+.2f} USD ({d['count']} lệnh, {d['count_win']} thắng) — {pct:.0f}% tổng PnL")
    summary_text = " | ".join(parts) if parts else "Chưa có dữ liệu."

    return {
        "total_pnl_usd": round(total_pnl, 2),
        "total_trades": len(trades),
        "by_exit_reason": by_reason,
        "summary_text": summary_text,
    }


def tp_reach_analysis(
    db: Session,
    portfolio_id: int | None = None,
    last_n_days: int = 30,
) -> dict:
    """
    Phân tích: long đóng có lãi nhưng không đạt TP — nguyên nhân TP quá cao hay thiếu thời gian?
    Chỉ xét lệnh LONG đóng với pnl > 0 và exit_reason != tp_hit (đóng bởi SL kéo lên, proactive, sync...).
    Trả về: count, avg_hold_min, avg_tp_pct (TP so với entry), avg_actual_pct (lời thực tế), diagnosis, suggestion.
    """
    since = datetime.utcnow() - timedelta(days=last_n_days)
    q = (
        select(Trade, Position)
        .where(
            Trade.action == "close",
            Trade.side == "long",
            Trade.pnl_usd > 0,
            Trade.created_at >= since,
            Trade.position_id == Position.id,
        )
    )
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    rows = list(db.execute(q).all())
    # Chỉ lấy lệnh KHÔNG đóng do chạm TP (đóng do SL kéo lên, proactive, sync)
    out = []
    for close_trade, position in rows:
        reason = _exit_reason_from_note(close_trade.note)
        if reason == "tp_hit":
            continue
        entry = float(position.entry_price or 0)
        tp = position.take_profit
        if not entry or entry <= 0:
            continue
        exit_price = float(close_trade.price or 0)
        if not exit_price:
            continue
        tp_pct = (float(tp) - entry) / entry * 100 if tp and tp > 0 else (6.0)  # mặc định 6% nếu không lưu TP
        actual_pct = (exit_price - entry) / entry * 100
        opened_at = getattr(position, "opened_at", None) or getattr(close_trade, "created_at", None)
        if opened_at and close_trade.created_at:
            hold_min = (close_trade.created_at - opened_at).total_seconds() / 60.0
        else:
            hold_min = None
        out.append({
            "tp_pct": round(tp_pct, 2),
            "actual_pct": round(actual_pct, 2),
            "hold_min": round(hold_min, 1) if hold_min is not None else None,
        })
    if not out:
        return {
            "count": 0,
            "avg_hold_min": None,
            "avg_tp_pct": None,
            "avg_actual_pct": None,
            "diagnosis": "Chưa có lệnh long đóng có lãi (không phải TP) trong kỳ.",
            "suggestion": "",
        }
    avg_hold = sum(x["hold_min"] for x in out if x["hold_min"] is not None) / max(1, sum(1 for x in out if x["hold_min"] is not None))
    avg_tp = sum(x["tp_pct"] for x in out) / len(out)
    avg_actual = sum(x["actual_pct"] for x in out) / len(out)
    # Chuyên gia: so sánh TP% với actual%, và thời gian giữ
    # Nếu avg_tp >> avg_actual và avg_hold ngắn → thiếu thời gian (kéo SL lên sớm / đóng sớm)
    # Nếu avg_tp >> avg_actual và avg_hold dài → TP có thể quá cao so với biên độ thực tế
    diagnosis_parts = []
    if avg_tp > avg_actual * 1.5:
        if avg_hold < 120:  # < 2h
            diagnosis_parts.append("Thời gian giữ trung bình ngắn ({:.0f} phút). Lệnh thường đóng khi SL được kéo lên (bảo vệ lãi) trước khi giá kịp chạm TP.".format(avg_hold))
        else:
            diagnosis_parts.append("TP tại entry khá xa ({:.1f}% từ entry) trong khi lời thực tế trung bình chỉ {:.2f}%. Có thể TP đặt quá cao so với biên độ giá thực tế trong khung thời gian giữ.".format(avg_tp, avg_actual))
    else:
        diagnosis_parts.append("Khoảng cách TP và lời thực tế tương đối gần — có thể cần thêm thời gian hoặc điều chỉnh nhẹ TP.")
    suggestion_parts = []
    if avg_hold < 120 and avg_tp > avg_actual * 1.5:
        suggestion_parts.append("(1) Cân nhắc kéo SL lên breakeven/lock profit muộn hơn (tăng lock_profit_min_usd hoặc chỉ kéo SL khi giá tiến gần TP hơn). (2) Hoặc giảm TP xuống gần thực tế (vd. dùng learned_max_tp_pct, hoặc cap TP theo ATR).")
    if avg_hold >= 120 and avg_tp > avg_actual * 1.5:
        suggestion_parts.append("(1) Hạ TP gần hơn (vd. strategy 4–5% thay vì 6%, hoặc bật max_tp_pct_above_current cho day trade). (2) Hoặc đặt TP theo ATR/cấu trúc (đã có trong suggest_sl_tp_update) để TP phù hợp biến động.")

    return {
        "count": len(out),
        "avg_hold_min": round(avg_hold, 1),
        "avg_tp_pct": round(avg_tp, 2),
        "avg_actual_pct": round(avg_actual, 2),
        "diagnosis": " ".join(diagnosis_parts) if diagnosis_parts else "",
        "suggestion": " ".join(suggestion_parts) if suggestion_parts else "",
    }
