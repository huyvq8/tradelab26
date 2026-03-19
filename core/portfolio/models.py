from datetime import datetime, date
from sqlalchemy import String, Float, Integer, DateTime, Date, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    capital_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    cash_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    positions: Mapped[list["Position"]] = relationship(back_populates="portfolio")
    trades: Mapped[list["Trade"]] = relationship(back_populates="portfolio")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    strategy_name: Mapped[str] = mapped_column(String(50))
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    hedge_of_position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    # Smart Scale-In (document/budget): so lan da add vao vi the; entry lan dau de so sanh rule gia.
    scale_in_count: Mapped[int] = mapped_column(Integer, default=0)
    initial_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    strategy_name: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fee_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    risk_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="trades")


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    equity_usd: Mapped[float] = mapped_column(Float)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
