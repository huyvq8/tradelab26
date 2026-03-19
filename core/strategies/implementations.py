from core.strategies.base import BaseStrategy, StrategySignal


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime):
        if regime == "high_momentum" and change_24h > 3:
            return StrategySignal(
                symbol,
                self.name,
                "long",
                0.72,
                price,
                price * 0.97,
                price * 1.06,
                "Momentum and regime aligned for continuation.",
                regime,
            )
        return None


class BreakoutMomentumStrategy(BaseStrategy):
    name = "breakout_momentum"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime):
        if change_24h > 6 and volume_24h > 10_000_000:
            return StrategySignal(
                symbol,
                self.name,
                "long",
                0.68,
                price,
                price * 0.975,
                price * 1.05,
                "Strong daily expansion with above-threshold volume.",
                regime,
            )
        return None


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime):
        if change_24h < -6:
            return StrategySignal(
                symbol,
                self.name,
                "long",
                0.60,
                price,
                price * 0.96,
                price * 1.04,
                "Oversold daily move suitable for paper mean-reversion test.",
                regime,
            )
        return None


class LiquiditySweepReversalStrategy(BaseStrategy):
    name = "liquidity_sweep_reversal"

    def evaluate(self, symbol, price, change_24h, volume_24h, regime):
        if 4 <= change_24h <= 10 and regime == "high_momentum":
            return StrategySignal(
                symbol,
                self.name,
                "short",
                0.55,
                price,
                price * 1.03,
                price * 0.95,
                "Late-stage momentum may produce a sweep-and-reversal setup.",
                regime,
            )
        return None


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
