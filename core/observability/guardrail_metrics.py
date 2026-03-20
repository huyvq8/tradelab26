"""Aggregate guardrail effectiveness from decision_log (+ optional DB trades for fast stopout)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_PATH = _ROOT / "data" / "decision_log.jsonl"


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        if ts.endswith("Z"):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _iter_log_rows() -> list[dict[str, Any]]:
    if not _LOG_PATH.exists():
        return []
    try:
        raw = _LOG_PATH.read_text(encoding="utf-8")
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def compute_guardrail_metrics(
    *,
    hours_24: bool = True,
    hours_168: bool = True,
    db_session: Any | None = None,
    portfolio_id: int | None = None,
    fast_stop_minutes: float = 15.0,
) -> dict[str, Any]:
    """
    Metrics from decision_log:
    - blocked by stop tight, MR no reversal, notional cap
    - avg notional % / stop % on entry_opened
    Fast stopout rates: if db_session + portfolio_id, split trades closed after guardrails rollout heuristic
    (uses journal mistake_tags / hold time — baseline None if insufficient data).
    """
    now = datetime.now(timezone.utc)
    cut24 = now - timedelta(hours=24)
    cut7d = now - timedelta(days=7)
    rows = _iter_log_rows()

    def window(cut: datetime) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            dt = _parse_ts(str(r.get("ts") or ""))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cut:
                out.append(r)
        return out

    def agg(cut: datetime) -> dict[str, Any]:
        w = window(cut)
        blocked_stop_tight = 0
        blocked_mr_rev = 0
        capped_notional = 0
        notional_pcts: list[float] = []
        stop_pcts: list[float] = []
        for r in w:
            ev = str(r.get("event") or "")
            rc = str(r.get("reason_code") or "")
            pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            if ev == "entry_rejected":
                if rc == "STOP_DISTANCE_TOO_TIGHT":
                    blocked_stop_tight += 1
                elif rc == "MR_NO_REVERSAL_CONFIRMATION":
                    blocked_mr_rev += 1
            if ev == "entry_adjusted" and rc == "NOTIONAL_CAPPED_BY_POLICY":
                capped_notional += 1
            if ev == "entry_opened":
                fn = pl.get("final_notional_pct_of_equity")
                sd = pl.get("stop_distance_pct")
                if isinstance(fn, (int, float)):
                    notional_pcts.append(float(fn))
                if isinstance(sd, (int, float)):
                    stop_pcts.append(float(sd))
        return {
            "total_signals_blocked_by_stop_tight": blocked_stop_tight,
            "total_signals_blocked_by_no_reversal_confirmation": blocked_mr_rev,
            "total_signals_capped_by_notional_policy": capped_notional,
            "avg_notional_pct_opened": round(sum(notional_pcts) / len(notional_pcts), 6) if notional_pcts else None,
            "avg_stop_distance_pct_opened": round(sum(stop_pcts) / len(stop_pcts), 6) if stop_pcts else None,
            "opened_count_in_window": len(
                [r for r in w if str(r.get("event") or "") == "entry_opened"]
            ),
        }

    out: dict[str, Any] = {
        "fast_stopout_rate_before_guardrails": None,
        "fast_stopout_rate_after_guardrails": None,
        "baseline_note": "Cần mốc thời gian rollout hoặc flag experiment để tách before/after; hiện chỉ có proxy aggregate trong fast_stopout_from_db.",
    }
    if hours_24:
        out["last_24h"] = agg(cut24)
    if hours_168:
        out["last_7d"] = agg(cut7d)

    out["fast_stopout_from_db"] = None
    if db_session is not None and portfolio_id is not None:
        try:
            from sqlalchemy import select
            from core.portfolio.models import Position, Trade

            db = db_session
            closes = list(
                db.scalars(
                    select(Trade).where(
                        Trade.portfolio_id == portfolio_id,
                        Trade.action == "close",
                    )
                )
            )
            fast = 0
            total = 0
            for ct in closes[-500:]:
                total += 1
                op = db.scalar(
                    select(Trade).where(
                        Trade.position_id == ct.position_id,
                        Trade.action == "open",
                    )
                )
                pos = db.get(Position, ct.position_id) if ct.position_id else None
                oa = getattr(op, "created_at", None) if op else None
                ca = getattr(ct, "created_at", None)
                hold_m = None
                if oa and ca and hasattr(ca - oa, "total_seconds"):
                    try:
                        hold_m = (ca - oa).total_seconds() / 60.0
                    except Exception:
                        hold_m = None
                if hold_m is not None and hold_m < fast_stop_minutes and (
                    (ct.close_source or "") == "sl_hit" or "sl" in (ct.note or "").lower()
                ):
                    fast += 1
            out["fast_stopout_from_db"] = {
                "sample_closes": total,
                "fast_stopout_count": fast,
                "fast_stopout_rate": round(fast / total, 4) if total else None,
                "note": "proxy: SL-ish close within fast_stop_minutes of open",
                "fast_stop_minutes": fast_stop_minutes,
            }
        except Exception as e:
            out["fast_stopout_from_db"] = {"error": str(e)}

    out["mr_report"] = compute_mr_guardrail_report(rows=rows, cut=cut24)
    return out


def compute_mr_guardrail_report(
    *,
    rows: list[dict[str, Any]] | None = None,
    cut: datetime | None = None,
) -> dict[str, Any]:
    """Mean reversion–specific counts from decision_log (same window as cut)."""
    data = rows if rows is not None else _iter_log_rows()
    if cut is not None:
        filt: list[dict[str, Any]] = []
        for r in data:
            dt = _parse_ts(str(r.get("ts") or ""))
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cut:
                filt.append(r)
        data = filt

    mr_fires = 0
    mr_blocked_rev = 0
    mr_floor_adj = 0
    mr_capped = 0
    for r in data:
        strat = str(r.get("strategy_name") or "")
        if strat != "mean_reversion":
            continue
        ev = str(r.get("event") or "")
        rc = str(r.get("reason_code") or "")
        if ev == "entry_opened":
            mr_fires += 1
        if ev == "entry_rejected" and rc == "MR_NO_REVERSAL_CONFIRMATION":
            mr_blocked_rev += 1
        if ev == "entry_adjusted" and rc == "STOP_DISTANCE_FLOOR_APPLIED" and strat == "mean_reversion":
            mr_floor_adj += 1
        if ev == "entry_adjusted" and rc == "NOTIONAL_CAPPED_BY_POLICY":
            if strat == "mean_reversion":
                mr_capped += 1

    return {
        "mean_reversion_entry_opened": mr_fires,
        "blocked_no_reversal_confirmation": mr_blocked_rev,
        "stop_floor_adjust_events": mr_floor_adj,
        "notional_cap_events": mr_capped,
        "win_rate_by_guardrail_note": "Cần nối journal/DB closed trades + guardrail_snapshot để win rate theo bucket",
    }
