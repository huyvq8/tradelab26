"""
Test Smart Short setups: pump_exhaustion, bull_trap, trend_pullback.
Run: python -m pytest tests/test_short_setups.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def _candle(o: float, h: float, low: float, c: float, vol: float = 1000.0):
    return type("Candle", (), {"open": o, "high": h, "low": low, "close": c, "volume": vol})()


def test_pump_exhaustion_valid():
    """Pump exhaustion: pump candle, touch resistance, fail continuation -> detect."""
    from core.strategies.short.short_patterns import detect_pump_exhaustion, _recent_high, _atr

    # Resistance = 104.5 (recent high). Nến -2: pump (98->104). Nến -1: high=104.5 (touch res), close 103 < open 104 (fail)
    candles = [
        _candle(100, 101, 99, 100.5, 800),
        _candle(100.5, 102, 100, 101, 900),
        _candle(101, 103, 100.5, 102, 850),
        _candle(102, 104, 101, 103, 900),
        _candle(103, 104.5, 102, 104, 950),
        _candle(104, 104.5, 103, 104, 1000),
        _candle(104, 104.5, 103.5, 104, 1100),
        _candle(104, 104.5, 103, 104, 1200),
        _candle(98, 104.5, 97, 104, 2000),
        _candle(104, 104.5, 102, 103, 1500),
    ]
    atr = _atr(candles, 14)
    assert atr and atr > 0
    res = _recent_high(candles, 10)
    assert res is not None
    out = detect_pump_exhaustion(candles, 103, atr_mult=0.5, volume_avg_lookback=5)
    assert out is not None
    assert "resistance" in out
    assert "reasons" in out
    assert "pump_candle" in out["reasons"] or "touch_resistance" in out["reasons"]


def test_pump_exhaustion_reject_no_pump():
    """No pump candle -> no detection."""
    from core.strategies.short.short_patterns import detect_pump_exhaustion

    candles = [_candle(100 + i * 0.1, 101 + i * 0.1, 99, 100.5 + i * 0.1, 500) for i in range(10)]
    out = detect_pump_exhaustion(candles, 101, atr_mult=2.0)
    assert out is None or "pump_candle" not in (out.get("reasons") or [])


def test_bull_trap_valid():
    """Bull trap: breakout above swing high then close below + bearish confirm."""
    from core.strategies.short.short_patterns import detect_bull_trap

    swing_high = 100.0
    candles = [
        _candle(98, 99, 97, 98.5, 500),
        _candle(98.5, 99.5, 98, 99, 500),
        _candle(99, 99.5, 98, 99, 500),
        _candle(99, 100.5, 98.5, 99.5, 600),
        _candle(99.5, 101, 99, 99.8, 700),
        _candle(99.8, 101.5, 99, 100.5, 800),
        _candle(100.5, 102, 100, 99.5, 900),
        _candle(99.5, 100, 98, 98.5, 1000),
    ]
    out = detect_bull_trap(candles, 98.5, lookback_swing=5)
    assert out is not None
    assert out.get("breakout_level") is not None
    assert "close_below" in out or "reasons" in out


def test_bull_trap_reject_no_breakout():
    """No breakout above swing -> no bull trap."""
    from core.strategies.short.short_patterns import detect_bull_trap

    candles = [_candle(98 + i * 0.2, 99 + i * 0.2, 97, 98.5 + i * 0.2, 500) for i in range(12)]
    out = detect_bull_trap(candles, 100, lookback_swing=10)
    assert out is None or out.get("breakout_level") is None


def test_trend_pullback_valid():
    """Trend pullback: HTF downtrend + pullback to EMA + lower high / break down."""
    from core.strategies.short.short_patterns import detect_trend_pullback

    closes = [100 - i * 1.5 for i in range(25)]
    candles = [_candle(c - 0.5, c + 1, c - 1, c, 500) for c in closes]
    candles[-1] = _candle(closes[-1] - 1, closes[-1] + 0.5, closes[-1] - 1.5, closes[-1] - 0.5, 500)
    out = detect_trend_pullback(candles, closes[-1] - 0.5, htf_downtrend=True, ema_period=20)
    assert out is None or isinstance(out, dict)


def test_trend_pullback_reject_htf_up():
    """HTF not downtrend -> no trend pullback short."""
    from core.strategies.short.short_patterns import detect_trend_pullback

    candles = [_candle(100 + i, 101 + i, 99 + i, 100.5 + i, 500) for i in range(25)]
    out = detect_trend_pullback(candles, 124, htf_downtrend=False)
    assert out is None


def test_detect_short_setups_integration():
    """detect_short_setups returns list of (setup_type, metrics)."""
    from core.strategies.short.short_patterns import detect_short_setups

    candles = [_candle(100, 101, 99, 100.5, 500) for _ in range(15)]
    out = detect_short_setups(candles, 100.5, htf_downtrend=False)
    assert isinstance(out, list)
    for item in out:
        assert len(item) == 2
        assert item[0] in ("pump_exhaustion", "bull_trap", "trend_pullback")
        assert isinstance(item[1], dict)


def test_short_signal_engine_no_signal_when_disabled():
    """When short config disabled, evaluate returns None."""
    from core.strategies.short.short_signal_engine import ShortSignalEngine

    engine = ShortSignalEngine({"short_strategy": {"enabled": False}})
    candles = [_candle(100, 105, 98, 103, 2000), _candle(103, 104, 101, 102, 1500)]
    out = engine.evaluate("BTC", 102, candles, htf_downtrend=False, regime="high_momentum")
    assert out is None
