"""Strategy policy per token_type: allowed/banned strategies, shortability, hedge_policy, risk_profile."""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_ROUTING = {
    "major": {"allowed_strategies": [], "banned_strategies": [], "shortability": "restricted", "hedge_policy": "allowed", "risk_profile": {}},
    "large_cap_alt": {"allowed_strategies": [], "banned_strategies": [], "shortability": "allowed", "hedge_policy": "restricted", "risk_profile": {}},
    "mid_cap_alt": {"allowed_strategies": [], "banned_strategies": [], "shortability": "allowed", "hedge_policy": "restricted", "risk_profile": {}},
    "low_cap": {"allowed_strategies": [], "banned_strategies": [], "shortability": "restricted", "hedge_policy": "disabled", "risk_profile": {}},
    "meme": {"allowed_strategies": [], "banned_strategies": [], "shortability": "disabled", "hedge_policy": "disabled", "risk_profile": {}},
    "narrative": {"allowed_strategies": [], "banned_strategies": [], "shortability": "allowed", "hedge_policy": "restricted", "risk_profile": {}},
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUTING_PATH = _PROJECT_ROOT / "config" / "strategy_routing.v1.json"


def load_routing_config() -> dict:
    if _ROUTING_PATH.exists():
        try:
            return json.loads(_ROUTING_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _DEFAULT_ROUTING.copy()


def get_policy_for_token_type(token_type: str, routing_config: dict | None = None) -> dict:
    """Return policy dict: allowed_strategies, banned_strategies, shortability, hedge_policy, short_min_score_override, risk_profile."""
    cfg = routing_config if routing_config is not None else load_routing_config()
    policy = cfg.get(token_type) or _DEFAULT_ROUTING.get(token_type) or _DEFAULT_ROUTING["mid_cap_alt"]
    return {
        "allowed_strategies": list(policy.get("allowed_strategies", [])),
        "banned_strategies": list(policy.get("banned_strategies", [])),
        "shortability": policy.get("shortability", "allowed"),
        "hedge_policy": policy.get("hedge_policy", "restricted"),
        "short_min_score_override": policy.get("short_min_score_override"),
        "risk_profile": dict(policy.get("risk_profile", {})),
    }
