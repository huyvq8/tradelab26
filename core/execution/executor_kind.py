"""Detect execution backend without importing Binance class (avoid circular imports)."""


def is_live_binance_executor(executor: object) -> bool:
    return getattr(executor, "__class__", type(executor)).__name__ == "BinanceFuturesExecutor"
