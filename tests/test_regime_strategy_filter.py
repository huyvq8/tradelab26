from core.orchestration.regime_strategy_filter import filter_and_order_strategies
from core.strategies.base import BaseStrategy


class _S(BaseStrategy):
    def __init__(self, name: str):
        self.name = name

    def evaluate(self, *a, **k):
        return None


def test_disable_mean_reversion_in_high_momentum():
    s = [_S("mean_reversion"), _S("breakout_momentum")]
    cfg = {
        "enabled": True,
        "disable_in_regime": {"high_momentum": ["mean_reversion"]},
        "evaluate_order": {},
    }
    out = filter_and_order_strategies(s, "high_momentum", cfg)
    assert [x.name for x in out] == ["breakout_momentum"]


def test_evaluate_order():
    s = [_S("trend_following"), _S("breakout_momentum")]
    cfg = {
        "enabled": True,
        "disable_in_regime": {},
        "evaluate_order": {"high_momentum": ["breakout_momentum", "trend_following"]},
    }
    out = filter_and_order_strategies(s, "high_momentum", cfg)
    assert [x.name for x in out] == ["breakout_momentum", "trend_following"]
