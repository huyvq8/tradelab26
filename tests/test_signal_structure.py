"""Native signal_structure helpers (extension score, comparability)."""
from __future__ import annotations

from core.strategies.signal_structure import extension_score_in_range


def _c(o, h, low, c):
    class K:
        pass

    k = K()
    k.open, k.high, k.low, k.close = o, h, low, c
    return k


def test_extension_score_at_range_high():
    klines = [_c(95, 96, 94, 95.5) for _ in range(8)]
    klines.append(_c(99, 101, 98, 100))
    x = extension_score_in_range(101.0, klines, lookback=10)
    assert x is not None
    assert x >= 0.99


def test_extension_score_at_range_low():
    klines = [_c(95, 96, 94, 95.5) for _ in range(8)]
    klines.append(_c(99, 101, 98, 100))
    x = extension_score_in_range(90.0, klines, lookback=10)
    assert x is not None
    assert x <= 0.05
