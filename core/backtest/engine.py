"""Backtest engine: run strategies on OHLCV series, record trades, compute metrics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from core.regime.detector import derive_regime
from core.strategies.base import StrategySignal
from core.strategies.implementations import build_strategy_set, build_strategy_set_from_config


def _window_klines(data: pd.DataFrame, i: int, n: int = 25) -> list:
    out = []
    start = max(0, i - n + 1)
    for j in range(start, i + 1):
        r = data.iloc[j]

        class K:
            pass

        k = K()
        k.open = float(r["open"])
        k.high = float(r["high"])
        k.low = float(r["low"])
        k.close = float(r["close"])
        k.volume = float(r["volume"])
        out.append(k)
    return out


@dataclass
class BacktestTrade:
    symbol: str
    strategy_name: str
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    quantity: float
    pnl_usd: float
    exit_reason: str  # "stop" | "tp" | "eos"


def run_backtest(
    data: pd.DataFrame,
    symbol: str = "BTC",
    strategies: list | None = None,
    strategy_config: dict | None = None,
    initial_capital: float = 1000.0,
    risk_pct: float = 0.01,
    fee_bps: int = 10,
) -> dict:
    """
    Run backtest on DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
    If strategy_config is set, strategies are built from config (enabled + disabled_under_regime).
    Returns dict with trades list and metrics: win_rate, profit_factor, expectancy, max_drawdown, total_trades.
    """
    if strategies is None:
        if strategy_config:
            strategies = build_strategy_set_from_config(strategy_config)
        else:
            strategies = build_strategy_set()
    disabled_regimes = (strategy_config or {}).get("disabled_under_regime") or {}
    required = ["open", "high", "low", "close", "volume"]
    for c in required:
        if c not in data.columns:
            raise ValueError(f"DataFrame must have column '{c}'")
    data = data.sort_index().dropna(subset=["close", "volume"])
    if data.empty or len(data) < 2:
        return {
            "trades": [],
            "winrate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
            "total_pnl": 0.0,
        }

    change_24h = data["close"].pct_change().fillna(0) * 100
    trades: list[BacktestTrade] = []
    position: dict | None = None  # signal, entry_time, entry_price, quantity
    cash = initial_capital
    equity_curve = [initial_capital]
    peak = initial_capital

    for i in range(1, len(data)):
        row = data.iloc[i]
        prev = data.iloc[i - 1]
        ts = data.index[i]
        open_, high, low, close, volume = (
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
        )
        chg = change_24h.iloc[i]
        regime = derive_regime(chg, volume)

        # Check exit for open position
        if position is not None:
            sig, entry_time, entry_price, qty = (
                position["signal"],
                position["entry_time"],
                position["entry_price"],
                position["quantity"],
            )
            exit_price = None
            exit_reason = None
            if sig.side == "long":
                if low <= sig.stop_loss:
                    exit_price = sig.stop_loss
                    exit_reason = "stop"
                elif high >= sig.take_profit:
                    exit_price = sig.take_profit
                    exit_reason = "tp"
            else:
                if high >= sig.stop_loss:
                    exit_price = sig.stop_loss
                    exit_reason = "stop"
                elif low <= sig.take_profit:
                    exit_price = sig.take_profit
                    exit_reason = "tp"
            if exit_price is not None and exit_reason is not None:
                direction = 1 if sig.side == "long" else -1
                pnl = (exit_price - entry_price) * qty * direction
                fee = (entry_price * qty + exit_price * qty) * fee_bps / 10_000
                pnl -= fee
                if sig.side == "long":
                    cash += exit_price * qty - fee
                else:
                    cash += (entry_price - exit_price) * qty - fee
                trades.append(
                    BacktestTrade(
                        symbol=symbol,
                        strategy_name=sig.strategy_name,
                        side=sig.side,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        exit_time=ts,
                        exit_price=exit_price,
                        quantity=qty,
                        pnl_usd=pnl,
                        exit_reason=exit_reason,
                    )
                )
                position = None

        # If no position, try to open
        if position is None:
            for strategy in strategies:
                if regime in disabled_regimes.get(strategy.name, []):
                    continue
                signal = strategy.evaluate(
                    symbol, close, chg, volume, regime, klines_1h=_window_klines(data, i)
                )
                if signal is None:
                    continue
                risk_dollars = initial_capital * risk_pct
                stop_distance = abs(signal.entry_price - signal.stop_loss) / max(
                    signal.entry_price, 1e-9
                )
                size_usd = min(cash * 0.95, risk_dollars / stop_distance) if stop_distance > 0 else 0
                if size_usd < 25:
                    continue
                slippage = 1 + 5 / 10_000 if signal.side == "long" else 1 - 5 / 10_000
                entry_price = close * slippage
                quantity = size_usd / entry_price
                cash -= size_usd
                position = {
                    "signal": signal,
                    "entry_time": ts,
                    "entry_price": entry_price,
                    "quantity": quantity,
                }
                break

        # Equity = cash + position value at close
        if position is not None:
            pos = position
            sig, entry_price, qty = pos["signal"], pos["entry_price"], pos["quantity"]
            if sig.side == "long":
                equity_curve.append(cash + close * qty)
            else:
                equity_curve.append(cash + (entry_price - close) * qty)
        else:
            equity_curve.append(cash)
        peak = max(peak, equity_curve[-1])

    # EOS: close any remaining position at last close
    if position is not None:
        sig = position["signal"]
        entry_time = position["entry_time"]
        entry_price = position["entry_price"]
        qty = position["quantity"]
        close = data["close"].iloc[-1]
        ts = data.index[-1]
        direction = 1 if sig.side == "long" else -1
        pnl = (close - entry_price) * qty * direction
        fee = (entry_price * qty + close * qty) * fee_bps / 10_000
        pnl -= fee
        if sig.side == "long":
            cash += close * qty - fee
        else:
            cash += (entry_price - close) * qty - fee
        trades.append(
            BacktestTrade(
                symbol=symbol,
                strategy_name=sig.strategy_name,
                side=sig.side,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=ts,
                exit_price=close,
                quantity=qty,
                pnl_usd=pnl,
                exit_reason="eos",
            )
        )

    # Metrics
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "trades": [],
            "winrate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
            "total_pnl": 0.0,
        }
    total_pnl = sum(t.pnl_usd for t in trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    winrate = len(wins) / total_trades if total_trades else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    expectancy = total_pnl / total_trades
    drawdowns = []
    peak = equity_curve[0]
    for e in equity_curve:
        if e >= peak:
            peak = e
        drawdowns.append((peak - e) / peak * 100 if peak > 0 else 0)
    max_drawdown_pct = max(drawdowns) if drawdowns else 0.0

    return {
        "trades": trades,
        "winrate": round(winrate, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy": round(expectancy, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
    }
