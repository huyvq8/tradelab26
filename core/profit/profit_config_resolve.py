"""
Merge `profit.active.json` + optional MR guardrail profile (MR_SAFE / MR_BALANCED).

Tách riêng khỏi `volatility_guard` để import ổn định (dashboard / cycle).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_profit_config_resolved() -> dict:
    """
    Same as load_profit_config, then merge optional guardrail profile.

    Profile: `entry_guardrail_profile` in profit.active.json, or env ENTRY_GUARDRAIL_PROFILE.
    File: config/mr_guardrail_profiles.v1.json — keys are profile names, values are deep-merge patches.
    """
    # Lazy import: avoids circular import with volatility_guard
    from core.profit.volatility_guard import load_profit_config

    base = load_profit_config()
    profile = str(base.get("entry_guardrail_profile") or "").strip()
    if not profile:
        profile = str(os.environ.get("ENTRY_GUARDRAIL_PROFILE") or "").strip()
    if not profile:
        return base
    path = _PROJECT_ROOT / "config" / "mr_guardrail_profiles.v1.json"
    if not path.exists():
        return base
    try:
        profiles = json.loads(path.read_text(encoding="utf-8"))
        patch = profiles.get(profile)
        if not isinstance(patch, dict):
            return base
        from core.experiments.merge_config import deep_merge

        return deep_merge(base, patch)
    except Exception:
        return base
