"""
Compare backtest results: active vs candidate config on same data.
"""
from __future__ import annotations

import pandas as pd

from core.backtest.engine import run_backtest
from core.ai.optimizer_agent import get_active_strategy_config, get_candidate_strategy_config
from core.validate.promotion_rules import load_promotion_rules, check_promotion


def klines_to_dataframe(klines: list) -> pd.DataFrame:
    """Convert list of Kline1h (open, high, low, close, volume, open_time_ms) to DataFrame."""
    if not klines:
        return pd.DataFrame()
    rows = []
    for k in klines:
        rows.append({
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "open_time_ms": getattr(k, "open_time_ms", 0),
        })
    df = pd.DataFrame(rows)
    if "open_time_ms" in df.columns:
        df["dt"] = pd.to_datetime(df["open_time_ms"], unit="ms")
        df = df.set_index("dt")
        df = df.drop(columns=["open_time_ms"], errors="ignore")
    df = df.sort_index()
    return df


def run_backtest_with_config(data: pd.DataFrame, strategy_config: dict, **kwargs) -> dict:
    """Run backtest and return metrics only (no trades list in result for comparison)."""
    result = run_backtest(
        data,
        strategy_config=strategy_config,
        **kwargs,
    )
    return {
        "profit_factor": result.get("profit_factor", 0),
        "max_drawdown_pct": result.get("max_drawdown_pct", 0),
        "total_trades": result.get("total_trades", 0),
        "winrate": result.get("winrate", 0),
        "total_pnl": result.get("total_pnl", 0),
    }


def compare_configs(
    data: pd.DataFrame,
    symbol: str = "BTC",
    initial_capital: float = 1000.0,
    risk_pct: float = 0.01,
) -> dict:
    """
    Run backtest for active and candidate config on same data.
    Return: metrics_active, metrics_candidate, promotion_pass, promotion_reasons.
    """
    active_cfg = get_active_strategy_config()
    candidate_cfg = get_candidate_strategy_config()
    if not candidate_cfg:
        candidate_cfg = active_cfg
    metrics_active = run_backtest_with_config(
        data, active_cfg, symbol=symbol, initial_capital=initial_capital, risk_pct=risk_pct,
    )
    metrics_candidate = run_backtest_with_config(
        data, candidate_cfg, symbol=symbol, initial_capital=initial_capital, risk_pct=risk_pct,
    )
    promotion_pass, promotion_reasons = check_promotion(metrics_active, metrics_candidate)
    return {
        "metrics_active": metrics_active,
        "metrics_candidate": metrics_candidate,
        "promotion_pass": promotion_pass,
        "promotion_reasons": promotion_reasons,
    }
