"""
Bundle dữ liệu thị trường 1 lần mỗi cycle — tránh gọi trùng quotes/klines giữa review_positions và run().
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.regime.detector import derive_regime

if TYPE_CHECKING:
    from core.market_data.client import MarketQuote


@dataclass
class CycleMarketSnapshot:
    """Quotes + nến 1h (tối đa 25) + nến 4h (tối đa 3) + regime đã derive — dùng chung trong cycle."""

    quotes: dict[str, "MarketQuote"]
    klines_1h_by_symbol: dict[str, list]
    klines_4h_by_symbol: dict[str, list]
    regime_by_symbol: dict[str, str]
    built_at_mono: float


def klines_1h_last_n(snapshot: CycleMarketSnapshot | None, symbol: str, n: int) -> list:
    """Lấy n cây 1h cuối từ snapshot (hoặc rỗng)."""
    if not snapshot:
        return []
    full = snapshot.klines_1h_by_symbol.get(symbol) or []
    if len(full) >= n:
        return full[-n:]
    return list(full)


def klines_4h_last_n(snapshot: CycleMarketSnapshot | None, symbol: str, n: int) -> list:
    if not snapshot:
        return []
    full = snapshot.klines_4h_by_symbol.get(symbol) or []
    if len(full) >= n:
        return full[-n:]
    return list(full)


def build_cycle_market_snapshot(symbols: list[str]) -> CycleMarketSnapshot:
    """Một lần: quotes + klines 1h×25 + 4h×3 + regime cho mỗi symbol."""
    from core.market_data.client import get_quotes_with_fallback, get_klines_1h, get_klines_4h

    syms = sorted({(s or "").strip().upper() for s in symbols if s and (s or "").strip()})
    if not syms:
        return CycleMarketSnapshot(
            quotes={},
            klines_1h_by_symbol={},
            klines_4h_by_symbol={},
            regime_by_symbol={},
            built_at_mono=time.monotonic(),
        )
    quotes = get_quotes_with_fallback(syms)
    k1: dict[str, list] = {}
    k4: dict[str, list] = {}
    reg: dict[str, str] = {}
    for s in syms:
        if s not in quotes:
            continue
        try:
            k1[s] = get_klines_1h(s, limit=25)
        except Exception:
            k1[s] = []
        try:
            k4[s] = get_klines_4h(s, limit=3)
        except Exception:
            k4[s] = []
        q = quotes[s]
        reg[s] = derive_regime(q.percent_change_24h, q.volume_24h)
    return CycleMarketSnapshot(
        quotes=quotes,
        klines_1h_by_symbol=k1,
        klines_4h_by_symbol=k4,
        regime_by_symbol=reg,
        built_at_mono=time.monotonic(),
    )


def merge_quotes_for_positions(
    snapshot: CycleMarketSnapshot | None,
    position_symbols: list[str],
) -> dict:
    """Ưu tiên snapshot; symbol thiếu → fetch nhỏ."""
    from core.market_data.client import get_quotes_with_fallback

    out: dict = {}
    missing: list[str] = []
    for sym in {x.strip().upper() for x in position_symbols if x}:
        if snapshot and sym in snapshot.quotes:
            out[sym] = snapshot.quotes[sym]
        else:
            missing.append(sym)
    if missing:
        extra = get_quotes_with_fallback(missing)
        out.update(extra)
    return out
