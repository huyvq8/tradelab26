"""Normalized R metadata: initial risk from position, planned R at entry, validity for aggregates."""
from __future__ import annotations

from core.portfolio.models import Position, Trade
from core.risk.daily_r import MIN_RISK_USD_FOR_R_AGGREGATION
from core.strategies.base import StrategySignal


def risk_per_unit_at_entry(entry_price: float, stop_price: float | None, *, side: str) -> float | None:
    if stop_price is None or entry_price <= 0:
        return None
    ep = float(entry_price)
    sl = float(stop_price)
    if side == "short":
        d = sl - ep
    else:
        d = ep - sl
    if d <= 0:
        return None
    return d


def dollar_risk_at_open(entry_price: float, quantity: float, stop_price: float | None, *, side: str) -> float | None:
    ru = risk_per_unit_at_entry(entry_price, stop_price, side=side)
    if ru is None or quantity <= 0:
        return None
    return float(ru) * float(quantity)


def planned_r_multiple(signal: StrategySignal) -> float | None:
    ep = float(signal.entry_price or 0)
    if ep <= 0:
        return None
    sl = signal.stop_loss
    tp = signal.take_profit
    if sl is None or tp is None:
        return None
    side = (signal.side or "long").strip().lower()
    risk = risk_per_unit_at_entry(ep, float(sl), side=side)
    if risk is None or risk <= 0:
        return None
    tpv = float(tp)
    if side == "short":
        reward = ep - tpv
    else:
        reward = tpv - ep
    if reward <= 0:
        return None
    return round(reward / risk, 4)


def initial_sl_for_r(position: Position) -> float | None:
    sl0 = getattr(position, "initial_stop_loss", None)
    if sl0 is not None:
        return float(sl0)
    if position.stop_loss is not None:
        return float(position.stop_loss)
    return None


def risk_usd_for_full_close(position: Position) -> float | None:
    """Dollar risk at entry for the current quantity (initial SL, not trailed SL)."""
    sl = initial_sl_for_r(position)
    if sl is None:
        return None
    side = (position.side or "long").strip().lower()
    return dollar_risk_at_open(
        float(position.entry_price or 0),
        float(position.quantity or 0),
        sl,
        side=side,
    )


def trade_close_has_valid_risk(t: Trade) -> bool:
    if t.action != "close":
        return False
    r = getattr(t, "risk_usd", None)
    if r is None:
        return False
    return float(r) >= MIN_RISK_USD_FOR_R_AGGREGATION


def infer_close_source_from_note(note: str | None) -> str:
    """Map free-text close note to canonical close_source (Trade.close_source)."""
    if not note:
        return "unknown"
    n = (note or "").lower()
    if "sl " in n or "stop loss" in n or "stop_loss" in n or "kích hoạt (giá chạm stop" in n:
        return "sl_hit"
    if "tp " in n or "take profit" in n or "take_profit" in n or "kích hoạt (giá chạm take" in n:
        return "tp_hit"
    if "đồng bộ" in n or "sync_binance" in n or ("sync" in n and "binance" in n):
        return "sync_binance_reconcile"
    if "đóng chủ động" in n or "proactive" in n or "max_hold" in n or "thesis:" in n:
        return "proactive_close"
    if "thủ công" in n or "manual" in n or "futures" in n:
        return "manual_close"
    return "unknown"


def attach_open_trade_risk_fields(
    trade: Trade,
    *,
    entry_price: float,
    quantity: float,
    signal: StrategySignal,
) -> None:
    side = (signal.side or "long").strip().lower()
    sl = signal.stop_loss
    ru = risk_per_unit_at_entry(float(entry_price), float(sl) if sl is not None else None, side=side)
    trade.risk_per_unit = round(float(ru), 8) if ru is not None else None
    ir = dollar_risk_at_open(float(entry_price), float(quantity), float(sl) if sl is not None else None, side=side)
    trade.initial_risk_usd = round(float(ir), 4) if ir is not None else None
    trade.planned_r_multiple = planned_r_multiple(signal)
    trade.notional_usd = round(float(entry_price) * float(quantity), 4)
    trade.intended_entry_price = round(float(entry_price), 8)
    trade.intended_stop_loss = round(float(sl), 8) if sl is not None else None
    if signal.take_profit is not None:
        trade.intended_take_profit = round(float(signal.take_profit), 8)
    else:
        trade.intended_take_profit = None
