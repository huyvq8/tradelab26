from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from sqlalchemy import select

from core.config import settings
from core.portfolio.models import Portfolio, Position, Trade
from core.portfolio.capital_split import normalize_bucket
from core.profit.thesis_profiles import apply_thesis_fields_to_position
from core.risk.daily_r import MIN_RISK_USD_FOR_R_AGGREGATION
from core.risk.trade_r_metrics import attach_open_trade_risk_fields, infer_close_source_from_note
from core.strategies.base import StrategySignal


class PaperExecutionSimulator:
    def open_position(
        self,
        db: Session,
        portfolio_id: int,
        signal: StrategySignal,
        size_usd: float,
    ) -> Position:
        entry_price = (
            signal.entry_price * (1 + settings.sim_slippage_bps / 10_000)
            if signal.side == "long"
            else signal.entry_price * (1 - settings.sim_slippage_bps / 10_000)
        )
        quantity = size_usd / entry_price
        bucket = normalize_bucket(getattr(signal, "capital_bucket", None))
        position = Position(
            portfolio_id=portfolio_id,
            symbol=signal.symbol,
            side=signal.side,
            strategy_name=signal.strategy_name,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=signal.stop_loss,
            initial_stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            opened_at=datetime.utcnow(),
            is_open=True,
            entry_regime=(getattr(signal, "regime", None) or None),
            capital_bucket=bucket,
        )
        apply_thesis_fields_to_position(position, signal)
        db.add(position)
        db.flush()
        fee = size_usd * settings.sim_fee_bps / 10_000
        trade = Trade(
            portfolio_id=portfolio_id,
            position_id=position.id,
            symbol=signal.symbol,
            side=signal.side,
            strategy_name=signal.strategy_name,
            action="open",
            price=entry_price,
            quantity=quantity,
            fee_usd=fee,
            pnl_usd=0.0,
            note=signal.rationale,
            capital_bucket=bucket,
        )
        db.add(trade)
        db.flush()
        attach_open_trade_risk_fields(
            trade,
            entry_price=entry_price,
            quantity=quantity,
            signal=signal,
        )
        db.flush()
        return position

    def close_position(
        self,
        db: Session,
        position: Position,
        exit_price: float,
        note: str = "",
        *,
        close_source: str | None = None,
    ) -> Trade:
        direction = 1 if position.side == "long" else -1
        gross_pnl = (
            (exit_price - position.entry_price)
            * position.quantity
            * direction
        )
        exit_fee = (
            (exit_price * position.quantity)
            * settings.sim_fee_bps
            / 10_000
        )
        open_trade = db.scalar(
            select(Trade).where(
                Trade.position_id == position.id,
                Trade.action == "open",
            )
        )
        open_fee = open_trade.fee_usd if open_trade else 0.0
        pnl_net = gross_pnl - exit_fee - open_fee

        position.is_open = False
        position.closed_at = datetime.utcnow()

        portfolio = db.scalar(select(Portfolio).where(Portfolio.id == position.portfolio_id))
        if portfolio:
            if position.side == "long":
                portfolio.cash_usd += exit_price * position.quantity
            else:
                portfolio.cash_usd += (position.entry_price - exit_price) * position.quantity

        risk_usd = None
        sl_for_r = getattr(position, "initial_stop_loss", None)
        if sl_for_r is None:
            sl_for_r = position.stop_loss
        if sl_for_r is not None:
            risk_usd = abs(position.entry_price - sl_for_r) * position.quantity
        if risk_usd is not None and float(risk_usd) < MIN_RISK_USD_FOR_R_AGGREGATION:
            risk_usd = None
        cs = (close_source or "").strip() or infer_close_source_from_note(note)
        realized_r = None
        if risk_usd is not None and float(risk_usd) > 0:
            realized_r = round(float(pnl_net) / float(risk_usd), 4)
        bclose = normalize_bucket(getattr(position, "capital_bucket", None))
        trade = Trade(
            portfolio_id=position.portfolio_id,
            position_id=position.id,
            symbol=position.symbol,
            side=position.side,
            strategy_name=position.strategy_name,
            action="close",
            price=exit_price,
            quantity=position.quantity,
            fee_usd=exit_fee,
            pnl_usd=round(pnl_net, 4),
            risk_usd=round(risk_usd, 4) if risk_usd is not None else None,
            close_source=cs,
            realized_r_multiple=realized_r,
            note=note,
            capital_bucket=bclose,
        )
        db.add(trade)
        db.flush()
        return trade

    def reduce_position(
        self,
        db: Session,
        position: Position,
        reduce_quantity: float,
        exit_price: float,
        note: str = "",
    ) -> Trade | None:
        """Chốt một phần vị thế (partial TP). Cập nhật position.quantity, ghi Trade action='partial_close'."""
        if reduce_quantity <= 0 or reduce_quantity >= position.quantity:
            return None
        direction = 1 if position.side == "long" else -1
        gross_pnl = (exit_price - position.entry_price) * reduce_quantity * direction
        exit_fee = (exit_price * reduce_quantity) * settings.sim_fee_bps / 10_000
        open_trade = db.scalar(
            select(Trade).where(
                Trade.position_id == position.id,
                Trade.action == "open",
            )
        )
        open_fee_per_unit = (open_trade.fee_usd / open_trade.quantity) if (open_trade and open_trade.quantity) else 0.0
        pnl_net = gross_pnl - exit_fee - open_fee_per_unit * reduce_quantity
        position.quantity = round(position.quantity - reduce_quantity, 8)
        portfolio = db.scalar(select(Portfolio).where(Portfolio.id == position.portfolio_id))
        if portfolio:
            if position.side == "long":
                portfolio.cash_usd += exit_price * reduce_quantity
            else:
                portfolio.cash_usd += (position.entry_price - exit_price) * reduce_quantity
        risk_usd = None
        sl_for_r = getattr(position, "initial_stop_loss", None)
        if sl_for_r is None:
            sl_for_r = position.stop_loss
        if sl_for_r is not None:
            risk_usd = abs(position.entry_price - sl_for_r) * reduce_quantity
        if risk_usd is not None and float(risk_usd) < MIN_RISK_USD_FOR_R_AGGREGATION:
            risk_usd = None
        bpart = normalize_bucket(getattr(position, "capital_bucket", None))
        trade = Trade(
            portfolio_id=position.portfolio_id,
            position_id=position.id,
            symbol=position.symbol,
            side=position.side,
            strategy_name=position.strategy_name,
            action="partial_close",
            price=exit_price,
            quantity=reduce_quantity,
            fee_usd=exit_fee,
            pnl_usd=round(pnl_net, 4),
            risk_usd=round(risk_usd, 4) if risk_usd is not None else None,
            note=note or "Partial TP (proactive exit engine)",
            capital_bucket=bpart,
        )
        db.add(trade)
        db.flush()
        return trade

    def update_position_sl_tp(
        self,
        db: Session,
        position: Position,
        new_sl: float | None,
        new_tp: float | None,
        note: str = "",
    ) -> None:
        """Cập nhật SL/TP cho vị thế (chỉ DB). Logic đóng lệnh sẽ dùng giá trị mới."""
        if new_sl is not None:
            position.stop_loss = new_sl
        if new_tp is not None:
            position.take_profit = new_tp
        db.flush()
