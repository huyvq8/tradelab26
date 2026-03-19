"""
Optimizer Agent (v4): apply suggested_actions from Reflection to strategy.candidate.json.
Never writes to strategy.active.json — only candidate. Promotion is separate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
STRATEGY_ACTIVE = _CONFIG_DIR / "strategy.active.json"
STRATEGY_CANDIDATE = _CONFIG_DIR / "strategy.candidate.json"
PROFIT_ACTIVE = _CONFIG_DIR / "profit.active.json"
PROFIT_CANDIDATE = _CONFIG_DIR / "profit.candidate.json"


def _load_candidate() -> dict:
    if STRATEGY_CANDIDATE.exists():
        return json.loads(STRATEGY_CANDIDATE.read_text(encoding="utf-8"))
    if STRATEGY_ACTIVE.exists():
        return json.loads(STRATEGY_ACTIVE.read_text(encoding="utf-8"))
    return {
        "strategies": {
            "trend_following": {"enabled": True},
            "breakout_momentum": {"enabled": True},
            "mean_reversion": {"enabled": True},
            "liquidity_sweep_reversal": {"enabled": True},
        },
        "params": {},
        "disabled_under_regime": {},
    }


def _save_candidate(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_CANDIDATE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def apply_suggested_actions_to_candidate(suggested_actions: list[dict]) -> int:
    """
    Apply list of { type, strategy?, regime?, value? } to strategy.candidate.json.
    Returns number of actions applied.
    """
    if not suggested_actions:
        return 0
    data = _load_candidate()
    if "strategies" not in data:
        data["strategies"] = {}
    if "disabled_under_regime" not in data:
        data["disabled_under_regime"] = {}
    if "params" not in data:
        data["params"] = {}
    applied = 0
    for action in suggested_actions:
        if not isinstance(action, dict):
            continue
        typ = action.get("type") or ""
        strategy = action.get("strategy")
        regime = action.get("regime")
        value = action.get("value")
        if typ == "disable_strategy" and strategy:
            if strategy in data["strategies"]:
                data["strategies"][strategy]["enabled"] = False
                applied += 1
        elif typ == "enable_strategy" and strategy:
            if strategy in data["strategies"]:
                data["strategies"][strategy]["enabled"] = True
                applied += 1
        elif typ == "disable_strategy_under_regime" and strategy and regime:
            key = strategy
            if key not in data["disabled_under_regime"]:
                data["disabled_under_regime"][key] = []
            if regime not in data["disabled_under_regime"][key]:
                data["disabled_under_regime"][key].append(regime)
                applied += 1
        elif typ == "enable_strategy_under_regime" and strategy and regime:
            key = strategy
            if key in data["disabled_under_regime"] and regime in data["disabled_under_regime"][key]:
                data["disabled_under_regime"][key].remove(regime)
                if not data["disabled_under_regime"][key]:
                    del data["disabled_under_regime"][key]
                applied += 1
        elif typ == "increase_min_volume_ratio" and strategy and value is not None:
            if strategy not in data["params"]:
                data["params"][strategy] = {}
            data["params"][strategy]["min_volume_ratio"] = float(value)
            applied += 1
        elif typ == "increase_min_confidence" and strategy and value is not None:
            if strategy not in data["params"]:
                data["params"][strategy] = {}
            data["params"][strategy]["min_confidence"] = float(value)
            applied += 1
        elif typ == "increase_sl_atr_multiplier" and strategy and value is not None:
            if strategy not in data["params"]:
                data["params"][strategy] = {}
            data["params"][strategy]["sl_atr_multiplier"] = float(value)
            applied += 1
        elif typ == "decrease_tp_rr" and strategy and value is not None:
            if strategy not in data["params"]:
                data["params"][strategy] = {}
            data["params"][strategy]["max_tp_rr"] = float(value)
            applied += 1
    if applied > 0:
        _save_candidate(data)
    profit_applied = _apply_profit_candidate_actions(suggested_actions)
    return applied + profit_applied


def _load_profit_candidate() -> dict:
    if PROFIT_CANDIDATE.exists():
        try:
            return json.loads(PROFIT_CANDIDATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if PROFIT_ACTIVE.exists():
        try:
            return json.loads(PROFIT_ACTIVE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_profit_candidate(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFIT_CANDIDATE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _apply_profit_candidate_actions(suggested_actions: list[dict]) -> int:
    """
    Phase 4 v6: Apply profit-related actions to profit.candidate.json.
    Types: reduce_risk_per_trade, reduce_weight_under_regime.
    """
    applied = 0
    for action in suggested_actions:
        if not isinstance(action, dict):
            continue
        typ = action.get("type") or ""
        strategy = action.get("strategy")
        regime = action.get("regime")
        value = action.get("value")
        if typ == "reduce_risk_per_trade" and value is not None:
            data = _load_profit_candidate()
            if "sizing" not in data:
                data["sizing"] = {}
            data["sizing"]["base_risk_pct"] = float(value)
            _save_profit_candidate(data)
            applied += 1
        elif typ == "reduce_weight_under_regime" and strategy and regime and value is not None:
            data = _load_profit_candidate()
            if "weight_under_regime" not in data:
                data["weight_under_regime"] = {}
            key = f"{strategy}.{regime}"
            data["weight_under_regime"][key] = float(value)
            _save_profit_candidate(data)
            applied += 1
    return applied


def get_active_strategy_config() -> dict:
    """Load strategy.active.json; fallback to default."""
    if STRATEGY_ACTIVE.exists():
        return json.loads(STRATEGY_ACTIVE.read_text(encoding="utf-8"))
    return _load_candidate()


def get_candidate_strategy_config() -> dict | None:
    """Load strategy.candidate.json if exists."""
    if STRATEGY_CANDIDATE.exists():
        return json.loads(STRATEGY_CANDIDATE.read_text(encoding="utf-8"))
    return None
