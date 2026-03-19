# Unwind hedge: close when pullback done (price reclaims structure) or timeout
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from core.portfolio.models import Position


def _to_ohlcv(c: Any) -> tuple[float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None)
    lo = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None)
    return (float(o or 0), float(h or 0), float(lo or 0), float(cl or 0))


def _ema(closes: list[float], period: int) -> float | None:
    if not closes or len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
    return ema


def should_unwind_hedge(
    hedge_position: Position,
    main_position: Position,
    current_price: float,
    klines_1h: list,
    config: dict | None = None,
    bars_per_hour: float = 1.0,
) -> tuple[bool, str]:
    """
    Return (should_close_hedge, reason).
    Unwind when: timeout (max_hedge_duration_bars) or pullback_done (price reclaimed EMA/structure).
    """
    cfg = config or {}
    max_bars = int(cfg.get("max_hedge_duration_bars", 24))
    unwind_on_timeout = cfg.get("unwind_on_timeout", True)
    unwind_on_pullback_done = cfg.get("unwind_on_pullback_done", True)

    opened_at = getattr(hedge_position, "opened_at", None)
    if opened_at and unwind_on_timeout and max_bars > 0:
        now = datetime.utcnow()
        if hasattr(now, "timestamp") and hasattr(opened_at, "timestamp"):
            hours_held = (now.timestamp() - opened_at.timestamp()) / 3600.0
            bars_held = hours_held * bars_per_hour
            if bars_held >= max_bars:
                return (True, "HEDGE_TIMEOUT_EXIT")

    if unwind_on_pullback_done and klines_1h and len(klines_1h) >= 10:
        closes = [_to_ohlcv(c)[3] for c in klines_1h]
        ema = _ema(closes, 9)
        if ema is not None:
            last_close = closes[-1]
            if main_position.side == "long":
                if last_close >= ema * 1.001:
                    return (True, "HEDGE_UNWIND_PULLBACK_DONE_EMA_RECLAIM")
            else:
                if last_close <= ema * 0.999:
                    return (True, "HEDGE_UNWIND_PULLBACK_DONE_EMA_RECLAIM")

    return (False, "")


def get_hedge_positions_for_main(db: Session, main_position_id: int):
    """Return open positions that are hedges of this main position."""
    return list(db.scalars(
        select(Position).where(
            Position.hedge_of_position_id == main_position_id,
            Position.is_open == True,
        )
    ))
