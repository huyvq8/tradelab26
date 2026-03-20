# Profit layer (v6): volatility guard, position sizing, strategy weight, allocation, expectancy

from core.profit.volatility_guard import (
    VolatilityGuardResult,
    check_volatility_guard,
    load_profit_config,
)
from core.profit.profit_config_resolve import load_profit_config_resolved
from core.profit.position_sizer import (
    apply_dynamic_sizing,
    get_confidence_multiplier,
    get_regime_score,
)
from core.profit.expectancy_engine import (
    compute_expectancy_map,
    get_expectancy_for_signal,
)
from core.profit.strategy_weight_engine import (
    compute_strategy_weights,
    get_strategy_weight,
)
from core.profit.allocation_engine import (
    AllocationResult,
    compute_allocation_mult,
)

__all__ = [
    "VolatilityGuardResult",
    "check_volatility_guard",
    "load_profit_config",
    "load_profit_config_resolved",
    "apply_dynamic_sizing",
    "get_confidence_multiplier",
    "get_regime_score",
    "compute_expectancy_map",
    "get_expectancy_for_signal",
    "compute_strategy_weights",
    "get_strategy_weight",
    "AllocationResult",
    "compute_allocation_mult",
]
