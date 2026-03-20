"""Thesis profile resolution from signal + position; attach at open."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.portfolio.models import Position
from core.strategies.base import StrategySignal

_ROOT = Path(__file__).resolve().parents[2]


def _load_thesis_management_file() -> dict[str, Any]:
    p = _ROOT / "config" / "thesis_management.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "thesis_management.v1.example.json"
    if not p.exists():
        return {"enabled": False, "version": "0"}
    return json.loads(p.read_text(encoding="utf-8"))


def load_thesis_management_config(db=None) -> dict[str, Any]:
    """
    File baseline; when `db` is set, deep-merge active `RuntimeConfigOverride` rows
    for `thesis_management.v1` (never writes config/*.json).
    """
    base = _load_thesis_management_file()
    if db is None:
        return base
    try:
        from core.brain.runtime_overrides import merge_config_with_active_overrides

        return merge_config_with_active_overrides(db, "thesis_management.v1", base)
    except Exception:
        return base


def _profile_key_for_signal(signal: StrategySignal) -> str:
    cfg = _load_thesis_management_file()
    em = cfg.get("entry_style_to_profile") or {}
    style = (getattr(signal, "entry_style", None) or "").strip().lower()
    if style and style in em:
        return str(em[style])
    sn = (signal.strategy_name or "").lower()
    if "short" in sn or sn.endswith("_short"):
        return "mean_reversion"
    if "momentum" in sn or "breakout" in sn:
        return "breakout_continuation"
    return "default"


def resolve_thesis_from_signal(signal: StrategySignal) -> dict[str, Any]:
    profile_key = _profile_key_for_signal(signal)
    meta = {
        "profile_key": profile_key,
        "entry_style": getattr(signal, "entry_style", None),
        "strategy_name": signal.strategy_name,
        "regime_at_entry": getattr(signal, "regime", None),
    }
    thesis_type = profile_key if profile_key != "default" else "generic"
    return {
        "thesis_type": thesis_type,
        "version": str(_load_thesis_management_file().get("version", "1")),
        "metadata": meta,
    }


def apply_thesis_fields_to_position(position: Position, signal: StrategySignal) -> None:
    r = resolve_thesis_from_signal(signal)
    position.thesis_type = r["thesis_type"]
    position.thesis_version = r["version"]
    position.thesis_metadata_json = json.dumps(r["metadata"], ensure_ascii=False)
    position.thesis_state = "NORMAL"
    position.thesis_warning_count = 0
    position.thesis_danger_count = 0
    position.thesis_last_score = 1.0
    position.thesis_last_reason = "open"
    position.zone_shift_risk_score = 0.0
    position.zone_shift_risk_level = "low"


def ensure_thesis_defaults_for_position(position: Position) -> None:
    """Backfill for legacy rows missing thesis fields."""
    if not getattr(position, "thesis_type", None):
        position.thesis_type = "generic"
    if not getattr(position, "thesis_version", None):
        position.thesis_version = "1"
    raw = getattr(position, "thesis_metadata_json", None)
    if not raw or raw == "{}":
        position.thesis_metadata_json = json.dumps(
            {"profile_key": "default", "backfilled": True}, ensure_ascii=False
        )
    st = getattr(position, "thesis_state", None) or ""
    if not st:
        position.thesis_state = "NORMAL"
