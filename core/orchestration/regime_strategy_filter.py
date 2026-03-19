"""Uu tien / tat strategy theo regime. Config: config/regime_strategy.v1.json"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.strategies.base import BaseStrategy

_LOG = logging.getLogger(__name__)
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _default_cfg() -> dict[str, Any]:
    return {
        "enabled": False,
        "disable_in_regime": {
            "high_momentum": ["mean_reversion"],
        },
        "evaluate_order": {
            "high_momentum": [
                "breakout_momentum",
                "trend_following",
                "liquidity_sweep_reversal",
                "mean_reversion",
            ],
        },
    }


def load_regime_strategy_config() -> dict[str, Any]:
    cfg = _default_cfg()
    path = _CONFIG_DIR / "regime_strategy.v1.json"
    if not path.exists():
        path = _CONFIG_DIR / "regime_strategy.v1.example.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except Exception as e:
            _LOG.warning("regime_strategy: cannot read %s: %s", path, e)
    return cfg


def filter_and_order_strategies(
    strategies: list[BaseStrategy],
    regime: str,
    config: dict[str, Any],
) -> list[BaseStrategy]:
    if not config.get("enabled"):
        return strategies
    reg = (regime or "").strip().lower()
    disabled = config.get("disable_in_regime") or {}
    if isinstance(disabled, dict):
        ban = {str(x).strip().lower() for x in (disabled.get(reg) or [])}
    else:
        ban = set()
    kept = [s for s in strategies if (s.name or "").strip().lower() not in ban]
    order_map = config.get("evaluate_order") or {}
    if not isinstance(order_map, dict) or reg not in order_map:
        return kept
    order_list = [str(x).strip().lower() for x in (order_map.get(reg) or [])]
    rank = {n: i for i, n in enumerate(order_list)}

    def sort_key(s: BaseStrategy) -> tuple[int, str]:
        n = (s.name or "").strip().lower()
        return (rank.get(n, 999), n)

    return sorted(kept, key=sort_key)
