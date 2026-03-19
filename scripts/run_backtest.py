"""
Run backtest for active and candidate config on same OHLCV data (v4).
Fetches klines from Binance (1h, 500 candles), compares metrics, prints promotion result.
Usage: python scripts/run_backtest.py [SYMBOL]
  SYMBOL default: BTC
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import pandas as pd
from core.market_data.client import get_klines_1h
from core.validate.compare_configs import klines_to_dataframe, compare_configs


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    limit = 500
    print(f"Fetching {limit} x 1h klines for {symbol}...")
    klines = get_klines_1h(symbol, limit=limit)
    if not klines or len(klines) < 50:
        print("Not enough klines. Need at least 50.")
        return
    df = klines_to_dataframe(klines)
    if df.empty or len(df) < 50:
        print("DataFrame empty or too short.")
        return
    print(f"Running backtest on {len(df)} bars...")
    result = compare_configs(df, symbol=symbol)
    ma = result["metrics_active"]
    mc = result["metrics_candidate"]
    print("\n--- Active config ---")
    print(f"  Profit factor: {ma['profit_factor']:.2f}  Drawdown: {ma['max_drawdown_pct']:.1f}%  Trades: {ma['total_trades']}")
    print("--- Candidate config ---")
    print(f"  Profit factor: {mc['profit_factor']:.2f}  Drawdown: {mc['max_drawdown_pct']:.1f}%  Trades: {mc['total_trades']}")
    print("\nPromotion pass:", result["promotion_pass"])
    if result["promotion_reasons"]:
        for r in result["promotion_reasons"]:
            print("  -", r)


if __name__ == "__main__":
    main()
