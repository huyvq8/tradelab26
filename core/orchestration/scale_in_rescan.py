"""
Cooldown rescan scale-in khi max_scale_in_reached — giảm spam log / evaluate mỗi 15s.
"""
from __future__ import annotations

import time

# Giây (monotonic): mặc định 120s; có thể chỉnh 60–180
SCALE_IN_MAX_REACHED_COOLDOWN_SEC = 120.0

_SCALE_IN_UNTIL: dict[str, float] = {}
_SCALE_IN_REGIME: dict[str, str] = {}
_SCALE_IN_1H_OPEN_MS: dict[str, int] = {}
_SCALE_IN_LAST_QTY: dict[str, float] = {}


def track_position_qty_for_scale_in(symbol: str, qty: float) -> None:
    """Gọi mỗi cycle khi có vị thế mở: nếu qty giảm → xóa cooldown."""
    sym = (symbol or "").strip().upper()
    prev = _SCALE_IN_LAST_QTY.get(sym)
    _SCALE_IN_LAST_QTY[sym] = float(qty or 0)
    if prev is not None and float(qty or 0) < prev - 1e-12:
        clear_scale_in_cooldown(sym)


def record_scale_in_max_reached(symbol: str, regime: str, klines_1h: list) -> None:
    sym = (symbol or "").strip().upper()
    _SCALE_IN_UNTIL[sym] = time.monotonic() + SCALE_IN_MAX_REACHED_COOLDOWN_SEC
    _SCALE_IN_REGIME[sym] = (regime or "").strip().lower()
    if klines_1h:
        last = klines_1h[-1]
        ms = int(getattr(last, "open_time_ms", 0) or 0)
        _SCALE_IN_1H_OPEN_MS[sym] = ms


def clear_scale_in_cooldown(symbol: str) -> None:
    sym = (symbol or "").strip().upper()
    _SCALE_IN_UNTIL.pop(sym, None)
    _SCALE_IN_REGIME.pop(sym, None)
    _SCALE_IN_1H_OPEN_MS.pop(sym, None)


def should_skip_scale_in_rescan(symbol: str, regime: str, klines_1h: list) -> bool:
    """
    True = bỏ qua evaluate scale-in (đang trong cooldown và không có ngoại lệ).
    Ngoại lệ: hết hạn; nến 1h mới (open_time_ms tăng); regime đổi (so với lúc ghi cooldown).
    """
    sym = (symbol or "").strip().upper()
    until = _SCALE_IN_UNTIL.get(sym, 0.0)
    if until <= time.monotonic():
        return False
    reg_now = (regime or "").strip().lower()
    if reg_now and reg_now != _SCALE_IN_REGIME.get(sym, reg_now):
        clear_scale_in_cooldown(sym)
        return False
    if klines_1h:
        cur_ms = int(getattr(klines_1h[-1], "open_time_ms", 0) or 0)
        if cur_ms > _SCALE_IN_1H_OPEN_MS.get(sym, 0):
            clear_scale_in_cooldown(sym)
            return False
    return True
