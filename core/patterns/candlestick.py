"""
Phát hiện hình nến từ OHLC (và volume). Dùng cho quyết định cập nhật TP/SL khi có vị thế.
Các pattern: hammer, engulfing_bull, engulfing_bear, doji, big_body (capitulation-like), rejection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_pct(self) -> float:
        return (self.body / self.open * 100) if self.open and self.open > 0 else 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def range_size(self) -> float:
        return self.high - self.low if self.high > self.low else 1e-10


def _to_candle(c: Any) -> Candle:
    if isinstance(c, Candle):
        return c
    if hasattr(c, "open"):
        return Candle(open=c.open, high=c.high, low=c.low, close=c.close, volume=getattr(c, "volume", 0))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return Candle(open=float(c[1]), high=float(c[2]), low=float(c[3]), close=float(c[4]), volume=float(c[5]) if len(c) > 5 else 0)
    if isinstance(c, dict):
        return Candle(
            open=float(c["open"]), high=float(c["high"]), low=float(c["low"]), close=float(c["close"]),
            volume=float(c.get("volume", 0)),
        )
    raise ValueError(f"Cannot convert to Candle: {type(c)}")


def detect_patterns(candles: list[Any], min_body_pct: float = 2.0) -> list[str]:
    """
    Nhận danh sách nến (mới nhất cuối). Trả về list tên pattern phát hiện được.
    Cần ít nhất 2 nến (để engulfing); 1 nến cho hammer/doji/big_body.
    """
    if not candles:
        return []
    out: list[str] = []
    cs = [_to_candle(c) for c in candles]
    last = cs[-1]

    # Doji: body rất nhỏ so với range
    if last.range_size and last.body / last.range_size < 0.1:
        out.append("doji")

    # Hammer (bullish): bóng dưới dài, body nhỏ ở trên, thường sau downtrend
    if last.lower_wick >= 2 * last.body and last.upper_wick <= last.body * 0.5 and last.body_pct <= 5:
        out.append("hammer")

    # Shooting star (bearish): bóng trên dài, body nhỏ ở dưới
    if last.upper_wick >= 2 * last.body and last.lower_wick <= last.body * 0.5 and last.body_pct <= 5:
        out.append("shooting_star")

    # Big body (capitulation / strong move)
    if last.body_pct >= 8:
        out.append("big_body_bull" if last.is_bullish else "big_body_bear")

    # Engulfing: cần 2 nến
    if len(cs) >= 2:
        prev = cs[-2]
        # Bullish engulfing: nến sau (bull) “nuốt” nến trước (bear)
        if last.is_bullish and not prev.is_bullish and last.open <= prev.close and last.close >= prev.open and last.close > prev.open and last.open < prev.close:
            out.append("engulfing_bull")
        # Bearish engulfing
        if not last.is_bullish and prev.is_bullish and last.open >= prev.close and last.close <= prev.open and last.close < prev.open and last.open > prev.close:
            out.append("engulfing_bear")

    # Rejection at high (long upper wick, có thể đảo chiều)
    if last.range_size and last.upper_wick / last.range_size >= 0.6 and last.body_pct >= 1:
        out.append("rejection_high")
    # Rejection at low
    if last.range_size and last.lower_wick / last.range_size >= 0.6 and last.body_pct >= 1:
        out.append("rejection_low")

    return out
