# Smart Short signal engine: patterns -> score -> filter -> planner -> ShortSignal
from __future__ import annotations

from typing import Any

from core.strategies.short.short_models import ShortSignal
from core.strategies.short.short_patterns import detect_short_setups
from core.strategies.short.short_scoring import score_short_setup
from core.strategies.short.short_filters import apply_short_filters, has_structure_break
from core.strategies.short.short_entry_planner import plan_short_entry_sl_tp


class ShortSignalEngine:
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        candles: list[Any],
        htf_downtrend: bool,
        regime: str = "",
    ) -> ShortSignal | None:
        short_cfg = self.config.get("short_strategy") or self.config
        if not short_cfg.get("enabled", True):
            return None
        setups = detect_short_setups(
            candles,
            current_price,
            htf_downtrend=htf_downtrend,
            config={
                "enable_pump_exhaustion": short_cfg.get("enable_pump_exhaustion", True),
                "enable_bull_trap": short_cfg.get("enable_bull_trap", True),
                "enable_trend_pullback": short_cfg.get("enable_trend_pullback", True),
            },
        )
        if not setups:
            return None
        min_score = int(short_cfg.get("min_score", 7))
        filters_cfg = short_cfg.get("filters") or {}
        planner_cfg = short_cfg.get("sl_tp_rules") or short_cfg
        htf_bullish = not htf_downtrend
        htf_strong_bull = False
        for setup_type, metrics in setups:
            score, reasons, debug = score_short_setup(
                setup_type, metrics, candles, current_price, htf_downtrend, short_cfg
            )
            if score < min_score:
                continue
            entry, sl, tp, tp_levels, inv_reason = plan_short_entry_sl_tp(
                setup_type, metrics, candles, current_price, planner_cfg
            )
            has_break = has_structure_break(metrics, setup_type)
            pass_filter, reject = apply_short_filters(
                symbol,
                setup_type,
                entry,
                sl,
                current_price,
                htf_bullish=htf_bullish,
                htf_strong_bull=htf_strong_bull,
                has_structure_break=has_break,
                config=filters_cfg,
            )
            if not pass_filter:
                continue
            confidence = min(1.0, score / 14.0)
            return ShortSignal(
                symbol=symbol,
                setup_type=setup_type,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                take_profit_levels=tp_levels,
                confidence_score=confidence,
                reasons=reasons,
                invalidation_reason=inv_reason,
                debug_metrics={**debug, "raw_score": score},
                regime=regime,
            )
        return None


def evaluate_short(
    symbol: str,
    current_price: float,
    candles: list[Any],
    htf_downtrend: bool,
    regime: str = "",
    config: dict | None = None,
) -> ShortSignal | None:
    engine = ShortSignalEngine(config)
    return engine.evaluate(symbol, current_price, candles, htf_downtrend, regime)
