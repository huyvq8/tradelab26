"""Merge active DB runtime overrides onto file-based config (never mutates repo JSON)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.brain.p2_models import RuntimeConfigOverride


def deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def merge_config_with_active_overrides(db: Session, target_config_name: str, base: dict[str, Any]) -> dict[str, Any]:
    now = datetime.utcnow()
    rows = list(
        db.scalars(
            select(RuntimeConfigOverride).where(
                RuntimeConfigOverride.target_config_name == target_config_name,
                RuntimeConfigOverride.status == "active",
                or_(RuntimeConfigOverride.expires_at.is_(None), RuntimeConfigOverride.expires_at > now),
            )
        )
    )
    out = dict(base)
    for row in rows:
        try:
            patch = json.loads(row.merge_patch_json or "{}")
            if patch:
                out = deep_merge_dict(out, patch)
        except Exception:
            continue
    return out


def expire_stale_runtime_overrides(db: Session, *, now: datetime | None = None) -> int:
    """Mark active overrides past expires_at as expired. Returns rows updated."""
    t = now or datetime.utcnow()
    rows = list(
        db.scalars(
            select(RuntimeConfigOverride).where(
                RuntimeConfigOverride.status == "active",
                RuntimeConfigOverride.expires_at.is_not(None),
                RuntimeConfigOverride.expires_at < t,
            )
        )
    )
    for r in rows:
        r.status = "expired"
    return len(rows)


def list_active_overrides(db: Session, target_config_name: str | None = None) -> list[RuntimeConfigOverride]:
    now = datetime.utcnow()
    q = select(RuntimeConfigOverride).where(
        RuntimeConfigOverride.status == "active",
        or_(RuntimeConfigOverride.expires_at.is_(None), RuntimeConfigOverride.expires_at > now),
    )
    if target_config_name:
        q = q.where(RuntimeConfigOverride.target_config_name == target_config_name)
    return list(db.scalars(q))
