"""Paper hoặc Binance Futures thật theo cấu hình (ưu tiên dashboard_overrides.json)."""
from core.config import (
    settings,
    get_effective_enable_live_binance_futures,
    get_effective_binance_futures_testnet,
)
from core.execution.simulator import PaperExecutionSimulator
from core.execution.binance_futures import BinanceFuturesExecutor


def get_execution_backend():
    """Paper (mặc định) hoặc Binance Futures thật khi bật và có API key."""
    if get_effective_enable_live_binance_futures() and (
        (settings.binance_api_key or "").strip() and (settings.binance_api_secret or "").strip()
    ):
        return BinanceFuturesExecutor(use_testnet=get_effective_binance_futures_testnet())
    return PaperExecutionSimulator()
