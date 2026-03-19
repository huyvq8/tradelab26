"""
Resolve config paths for A/B experiments (env overrides).
- ENTRY_TIMING_CONFIG — path to entry timing JSON (relative to project root or absolute)
- ENTRY_CONTEXT_GATES_CONFIG — optional JSON merged over entry_context_gates.v1.json
- PROFIT_ACTIVE_OVERLAY — path to partial JSON merged into profit.active.json
- EDGE_EXPERIMENT — label stored in logs (default: default)
- EDGE_SESSION — optional session id (e.g. paper run id)
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent


def _project_path(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs.strip())
    if p.is_absolute():
        return p
    return _ROOT / p


def resolved_entry_timing_config_path() -> Path:
    env = os.environ.get("ENTRY_TIMING_CONFIG", "").strip()
    if env:
        p = _project_path(env)
        if p.exists():
            return p
    return _ROOT / "config" / "entry_timing.v1.json"


def resolved_profit_overlay_path() -> Path | None:
    env = os.environ.get("PROFIT_ACTIVE_OVERLAY", "").strip()
    if not env:
        return None
    p = _project_path(env)
    return p if p.exists() else None


def resolved_entry_context_gates_path() -> Path | None:
    env = os.environ.get("ENTRY_CONTEXT_GATES_CONFIG", "").strip()
    if not env:
        return None
    p = _project_path(env)
    return p if p.exists() else None


def experiment_labels() -> dict[str, str]:
    return {
        "experiment_id": (os.environ.get("EDGE_EXPERIMENT") or "default").strip() or "default",
        "session_id": (os.environ.get("EDGE_SESSION") or "").strip(),
        "entry_timing_path": str(resolved_entry_timing_config_path()),
        "profit_overlay": str(resolved_profit_overlay_path() or ""),
        "entry_context_gates_path": str(resolved_entry_context_gates_path() or ""),
    }
