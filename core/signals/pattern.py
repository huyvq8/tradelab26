"""
Nhan dien dau hieu nen (V-reversal, engulfing, ...).
Can du lieu OHLC (Binance/Bybit) de hoat dong that.
Hien tai: stub - tra ve None; khi co OHLC se tich hop va gui Telegram khi phat hien pattern.
"""
from __future__ import annotations


def check_v_reversal_trap(symbol: str, ohlc_bars: list[dict] | None) -> dict | None:
    """
    Kiem tra mau V-reversal trap (dump roi bounce roi dap xuong).
    ohlc_bars: list of {open, high, low, close, volume}, moi phan tu 1 nen.
    Tra ve None neu chua co OHLC hoac chua phat hien; neu co thi tra dict mo ta pattern de gui Telegram.
    """
    if not ohlc_bars or len(ohlc_bars) < 5:
        return None
    # TODO: khi co OHLC that, viet logic nhan dien (vd: 3 nen giam manh, 2 nen tang, du doan dap xuong)
    return None
