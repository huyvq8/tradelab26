"""
Promotion rules (v4): candidate config is promoted only if it beats active by rules.
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
RULES_PATH = _CONFIG_DIR / "promotion_rules.json"


def load_promotion_rules() -> dict:
    if RULES_PATH.exists():
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return {
        "min_profit_factor_improvement_pct": 8,
        "max_drawdown_increase_pct": 5,
        "min_trades_ratio": 0.5,
        "require_min_trades": 5,
    }


def check_promotion(
    metrics_active: dict,
    metrics_candidate: dict,
    rules: dict | None = None,
) -> tuple[bool, list[str]]:
    """
    Return (pass: bool, reasons: list of strings).
    Candidate must: PF improvement >= min_profit_factor_improvement_pct,
    drawdown increase <= max_drawdown_increase_pct, trades count >= min_trades_ratio * active.
    """
    if rules is None:
        rules = load_promotion_rules()
    reasons = []
    pf_a = metrics_active.get("profit_factor") or 0
    pf_c = metrics_candidate.get("profit_factor") or 0
    dd_a = metrics_active.get("max_drawdown_pct") or 0
    dd_c = metrics_candidate.get("max_drawdown_pct") or 0
    trades_a = metrics_active.get("total_trades") or 0
    trades_c = metrics_candidate.get("total_trades") or 0
    min_improve = rules.get("min_profit_factor_improvement_pct", 8) / 100.0
    max_dd_increase = rules.get("max_drawdown_increase_pct", 5) / 100.0
    min_ratio = rules.get("min_trades_ratio", 0.5)
    min_trades = rules.get("require_min_trades", 5)
    if pf_a <= 0:
        improvement_ok = pf_c > 0
    else:
        improvement_ok = pf_c >= pf_a * (1 + min_improve)
    if not improvement_ok:
        reasons.append(f"Profit factor candidate {pf_c:.2f} < active {pf_a:.2f} * (1+{min_improve:.0%})")
    dd_ok = (dd_c <= dd_a * (1 + max_dd_increase)) if dd_a > 0 else True
    if not dd_ok:
        reasons.append(f"Drawdown candidate {dd_c:.1f}% > active {dd_a:.1f}% * (1+{max_dd_increase:.0%})")
    trades_ok = trades_c >= max(min_trades, trades_a * min_ratio) if trades_a > 0 else trades_c >= min_trades
    if not trades_ok:
        reasons.append(f"Trades candidate {trades_c} < required (min {min_trades} or {min_ratio:.0%} of active)")
    pass_ = improvement_ok and dd_ok and trades_ok
    return pass_, reasons
