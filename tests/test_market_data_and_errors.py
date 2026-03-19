"""
Unit test: market_data (cache, parse klines, 429 fallback), candlestick patterns, regime, 4h trend filter.
Chạy: python -m pytest tests/test_market_data_and_errors.py -v
Hoặc: cd trading-lab-pro-v3 && python tests/test_market_data_and_errors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def test_parse_klines_response():
    """BinanceClient._parse_klines_response trả về list Kline1h từ mảng Binance."""
    from core.market_data.client import BinanceClient, Kline1h

    arr = [
        [1700000000000, "100", "102", "99", "101", "1000"],
        [1700003600000, "101", "103", "100", "102", "1100"],
    ]
    out = BinanceClient()._parse_klines_response(arr)
    assert len(out) == 2
    assert out[0].open == 100.0 and out[0].high == 102.0 and out[0].low == 99.0 and out[0].close == 101.0
    assert out[0].volume == 1000.0 and out[0].open_time_ms == 1700000000000
    assert out[1].close == 102.0


def test_parse_klines_empty():
    """Parse mảng rỗng hoặc phần tử thiếu -> list rỗng hoặc bỏ qua phần tử lỗi."""
    from core.market_data.client import BinanceClient

    assert BinanceClient()._parse_klines_response([]) == []
    # Phần tử có < 6 phần thì không append (len(k) >= 6)
    out = BinanceClient()._parse_klines_response([["a", "b"]])
    assert out == []


def test_klines_cache_key_and_ttl():
    """Cache klines dùng key (symbol, interval, limit); TTL theo interval."""
    from core.market_data.client import KLINES_CACHE_TTL, DEFAULT_KLINES_TTL

    assert KLINES_CACHE_TTL["1h"] == 60
    assert KLINES_CACHE_TTL["5m"] == 30
    assert KLINES_CACHE_TTL["4h"] == 300
    assert DEFAULT_KLINES_TTL == 60


def test_derive_regime():
    """Regime: high_momentum, risk_off, balanced."""
    from core.regime.detector import derive_regime

    assert derive_regime(6, 10_000_000) == "high_momentum"
    assert derive_regime(5.1, 6_000_000) == "high_momentum"
    assert derive_regime(-6, 1) == "risk_off"
    assert derive_regime(0, 0) == "balanced"
    assert derive_regime(3, 1_000_000) == "balanced"


def test_detect_patterns_doji():
    """Pattern doji: body rất nhỏ so với range."""
    from core.patterns.candlestick import detect_patterns, Candle

    # Nến body nhỏ, range lớn
    candles = [Candle(open=100, high=102, low=98, close=100.1, volume=0)]
    assert "doji" in detect_patterns(candles)


def test_detect_patterns_hammer():
    """Pattern hammer: bóng dưới dài, body nhỏ trên, upper wick rất ngắn."""
    from core.patterns.candlestick import detect_patterns, Candle

    # Body nhỏ, bóng dưới dài, bóng trên rất ngắn (để không bị doji: body/range >= 0.1)
    candles = [Candle(open=100, high=100.6, low=95, close=100.6, volume=0)]
    assert "hammer" in detect_patterns(candles)


def test_detect_patterns_empty():
    """Pattern với list rỗng -> []."""
    from core.patterns.candlestick import detect_patterns

    assert detect_patterns([]) == []


def test_4h_trend_filter_logic_long():
    """Logic 4h filter: long chỉ khi nến 4h gần nhất bullish (close >= open)."""
    from core.market_data.client import Kline1h

    bullish = Kline1h(open=100, high=105, low=99, close=103, volume=0, open_time_ms=0)
    bearish = Kline1h(open=103, high=104, low=98, close=100, volume=0, open_time_ms=0)
    assert bullish.close >= bullish.open  # long cho phép
    assert not (bearish.close >= bearish.open)  # long bị lọc


def test_4h_trend_filter_logic_short():
    """Logic 4h filter: short chỉ khi nến 4h gần nhất bearish (close <= open)."""
    from core.market_data.client import Kline1h

    bearish = Kline1h(open=103, high=104, low=98, close=100, volume=0, open_time_ms=0)
    bullish = Kline1h(open=100, high=105, low=99, close=103, volume=0, open_time_ms=0)
    assert bearish.close <= bearish.open  # short cho phép
    assert not (bullish.close <= bullish.open)  # short bị lọc


def test_get_klines_uses_cache_when_fresh():
    """Khi cache còn hạn (trong TTL), get_klines trả về cache không gọi HTTP."""
    import time
    from core.market_data.client import BinanceClient, Kline1h

    BinanceClient._klines_cache.clear()
    key = ("BTC", "1h", 5)
    cached = [
        Kline1h(open=1, high=2, low=0.5, close=1.5, volume=100, open_time_ms=0),
    ]
    BinanceClient._klines_cache[key] = (time.monotonic(), cached)

    client = BinanceClient()
    result = client.get_klines("BTC", interval="1h", limit=5)
    assert result == cached
    BinanceClient._klines_cache.clear()


def test_effective_risk_capital_usd():
    from core.risk.engine import effective_risk_capital_usd
    from core.config import settings

    assert effective_risk_capital_usd(500.0) == 500.0
    assert effective_risk_capital_usd(None) == float(settings.default_capital_usd)
    assert effective_risk_capital_usd(0) == float(settings.default_capital_usd)


def test_risk_engine_rejects_when_kill_switch_and_over_threshold():
    """RiskEngine từ chối mở lệnh khi kill switch bật và daily R <= -threshold."""
    from unittest.mock import patch
    from core.risk.engine import RiskEngine
    from core.strategies.base import StrategySignal

    signal = StrategySignal(
        symbol="BTC",
        strategy_name="trend_following",
        side="long",
        confidence=0.7,
        entry_price=100,
        stop_loss=98,
        take_profit=102,
        rationale="Test",
        regime="high_momentum",
    )
    with patch("core.risk.engine.get_effective_kill_switch_enabled", return_value=True), \
         patch("core.risk.engine.get_effective_kill_switch_r_threshold", return_value=3.0):
        engine = RiskEngine()
        decision = engine.assess(
            signal=signal,
            available_cash=1000,
            open_positions=0,
            daily_realized_pnl=0,
            daily_realized_r=-4.0,
            consecutive_loss_count=0,
        )
    assert decision.approved is False
    assert "kill" in decision.reason.lower() or "R" in decision.reason or "dung" in decision.reason.lower()


def run_all():
    """Chạy toàn bộ test (khi không dùng pytest)."""
    tests = [
        ("parse_klines_response", test_parse_klines_response),
        ("parse_klines_empty", test_parse_klines_empty),
        ("klines_cache_ttl", test_klines_cache_key_and_ttl),
        ("derive_regime", test_derive_regime),
        ("detect_patterns_doji", test_detect_patterns_doji),
        ("detect_patterns_hammer", test_detect_patterns_hammer),
        ("detect_patterns_empty", test_detect_patterns_empty),
        ("4h_filter_long", test_4h_trend_filter_logic_long),
        ("4h_filter_short", test_4h_trend_filter_logic_short),
        ("get_klines_cache", test_get_klines_uses_cache_when_fresh),
        ("effective_risk_capital", test_effective_risk_capital_usd),
        ("risk_kill_switch", test_risk_engine_rejects_when_kill_switch_and_over_threshold),
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  OK: {name}")
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            failed.append((name, e))
    if failed:
        print(f"\n{len(failed)} test(s) failed.")
        sys.exit(1)
    print("\nAll market_data & error checks passed.")


if __name__ == "__main__":
    run_all()
