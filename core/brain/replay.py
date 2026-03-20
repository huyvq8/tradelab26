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


def replay_proposal_vs_baseline(
    baseline_metrics: dict[str, Any],
    proposal_public_id: str | None = None,
    proposed_metrics: dict[str, Any] | None = None,
    *,
    regime: str | None = None,
    cluster: str | None = None,
    fee_slippage_bps: float = 0.0,
    drawdown_penalty_weight: float = 0.0,
) -> dict[str, Any]:
    """
    Compare hypothetical proposal run vs baseline (caller supplies metrics dicts).
    Extend later with full bar replay + fee/DD models; format is stable for dashboard/API.
    """
    prop = proposed_metrics or {}
    base = baseline_metrics or {}
    keys = set(base.keys()) | set(prop.keys())
    delta: dict[str, Any] = {}
    for k in keys:
        if isinstance(base.get(k), (int, float)) and isinstance(prop.get(k), (int, float)):
            delta[k] = float(prop[k]) - float(base[k])
    dd_pen = drawdown_penalty_weight * float(prop.get("max_drawdown_pct", 0) or 0)
    fee_adj = fee_slippage_bps / 10_000.0 * float(prop.get("turnover_notional", 0) or 0)
    return {
        "proposal_public_id": proposal_public_id,
        "regime_filter": regime,
        "cluster_filter": cluster,
        "fee_slippage_bps": fee_slippage_bps,
        "drawdown_penalty_applied": dd_pen,
        "fee_cost_estimate_usd": fee_adj,
        "baseline": base,
        "proposed": prop,
        "delta": delta,
        "note": "Pass metrics from your simulation harness; wire to SQL replay in a follow-up.",
    }
