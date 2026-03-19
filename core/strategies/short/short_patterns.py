"""
Phát hiện 3 setup short cốt lõi:
- Pump Exhaustion: pump mạnh + volume spike + chạm resistance + fail continuation + break structure down
- Bull Trap: phá đỉnh rồi đóng lại dưới + nến xác nhận yếu
- Trend Pullback: HTF downtrend + hồi lên EMA/supply + lower high + break xuống
"""
from __future__ import annotations

from typing import Any

# Candles: list of objects with .open, .high, .low, .close, .volume (e.g. Kline1h)


def _ohlcv(c: Any) -> tuple[float, float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None)
    lo = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None)
    v = getattr(c, "volume", None) or (c.get("volume") if isinstance(c, dict) else 0.0)
    return (float(o or 0), float(h or 0), float(lo or 0), float(cl or 0), float(v or 0))


def _atr(candles: list, period: int = 14) -> float | None:
    if not candles or len(candles) < 2 or period < 1:
        return None
    tr_list = []
    prev_close = None
    for c in candles:
        o, h, lo, cl, _ = _ohlcv(c)
        if prev_close is None:
            tr = h - lo
        else:
            tr = max(h - lo, abs(h - prev_close), abs(lo - prev_close))
        tr_list.append(tr)
        prev_close = cl
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else None
    return sum(tr_list[-period:]) / period


def _recent_high(candles: list, lookback: int = 10) -> float | None:
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    highs = [_ohlcv(c)[1] for c in subset]
    return max(highs) if highs else None


def _recent_low(candles: list, lookback: int = 10) -> float | None:
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    lows = [_ohlcv(c)[2] for c in subset]
    return min(lows) if lows else None


def detect_pump_exhaustion(
    candles: list,
    current_price: float,
    atr_mult: float = 1.0,
    volume_avg_lookback: int = 5,
) -> dict | None:
    """
    Pump mạnh + volume spike + chạm resistance + nến sau không follow-through + break micro structure xuống.
    Trả về dict với keys: resistance, break_low, volume_ratio, reasons; hoặc None.
    """
    if not candles or len(candles) < 6:
        return None
    o, h, lo, cl, vol = _ohlcv(candles[-1])
    prev_o, prev_h, prev_lo, prev_cl, prev_vol = _ohlcv(candles[-2])
    atr = _atr(candles, 14)
    if atr is None or atr <= 0:
        return None
    res = _recent_high(candles, 10)
    if res is None:
        return None
    # Volume spike: nến gần nhất volume > trung bình
    vol_slice = [_ohlcv(c)[4] for c in candles[-volume_avg_lookback - 1 : -1]]
    avg_vol = sum(vol_slice) / len(vol_slice) if vol_slice else 0
    volume_spike = avg_vol > 0 and vol >= 1.3 * avg_vol
    # Pump: nến trước đó tăng mạnh (body dương lớn)
    pump_candle = prev_cl > prev_o and (prev_cl - prev_o) >= atr * atr_mult
    # Chạm resistance: high gần resistance
    touch_res = res > 0 and h >= res * 0.998
    # Fail continuation: nến hiện tại đóng dưới open hoặc đóng dưới nửa trên của nến pump
    fail_cont = cl < o or (cl < (prev_o + prev_cl) / 2)
    # Break structure down: giá đóng dưới low gần đây (micro structure)
    recent_low = _recent_low(candles[:-1], 3)
    break_down = recent_low is not None and cl < recent_low * 1.002
    if not (pump_candle and touch_res and fail_cont):
        return None
    reasons = []
    if pump_candle:
        reasons.append("pump_candle")
    if volume_spike:
        reasons.append("volume_spike")
    if touch_res:
        reasons.append("touch_resistance")
    if fail_cont:
        reasons.append("fail_continuation")
    if break_down:
        reasons.append("break_structure_down")
    return {
        "resistance": res,
        "break_low": recent_low,
        "volume_ratio": vol / avg_vol if avg_vol > 0 else 0,
        "reasons": reasons,
        "atr": atr,
    }


def detect_bull_trap(
    candles: list,
    current_price: float,
    lookback_swing: int = 10,
) -> dict | None:
    """
    Phá đỉnh cũ (breakout) rồi đóng lại dưới vùng breakout + nến xác nhận bearish.
    """
    if not candles or len(candles) < lookback_swing + 3:
        return None
    # Swing high trước breakout
    before = candles[: -2]
    swing_high = _recent_high(before, lookback_swing) if before else None
    if swing_high is None:
        return None
    prev_o, prev_h, prev_lo, prev_cl, _ = _ohlcv(candles[-2])
    o, h, lo, cl, _ = _ohlcv(candles[-1])
    # Nến -2: breakout trên swing high (high > swing_high)
    broke_out = prev_h > swing_high * 1.001
    # Đóng lại dưới: close nến -2 hoặc nến -1 dưới swing_high
    close_below = prev_cl < swing_high * 0.999 or cl < swing_high * 0.999
    # Nến xác nhận bearish: nến hiện tại đóng dưới mở
    bearish_confirm = cl < o
    if not (broke_out and close_below and bearish_confirm):
        return None
    return {
        "breakout_level": swing_high,
        "close_below": close_below,
        "reasons": ["breakout_then_close_below", "bearish_confirm"],
    }


def detect_trend_pullback(
    candles: list,
    current_price: float,
    htf_downtrend: bool,
    ema_period: int = 20,
) -> dict | None:
    """
    HTF downtrend + giá hồi lên (EMA/supply) + lower high + break xuống lại.
    Cần htf_downtrend từ bên ngoài (4h/1h trend).
    """
    if not htf_downtrend or not candles or len(candles) < ema_period + 5:
        return None
    # EMA đơn giản
    closes = [_ohlcv(c)[3] for c in candles]
    ema = sum(closes[-ema_period:]) / ema_period if len(closes) >= ema_period else None
    if ema is None:
        return None
    o, h, lo, cl, _ = _ohlcv(candles[-1])
    prev_high = _ohlcv(candles[-2])[1] if len(candles) >= 2 else None
    # Pullback to EMA: giá đã chạm trên EMA gần đây
    recent_highs = [_ohlcv(c)[1] for c in candles[-5:]]
    touch_ema = max(recent_highs) >= ema * 0.995 if recent_highs else False
    # Lower high: high nến hiện tại < high nến trước
    lower_high = prev_high is not None and h < prev_high
    # Break down: đóng dưới EMA hoặc dưới low gần
    recent_low = _recent_low(candles[:-1], 3)
    break_down = cl < ema * 1.002 or (recent_low is not None and cl < recent_low * 1.001)
    if not (touch_ema and (lower_high or break_down)):
        return None
    return {
        "ema": ema,
        "lower_high": lower_high,
        "break_down": break_down,
        "reasons": ["htf_downtrend", "pullback_to_ema", "lower_high_or_break"],
    }


def detect_short_setups(
    candles: list,
    current_price: float,
    htf_downtrend: bool = False,
    config: dict | None = None,
) -> list[tuple[str, dict]]:
    """
    Chạy 3 detector; trả về list (setup_type, metrics).
    config: enable_pump_exhaustion, enable_bull_trap, enable_trend_pullback (default True).
    """
    cfg = config or {}
    out = []
    if cfg.get("enable_pump_exhaustion", True):
        pe = detect_pump_exhaustion(candles, current_price, atr_mult=0.8)
        if pe:
            out.append(("pump_exhaustion", pe))
    if cfg.get("enable_bull_trap", True):
        bt = detect_bull_trap(candles, current_price)
        if bt:
            out.append(("bull_trap", bt))
    if cfg.get("enable_trend_pullback", True) and htf_downtrend:
        tp = detect_trend_pullback(candles, current_price, htf_downtrend=True)
        if tp:
            out.append(("trend_pullback", tp))
    return out
