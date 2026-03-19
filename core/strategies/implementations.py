from core.strategies.base import BaseStrategy, StrategySignal
from core.strategies.signal_structure import (
    atr_mean_range,
    extension_score_in_range,
    quality_long_momentum,
    structural_long_levels,
)

# Chan entry khi gia qua cang range (bug/trading_system_bugs_and_fixes 2.3) — bo sung entry_context_gates.
_STRATEGY_MAX_EXTENSION_FOR_ENTRY = 0.88


def _native_structure_meta(atr: float, setup: str, **extra: object) -> dict:
    """Gắn lên StrategySignal — log/journal/experiment không cần đọc lại cycle."""
    m: dict = {"atr_mean_range_1h": round(float(atr), 8), "setup": setup}
    m.update({k: v for k, v in extra.items() if v is not None})
    return m


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime, klines_1h=None):
        if regime != "high_momentum" or change_24h <= 3:
            return None
        klines = klines_1h or []
        if len(klines) >= 15:
            atr = atr_mean_range(klines, 14)
            if atr and atr > 0:
                zl, zh, sl, tp, tp_ext, _ = structural_long_levels(
                    price,
                    atr,
                    tp_atr_mult=1.5,
                    tp_ext_atr_mult=2.25,
                    max_tp_pct=0.03,
                    max_tp_ext_pct=0.045,
                )
                q = quality_long_momentum(
                    regime=regime, change_24h=change_24h, price=price, klines=klines, atr=atr
                )
                ext = extension_score_in_range(price, klines, lookback=10)
                if ext is not None and float(ext) > _STRATEGY_MAX_EXTENSION_FOR_ENTRY:
                    return None
                conf = min(0.85, 0.62 + (q - 0.5) * 0.4)
                rationale = (
                    f"Trend continuation: ATR zone [{zl:.6f},{zh:.6f}], structural SL, TP1/TP2 from ATR; quality={q:.2f}."
                )
                return StrategySignal(
                    symbol,
                    self.name,
                    "long",
                    conf,
                    price,
                    sl,
                    tp,
                    rationale,
                    regime,
                    entry_zone_low=zl,
                    entry_zone_high=zh,
                    take_profit_extended=tp_ext,
                    levels_from_structure=True,
                    atr_estimate_1h=atr,
                    structure_meta=_native_structure_meta(
                        atr,
                        "trend_following",
                        entry_zone=[zl, zh],
                        sl_atr_mult=1.25,
                        tp_atr_mult=2.0,
                        tp_ext_atr_mult=3.0,
                        setup_quality=q,
                        entry_style="trend_continuation",
                        extension_score=ext,
                    ),
                    setup_quality=q,
                    entry_style="trend_continuation",
                    extension_score=ext,
                    quality_score=q,
                )
        return StrategySignal(
            symbol,
            self.name,
            "long",
            0.72,
            price,
            price * 0.97,
            price * 1.06,
            "Momentum and regime aligned (fallback: no klines).",
            regime,
            levels_from_structure=False,
        )


class BreakoutMomentumStrategy(BaseStrategy):
    name = "breakout_momentum"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime, klines_1h=None):
        if change_24h <= 6 or volume_24h <= 10_000_000:
            return None
        klines = klines_1h or []
        if len(klines) >= 15:
            atr = atr_mean_range(klines, 14)
            if atr and atr > 0:
                zl, zh, sl, tp, tp_ext, _ = structural_long_levels(
                    price,
                    atr,
                    sl_atr_mult=1.15,
                    tp_atr_mult=1.5,
                    tp_ext_atr_mult=2.25,
                    max_tp_pct=0.03,
                    max_tp_ext_pct=0.045,
                )
                q = quality_long_momentum(
                    regime=regime, change_24h=change_24h, price=price, klines=klines, atr=atr
                )
                if change_24h > 10:
                    q = min(0.95, q + 0.04)
                ext = extension_score_in_range(price, klines, lookback=10)
                if ext is not None and float(ext) > _STRATEGY_MAX_EXTENSION_FOR_ENTRY:
                    return None
                conf = min(0.82, 0.64 + (q - 0.5) * 0.35)
                rationale = (
                    f"Breakout expansion: vol+move; ATR zone, capped TP bands; quality={q:.2f}."
                )
                return StrategySignal(
                    symbol,
                    self.name,
                    "long",
                    conf,
                    price,
                    sl,
                    tp,
                    rationale,
                    regime,
                    entry_zone_low=zl,
                    entry_zone_high=zh,
                    take_profit_extended=tp_ext,
                    levels_from_structure=True,
                    atr_estimate_1h=atr,
                    structure_meta=_native_structure_meta(
                        atr,
                        "breakout_momentum",
                        entry_zone=[zl, zh],
                        sl_atr_mult=1.15,
                        tp_atr_mult=1.85,
                        tp_ext_atr_mult=2.8,
                        setup_quality=q,
                        entry_style="breakout_expansion",
                        extension_score=ext,
                    ),
                    setup_quality=q,
                    entry_style="breakout_expansion",
                    extension_score=ext,
                    quality_score=q,
                )
        return StrategySignal(
            symbol,
            self.name,
            "long",
            0.68,
            price,
            price * 0.975,
            price * 1.05,
            "Strong daily expansion (fallback: no klines).",
            regime,
            levels_from_structure=False,
        )


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime, klines_1h=None):
        if change_24h >= -6:
            return None
        klines = klines_1h or []
        if len(klines) >= 12:
            atr = atr_mean_range(klines, 14)
            if atr and atr > 0:
                sl = price - max(1.2 * atr, 0.012 * price)
                tp = price + min(1.5 * atr, 0.03 * price)
                z = 0.18 * atr
                zl, zh = price - z, price + z
                q = 0.55 + min(0.15, abs(change_24h) / 200.0)
                ext = extension_score_in_range(price, klines, lookback=10)
                return StrategySignal(
                    symbol,
                    self.name,
                    "long",
                    0.60,
                    price,
                    sl,
                    tp,
                    "Oversold MR: ATR-scaled SL/TP from 1h structure.",
                    regime,
                    entry_zone_low=zl,
                    entry_zone_high=zh,
                    take_profit_extended=price + min(2.0 * atr, 0.04 * price),
                    levels_from_structure=True,
                    atr_estimate_1h=atr,
                    structure_meta=_native_structure_meta(
                        atr,
                        "mean_reversion",
                        entry_zone=[zl, zh],
                        setup_quality=q,
                        entry_style="mean_reversion_long",
                        extension_score=ext,
                    ),
                    setup_quality=q,
                    entry_style="mean_reversion_long",
                    extension_score=ext,
                    quality_score=q,
                )
        return StrategySignal(
            symbol,
            self.name,
            "long",
            0.60,
            price,
            price * 0.96,
            price * 1.04,
            "Oversold daily move (fallback: no klines).",
            regime,
            levels_from_structure=False,
        )


class LiquiditySweepReversalStrategy(BaseStrategy):
    name = "liquidity_sweep_reversal"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime, klines_1h=None):
        if not (4 <= change_24h <= 10 and regime == "high_momentum"):
            return None
        klines = klines_1h or []
        if len(klines) >= 12:
            atr = atr_mean_range(klines, 14)
            if atr and atr > 0:
                sl = price + max(1.1 * atr, 0.01 * price)
                tp = price - min(1.9 * atr, 0.045 * price)
                z = 0.2 * atr
                zl, zh = price - z, price + z
                ext = extension_score_in_range(price, klines, lookback=10)
                sq = 0.52
                return StrategySignal(
                    symbol,
                    self.name,
                    "short",
                    0.55,
                    price,
                    sl,
                    tp,
                    "Sweep-reversal short: ATR-based risk and target.",
                    regime,
                    entry_zone_low=zl,
                    entry_zone_high=zh,
                    take_profit_extended=price - min(2.6 * atr, 0.055 * price),
                    levels_from_structure=True,
                    atr_estimate_1h=atr,
                    structure_meta=_native_structure_meta(
                        atr,
                        "liquidity_sweep_reversal",
                        entry_zone=[zl, zh],
                        setup_quality=sq,
                        entry_style="liquidity_sweep_short",
                        extension_score=ext,
                    ),
                    setup_quality=sq,
                    entry_style="liquidity_sweep_short",
                    extension_score=ext,
                    quality_score=sq,
                )
        return StrategySignal(
            symbol,
            self.name,
            "short",
            0.55,
            price,
            price * 1.03,
            price * 0.95,
            "Late-stage momentum sweep (fallback: no klines).",
            regime,
            levels_from_structure=False,
        )


_STRATEGY_CLASSES = {
    "trend_following": TrendFollowingStrategy,
    "breakout_momentum": BreakoutMomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "liquidity_sweep_reversal": LiquiditySweepReversalStrategy,
}


def build_strategy_set():
    return [
        TrendFollowingStrategy(),
        BreakoutMomentumStrategy(),
        MeanReversionStrategy(),
        LiquiditySweepReversalStrategy(),
    ]


def build_strategy_set_from_config(config: dict):
    """Build list of strategies from config (only enabled). config['strategies'][name]['enabled']."""
    strategies = config.get("strategies") or {}
    out = []
    for name, cls in _STRATEGY_CLASSES.items():
        if strategies.get(name, {}).get("enabled", True):
            out.append(cls())
    return out if out else build_strategy_set()
