from datetime import datetime, date
from sqlalchemy import Integer, String, Float, DateTime, Date, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class JournalEntry(Base):
    """
    Full trade journal for learning loop (v4).
    - reasons + market_context + risk_score → "vì sao vừa vào lệnh"
    - exit_reason + mistake_tags → "vì sao vừa thua"
    """
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)  # long | short
    strategy_name: Mapped[str] = mapped_column(String(50))
    regime: Mapped[str] = mapped_column(String(50), default="unknown")
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 5m, 1h, etc.
    setup_score: Mapped[float] = mapped_column(Float, default=0.0)
    entry_reason: Mapped[str] = mapped_column(Text)
    risk_plan: Mapped[str] = mapped_column(Text)
    # v4: structured entry context for AI learning
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of strings
    market_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1 risk metric
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
    mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # v4: structured exit for "vì sao thua"
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)  # tp_hit, sl_hit, sync_binance, proactive, manual
    mistake_tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of tags
    # Audit / dashboard: lý do chọn TP/SL (công thức, RR, strategy) để hiển thị "why TP was chosen"
    tp_sl_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v6 short: setup_type (pump_exhaustion, bull_trap, trend_pullback)
    setup_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # v6 hedge: reason + ratio when entry is a hedge
    hedge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hedge_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    liquidity_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    volatility_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    manipulation_risk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    was_strategy_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    short_allowed_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hedge_allowed_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    capital_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
