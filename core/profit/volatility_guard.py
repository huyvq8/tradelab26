"""
Phase 1 v6: Volatility guard — chặn hoặc giảm size khi biến động quá cao (ATR, volume/price cực đoan).
Input: symbol, quote, klines 1h. Output: allow_trade, reduce_size_pct, block_reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.config import settings


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROFIT_ACTIVE_PATH = _PROJECT_ROOT / "config" / "profit.active.json"


def load_profit_config() -> dict:
    """Đọc config profit layer (profit.active.json). Fallback về settings/env hoặc mặc định."""
    if _PROFIT_ACTIVE_PATH.exists():
        try:
            return json.loads(_PROFIT_ACTIVE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


@dataclass
class VolatilityGuardResult:
    allow_trade: bool
    reduce_size_pct: float  # 0.0 .. 1.0
    block_reason: str


def _atr_from_candles(candles: list, period: int = 14) -> float | None:
    """ATR(period) từ danh sách nến. Mỗi phần tử có .open, .high, .low, .close."""
    if not candles or len(candles) < 2 or period < 1:
        return None
    tr_list = []
    prev_close = None
    for c in candles:
        h, low = getattr(c, "high", None), getattr(c, "low", None)
        cl = getattr(c, "close", None)
        if h is None or low is None or cl is None:
            continue
        h, low, cl = float(h), float(low), float(cl)
        if prev_close is None:
            tr = h - low if low < h else 0.0
        else:
            tr = max(h - low, abs(h - prev_close), abs(low - prev_close))
        tr_list.append(tr)
        prev_close = cl
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else None
    return sum(tr_list[-period:]) / period


def check_volatility_guard(
    symbol: str,
    quote: object,
    klines_1h: list,
    config: dict | None = None,
) -> VolatilityGuardResult:
    """
    Kiểm tra volatility: ATR/price quá cao → block hoặc reduce; biến động 24h cực đoan → reduce.
    quote: object có .price, .volume_24h, .percent_change_24h (MarketQuote).
    klines_1h: list nến 1h (có .open, .high, .low, .close) để tính ATR.
    config: từ load_profit_config() hoặc None (dùng mặc định).
    """
    cfg = config if config is not None else load_profit_config()
    vol_cfg = cfg.get("volatility_guard") or {}
    enabled = vol_cfg.get("enabled", True)
    if not enabled:
        return VolatilityGuardResult(allow_trade=True, reduce_size_pct=0.0, block_reason="")

    price = getattr(quote, "price", None) or (quote.get("price") if isinstance(quote, dict) else None)
    if price is None or float(price) <= 0:
        return VolatilityGuardResult(allow_trade=True, reduce_size_pct=0.0, block_reason="")
    price = float(price)
    change_24h = getattr(quote, "percent_change_24h", None)
    if change_24h is None and isinstance(quote, dict):
        change_24h = quote.get("percent_change_24h", 0)
    change_24h = float(change_24h or 0)

    # Ngưỡng từ config (mặc định: block khi ATR/price > 6%, reduce 50% khi > 4%)
    atr_block_pct = float(vol_cfg.get("atr_block_pct", 0.06))
    atr_reduce_pct = float(vol_cfg.get("atr_reduce_pct", 0.04))
    atr_reduce_size_pct = float(vol_cfg.get("atr_reduce_size_pct", 0.5))
    change_24h_reduce_abs = float(vol_cfg.get("change_24h_reduce_abs", 15.0))  # |change| > 15% → reduce
    change_24h_reduce_size_pct = float(vol_cfg.get("change_24h_reduce_size_pct", 0.3))

    reduce_from_atr = 0.0
    reduce_from_change = 0.0
    block_reason = ""

    # 1) ATR
    atr = _atr_from_candles(klines_1h, period=14)
    if atr is not None and price > 0:
        atr_pct = atr / price
        if atr_pct >= atr_block_pct:
            block_reason = f"volatility_guard: ATR/price {atr_pct*100:.2f}% >= block {atr_block_pct*100:.0f}%"
            return VolatilityGuardResult(allow_trade=False, reduce_size_pct=0.0, block_reason=block_reason)
        if atr_pct >= atr_reduce_pct:
            reduce_from_atr = atr_reduce_size_pct

    # 2) Biến động 24h cực đoan (proxy cho panic/pump)
    if abs(change_24h) >= change_24h_reduce_abs:
        reduce_from_change = change_24h_reduce_size_pct

    reduce_size_pct = min(1.0, reduce_from_atr + reduce_from_change)
    if reduce_size_pct > 0 and not block_reason:
        reasons = []
        if reduce_from_atr > 0:
            reasons.append(f"ATR/price cao (reduce {reduce_from_atr*100:.0f}%)")
        if reduce_from_change > 0:
            reasons.append(f"|change_24h|={abs(change_24h):.1f}% (reduce {reduce_from_change*100:.0f}%)")
        block_reason = "volatility_guard: " + "; ".join(reasons)

    return VolatilityGuardResult(
        allow_trade=True,
        reduce_size_pct=round(reduce_size_pct, 2),
        block_reason=block_reason,
    )
