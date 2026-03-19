from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.journal.models import JournalEntry
from core.portfolio.models import Trade


def repeated_mistakes(db: Session, top_n: int = 5) -> list[tuple[str, int]]:
    """From journal mistakes, return most repeated (text, count) for learning."""
    journals = list(db.scalars(select(JournalEntry).where(JournalEntry.mistakes.is_not(None))))
    pieces: list[str] = []
    for j in journals:
        if j.mistakes:
            for part in j.mistakes.replace(";", "\n").split("\n"):
                t = part.strip()
                if t:
                    pieces.append(t.lower())
    if not pieces:
        return []
    return Counter(pieces).most_common(top_n)


class ReflectionEngine:
    def build_daily_reflection(self, db: Session, target_date, portfolio_id: int | None = None) -> dict:
        journals = list(
            db.scalars(select(JournalEntry).where(JournalEntry.entry_date == target_date))
        )
        start_dt = datetime.combine(target_date, time.min)
        end_dt = start_dt + timedelta(days=1)
        q = select(Trade).where(
            Trade.created_at >= start_dt, Trade.created_at < end_dt
        )
        if portfolio_id is not None:
            q = q.where(Trade.portfolio_id == portfolio_id)
        trades = list(db.scalars(q))
        strategy_counts = Counter(j.strategy_name for j in journals)
        lessons = [j.lessons for j in journals if j.lessons]
        mistakes = [j.mistakes for j in journals if j.mistakes]
        close_trades = [t for t in trades if t.action == "close"]
        realized = round(sum(t.pnl_usd for t in close_trades), 2)
        repeated = repeated_mistakes(db, top_n=5)
        repeated_list = [{"text": t, "count": c} for t, c in repeated]

        # Metrics for learning (win rate, PF, expectancy, strategy accuracy)
        from core.analytics.metrics import compute_metrics, profit_by_exit_reason, tp_reach_analysis
        metrics = compute_metrics(db, portfolio_id=portfolio_id)
        profit_source = profit_by_exit_reason(db, portfolio_id=portfolio_id, last_n_days=30)
        tp_reach = tp_reach_analysis(db, portfolio_id=portfolio_id, last_n_days=30)
        win_rate = metrics.get("win_rate", 0.0)
        profit_factor = metrics.get("profit_factor", 0.0)
        expectancy_usd = metrics.get("expectancy_usd", 0.0)
        strategy_accuracy = metrics.get("strategy_accuracy", {})

        # Học từ dữ liệu cũ: đọc lịch sử đóng lệnh + journal mistakes để nhận diện pattern sai lầm
        try:
            from core.reflection.learn_from_history import learn_from_closed_trades
            learned = learn_from_closed_trades(db, portfolio_id=portfolio_id, last_n_days=30, min_trades_per_group=2)
        except Exception:
            learned = {"warnings": [], "by_group": [], "from_journal_mistakes": []}

        out = {
            "journal_count": len(journals),
            "realized_pnl": realized,
            "strategy_counts": dict(strategy_counts),
            "lessons": lessons,
            "mistakes": mistakes,
            "top_pattern": strategy_counts.most_common(1)[0][0] if strategy_counts else "none",
            "repeated_mistakes": repeated_list,
            "metrics": metrics,
            "profit_by_exit_reason": profit_source,
            "tp_reach_analysis": tp_reach,
            "learned_from_history": learned,
            "reflection_summary": {},
            "mistakes_found": [],
            "suggested_actions": [],
        }

        # AI reflection when OPENAI_API_KEY is set
        try:
            from core.reflection.ai_service import (
                daily_review_from_context,
                next_day_plan_from_context,
            )
            trades_text = "\n".join(
                f"- {t.symbol} {t.side} {t.strategy_name} | entry/exit ~{t.price} | PnL {t.pnl_usd} USD"
                for t in close_trades
            ) or "(no closed trades today)"
            journal_text = "\n---\n".join(
                f"Entry: {j.entry_reason or ''}\nLessons: {j.lessons or ''}\nMistakes: {j.mistakes or ''}"
                for j in journals
            ) or "(no journal entries today)"
            open_count = 0
            if portfolio_id is not None:
                from core.portfolio.models import Position
                open_count = len(list(db.scalars(
                    select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio_id)
                )))

            ai_summary = daily_review_from_context(
                target_date=str(target_date),
                realized_pnl=realized,
                total_trades=len(close_trades),
                win_rate=win_rate,
                profit_factor=profit_factor,
                expectancy_usd=expectancy_usd,
                trades_text=trades_text,
                journal_text=journal_text,
                repeated_mistakes=repeated_list,
            )
            if ai_summary:
                out["ai_summary"] = ai_summary
                ai_plan = next_day_plan_from_context(
                    reflection_summary=ai_summary,
                    realized_pnl=realized,
                    win_rate=win_rate,
                    strategy_accuracy=strategy_accuracy,
                    repeated_mistakes=repeated_list,
                    open_positions=open_count,
                )
                if ai_plan:
                    out["ai_next_day_plan"] = ai_plan
            # v4: structured reflection (suggested_actions for optimizer)
            try:
                from core.ai.reflection_agent import run_structured_reflection
                learned = out.get("learned_from_history") or {}
                warnings = learned.get("warnings") or []
                structured = run_structured_reflection(
                    target_date=str(target_date),
                    trades_text=trades_text,
                    journal_text=journal_text,
                    metrics=metrics,
                    learned_warnings=warnings,
                    repeated_mistakes=repeated_list,
                )
                if structured:
                    out["reflection_summary"] = structured.get("summary") or {}
                    out["mistakes_found"] = structured.get("mistakes_found") or []
                    out["suggested_actions"] = structured.get("suggested_actions") or []
            except Exception:
                out["reflection_summary"] = {}
                out["mistakes_found"] = []
                out["suggested_actions"] = []
        except Exception:
            if "reflection_summary" not in out:
                out["reflection_summary"] = {}
            if "mistakes_found" not in out:
                out["mistakes_found"] = []
            if "suggested_actions" not in out:
                out["suggested_actions"] = []

        # Phân tích TP không bao giờ chạm: có thể gọi OpenAI để có góc nhìn chuyên gia
        if out.get("tp_reach_analysis") and out["tp_reach_analysis"].get("count", 0) > 0:
            try:
                from core.reflection.ai_service import tp_reach_diagnosis_from_context
                ai_tp = tp_reach_diagnosis_from_context(out["tp_reach_analysis"])
                if ai_tp:
                    out["tp_reach_analysis"]["ai_diagnosis"] = ai_tp
            except Exception:
                pass

        # Tự động thêm suggested_action khi học được "đúng hướng nhưng lời ít" (vào muộn trong trend)
        learned = out.get("learned_from_history") or {}
        for w in learned.get("warnings") or []:
            if w.get("type") == "win_but_small_profit":
                actions = out.get("suggested_actions") or []
                if not any(a.get("type") == "improve_entry_timing" for a in actions if isinstance(a, dict)):
                    actions.append({
                        "type": "improve_entry_timing",
                        "reason": w.get("message", "Nhiều lệnh thắng nhưng lời rất ít."),
                        "suggestion": "Bật use_4h_trend_filter (chỉ vào long khi nến 4h tăng, short khi nến 4h giảm) hoặc cân nhắc điều kiện vào sớm hơn khi trend mới hình thành.",
                    })
                    out["suggested_actions"] = actions
                break

        return out
