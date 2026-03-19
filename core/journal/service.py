from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.journal.models import JournalEntry
from core.journal.context_builder import (
    build_tp_sl_explanation,
    serialize_reasons,
    serialize_market_context,
    serialize_mistake_tags,
)
from core.portfolio.models import Position, Trade
from core.strategies.base import StrategySignal


def _infer_exit_reason(note: str | None) -> str:
    """Từ note đóng lệnh suy ra lý do (sl_hit / tp_hit / sync_binance / proactive / manual)."""
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


class JournalService:
    def create_entry(
        self,
        db: Session,
        signal: StrategySignal,
        risk_reason: str,
        setup_score: float,
        trade_id: int | None = None,
        *,
        side: str | None = None,
        reasons: list[str] | None = None,
        market_context: dict | None = None,
        risk_score: float | None = None,
        timeframe: str | None = None,
        tp_sl_explanation: str | None = None,
        setup_type: str | None = None,
        hedge_reason: str | None = None,
        hedge_ratio: float | None = None,
        token_type: str | None = None,
        liquidity_tier: str | None = None,
        volatility_tier: str | None = None,
        manipulation_risk: str | None = None,
        was_strategy_allowed: bool | None = None,
        short_allowed_flag: bool | None = None,
        hedge_allowed_flag: bool | None = None,
    ):
        """Create journal entry with optional v4 context, v6 setup/hedge, token intelligence fields."""
        if tp_sl_explanation is None:
            tp_sl_explanation = build_tp_sl_explanation(signal)
        entry = JournalEntry(
            trade_id=trade_id,
            entry_date=date.today(),
            symbol=signal.symbol,
            side=side or signal.side,
            strategy_name=signal.strategy_name,
            regime=signal.regime,
            timeframe=timeframe,
            setup_score=setup_score,
            entry_reason=signal.rationale,
            risk_plan=risk_reason,
            reasons=serialize_reasons(reasons) if reasons else None,
            market_context=serialize_market_context(market_context) if market_context else None,
            risk_score=risk_score,
            tp_sl_explanation=tp_sl_explanation,
            setup_type=setup_type,
            hedge_reason=hedge_reason,
            hedge_ratio=hedge_ratio,
            token_type=token_type,
            liquidity_tier=liquidity_tier,
            volatility_tier=volatility_tier,
            manipulation_risk=manipulation_risk,
            was_strategy_allowed=was_strategy_allowed,
            short_allowed_flag=short_allowed_flag,
            hedge_allowed_flag=hedge_allowed_flag,
        )
        db.add(entry)
        db.flush()
        return entry

    def add_outcome(
        self,
        db: Session,
        journal_id: int,
        result_summary: str,
        lessons: str,
        mistakes: str = "",
        result_r: float | None = None,
        mfe_pct: float | None = None,
        mae_pct: float | None = None,
        exit_reason: str | None = None,
        mistake_tags: list[str] | None = None,
    ):
        entry = db.get(JournalEntry, journal_id)
        if not entry:
            return None
        entry.result_summary = result_summary
        entry.lessons = lessons
        entry.mistakes = mistakes
        if result_r is not None:
            entry.result_r = result_r
        if mfe_pct is not None:
            entry.mfe_pct = mfe_pct
        if mae_pct is not None:
            entry.mae_pct = mae_pct
        if exit_reason is not None:
            entry.exit_reason = exit_reason
        if mistake_tags is not None:
            entry.mistake_tags = serialize_mistake_tags(mistake_tags)
        db.flush()
        return entry

    def record_outcome_from_close(
        self,
        db: Session,
        position: Position,
        close_trade: Trade,
    ) -> JournalEntry | None:
        """
        Tự ghi kết quả và nhận diện sai lầm khi một lệnh đóng (từ dữ liệu thật).
        Tìm journal entry tương ứng (qua open Trade), điền result_summary, lessons, mistakes
        để reflection/đọc dữ liệu cũ có thể nhận ra pattern (vd. SL quá nhanh, lỗ lặp lại).
        """
        open_trade = db.scalar(
            select(Trade).where(
                Trade.position_id == position.id,
                Trade.action == "open",
            )
        )
        if not open_trade:
            return None
        journal = db.scalar(select(JournalEntry).where(JournalEntry.trade_id == open_trade.id))
        if not journal or journal.result_summary:
            return None  # đã ghi rồi thì bỏ qua
        exit_reason = _infer_exit_reason(close_trade.note)
        pnl = float(close_trade.pnl_usd or 0)
        risk_usd = float(close_trade.risk_usd or 0) if close_trade.risk_usd is not None else None
        result_r = (pnl / risk_usd) if risk_usd and risk_usd > 0 else None
        opened_at = getattr(position, "opened_at", None) or getattr(open_trade, "created_at", None)
        if opened_at and close_trade.created_at and hasattr(close_trade.created_at, "__sub__"):
            try:
                hold_sec = (close_trade.created_at - opened_at).total_seconds()
            except Exception:
                hold_sec = 0
        else:
            hold_sec = 0
        hold_min = hold_sec / 60.0

        result_summary = (
            f"Đóng bởi {exit_reason} | PnL {pnl:.2f} USD | "
            f"giữ {hold_min:.0f} phút"
            + (f" | R={result_r:.2f}" if result_r is not None else "")
        )
        lessons_list: list[str] = []
        mistakes_list: list[str] = []
        mistake_tags_list: list[str] = []  # v4 structured tags for "vì sao thua"

        if exit_reason == "sl_hit":
            if hold_min < 5:
                mistakes_list.append("sl hit in under 5 min")
                mistake_tags_list.append("sl_very_fast")
                lessons_list.append("SL kích hoạt rất nhanh; cân nhắc nới SL hoặc vào lệnh sau confirmation.")
            elif hold_min < 15:
                mistakes_list.append("sl hit in under 15 min")
                mistake_tags_list.append("sl_fast")
            if pnl < 0 and risk_usd and result_r is not None and result_r < -1.5:
                mistakes_list.append("loss larger than 1.5r")
                mistake_tags_list.append("loss_larger_than_1_5r")
        elif exit_reason == "tp_hit":
            if pnl > 0:
                lessons_list.append("TP đạt; giữ kỷ luật cắt lỗ/chốt lời.")
        if pnl < 0 and hold_min < 10:
            mistakes_list.append("loss after short hold")
            mistake_tags_list.append("loss_short_hold")
        if pnl < 0:
            lessons_list.append("Lệnh lỗ; xem lại entry và SL/TP.")

        return self.add_outcome(
            db,
            journal.id,
            result_summary=result_summary,
            lessons=" | ".join(lessons_list) if lessons_list else "Xem lại setup và risk.",
            mistakes="; ".join(mistakes_list) if mistakes_list else ("sl hit" if exit_reason == "sl_hit" else ""),
            result_r=result_r,
            exit_reason=exit_reason,
            mistake_tags=mistake_tags_list if mistake_tags_list else None,
        )
