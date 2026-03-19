"""Replay / audit: compare persisted change-point scores vs fresh recompute (best-effort)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.brain.change_point import compute_change_point_for_symbol
from core.brain.persistence import fetch_cycle_bundle
from core.brain.runtime_state import load_runtime_state
from core.market_data.client import get_klines_1h


def replay_cycle_change_points(db: Session, cycle_id: str) -> dict[str, Any]:
    bundle = fetch_cycle_bundle(db, cycle_id)
    if bundle.get("error"):
        return bundle
    rt = load_runtime_state()
    btc_reg = str(rt.last_btc_regime or "balanced")
    rows_out: list[dict[str, Any]] = []
    for r in bundle.get("change_point_events") or []:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        try:
            kl = get_klines_1h(sym, limit=25)
        except Exception:
            kl = []
        cp = compute_change_point_for_symbol(
            kl,
            "long",
            prev_btc_regime=btc_reg,
            curr_btc_regime=btc_reg,
            funding_rate=None,
        )
        stored = float(r.get("change_point_score") or 0)
        rec = float(cp.change_point_score)
        rows_out.append(
            {
                "symbol": sym,
                "stored_score": stored,
                "recomputed_score": rec,
                "abs_delta": abs(stored - rec),
            }
        )
    return {"cycle_id": cycle_id, "change_point_compare": rows_out}
