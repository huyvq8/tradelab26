# Open hedge position (opposite side, size from hedge_sizer), link to main via hedge_of_position_id
from __future__ import annotations

from sqlalchemy.orm import Session

from core.execution import get_execution_backend
from core.portfolio.models import Position
from core.strategies.base import StrategySignal


def open_hedge_position(
    db: Session,
    portfolio_id: int,
    main_position: Position,
    hedge_size_usd: float,
    current_price: float,
    hedge_reason: str = "protective",
) -> Position | None:
    """
    Mở vị thế hedge (ngược chiều main), size = hedge_size_usd.
    Position mới có strategy_name="hedge", hedge_of_position_id=main_position.id.
    """
    if hedge_size_usd < 25:
        return None
    opposite_side = "short" if main_position.side == "long" else "long"
    sl_buffer_pct = 0.02
    if main_position.side == "long":
        sl = current_price * (1 + sl_buffer_pct)
        tp = current_price * (1 - 0.015)
    else:
        sl = current_price * (1 - sl_buffer_pct)
        tp = current_price * (1 + 0.015)
    signal = StrategySignal(
        symbol=main_position.symbol,
        strategy_name="hedge",
        side=opposite_side,
        confidence=0.5,
        entry_price=current_price,
        stop_loss=sl,
        take_profit=tp,
        rationale=f"Hedge: {hedge_reason} (main pos_id={main_position.id})",
        regime="",
    )
    executor = get_execution_backend()
    position = executor.open_position(db, portfolio_id, signal, hedge_size_usd)
    if position and getattr(position, "id", None):
        position.strategy_name = "hedge"
        position.hedge_of_position_id = main_position.id
        db.flush()
    return position
