"""Runtime strategy allowlist from config (no code edit) — complements dashboard single_strategy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CFG = _ROOT / "config" / "strategy_runtime_governance.v1.json"


def load_runtime_strategy_governance() -> dict[str, Any]:
    if not _CFG.exists():
        return {"version": 1, "enabled": False}
    try:
        return json.loads(_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "enabled": False}


def filter_strategy_objects(strategies: list[Any], cfg: dict[str, Any] | None = None) -> list[Any]:
    """
    Returns strategies allowed by governance.json.
    Precedence: mean_reversion_only > enabled_strategies > disabled_strategies.
    """
    cfg = cfg if cfg is not None else load_runtime_strategy_governance()
    if not cfg.get("enabled", False):
        return list(strategies)
    if cfg.get("mean_reversion_only"):
        return [s for s in strategies if getattr(s, "name", None) == "mean_reversion"]
    enabled = cfg.get("enabled_strategies")
    if isinstance(enabled, list) and enabled:
        allow = {str(x).strip() for x in enabled if x}
        return [s for s in strategies if getattr(s, "name", None) in allow]
    disabled = cfg.get("disabled_strategies") or []
    block = {str(x).strip() for x in disabled if x}
    return [s for s in strategies if getattr(s, "name", None) not in block]
