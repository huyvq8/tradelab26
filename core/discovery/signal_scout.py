"""
Scout token đang có dấu hiệu vào lệnh (long/short).
Quét top symbol theo volume 24h, chạy regime + chiến lược, chấm điểm và trả về top N.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.market_data.client import get_top_symbols_quotes_by_volume, MarketQuote
from core.regime.detector import derive_regime
from core.strategies.implementations import build_strategy_set
from core.strategies.base import StrategySignal


@dataclass
class ScoutResult:
    """Một token có ít nhất một tín hiệu, kèm điểm và tín hiệu tốt nhất."""
    symbol: str
    regime: str
    best_signal: StrategySignal
    all_signals: list[StrategySignal]
    score: float
    quote: MarketQuote


def _score_signal(sig: StrategySignal, volume_24h: float) -> float:
    """
    Điểm tổng hợp: confidence (ưu tiên) + trọng số theo volume (thanh khoản).
    volume càng lớn càng đáng quan tâm nhưng không lấn át confidence.
    """
    conf_part = sig.confidence * 60.0  # tối đa 60 (khi confidence=1)
    vol_norm = min(volume_24h / 100_000_000, 1.0) * 40.0  # volume 100M+ => +40
    return round(conf_part + vol_norm, 2)


def scan_candidates(
    top_universe: int = 100,
    min_volume_usd: float = 500_000,
    result_top_n: int = 10,
) -> list[ScoutResult]:
    """
    Quét các token có dấu hiệu vào lệnh từ universe (top symbol theo volume).
    Trả về top `result_top_n` kết quả đã sắp xếp theo điểm giảm dần.
    """
    quotes = get_top_symbols_quotes_by_volume(top_n=top_universe, min_volume_usd=min_volume_usd)
    if not quotes:
        return []
    strategies = build_strategy_set()
    results: list[ScoutResult] = []
    for symbol, quote in quotes.items():
        chg = quote.percent_change_24h
        vol = quote.volume_24h
        regime = derive_regime(chg, vol)
        signals: list[StrategySignal] = []
        for strat in strategies:
            sig = strat.evaluate(symbol, quote.price, chg, vol, regime)
            if sig:
                signals.append(sig)
        if not signals:
            continue
        # Lấy tín hiệu tốt nhất (confidence cao nhất)
        best = max(signals, key=lambda s: s.confidence)
        score = _score_signal(best, vol)
        results.append(ScoutResult(
            symbol=symbol,
            regime=regime,
            best_signal=best,
            all_signals=signals,
            score=score,
            quote=quote,
        ))
    results.sort(key=lambda x: x.score, reverse=True)
    return results[:result_top_n]
