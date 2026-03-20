"""Flatten guardrail fields for dashboard tables and learning exports."""
from __future__ import annotations

from typing import Any


def _yn(b: bool) -> str:
    return "yes" if b else "no"


def row_from_decision_event(r: dict[str, Any]) -> dict[str, Any]:
    """Build summary columns from decision_log row (entry_opened / entry_rejected / entry_adjusted)."""
    pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
    ev = str(r.get("event") or "")
    rc = str(r.get("reason_code") or "")
    row: dict[str, Any] = {
        "ts": r.get("ts"),
        "event": ev,
        "symbol": r.get("symbol"),
        "strategy": r.get("strategy_name"),
        "reason_code": rc,
        "stop_distance_pct": pl.get("stop_distance_pct"),
        "final_notional_pct_of_equity": pl.get("final_notional_pct_of_equity"),
        "risk_efficiency_ratio": pl.get("risk_efficiency_ratio"),
        "stop_floor_applied": _yn(bool(pl.get("stop_floor_applied"))),
        "notional_cap_applied": _yn(bool((pl.get("notional_cap") or {}).get("applied"))),
        "mr_reversal_confirmation": _mr_rev_from_payload(pl, rc),
        "rejection_stage": pl.get("blocking_stage") or rc or "",
    }
    if ev == "entry_adjusted" and rc == "STOP_DISTANCE_FLOOR_APPLIED":
        row["stop_floor_applied"] = "yes"
    if ev == "entry_adjusted" and rc == "NOTIONAL_CAPPED_BY_POLICY":
        row["notional_cap_applied"] = "yes"
    return row


def _mr_rev_from_payload(pl: dict[str, Any], reason_code: str) -> str:
    if reason_code == "MR_NO_REVERSAL_CONFIRMATION":
        return "no"
    rd = pl.get("reversal_diagnostics")
    if isinstance(rd, dict) and rd.get("flags"):
        fl = rd["flags"]
        if isinstance(fl, dict) and any(fl.values()):
            return "yes"
    if pl.get("mr_reversal_passed") is True:
        return "yes"
    return "n/a"


def dashboard_candidate_guardrail_row(
    *,
    sig: Any,
    regime: str,
    klines_1h: list,
    guard_cfg: dict[str, Any],
    equity_usd: float,
) -> dict[str, Any]:
    """Dry-run guardrails for dashboard candidate table (no mutation of sig)."""
    from copy import deepcopy

    from core.risk.entry_guardrails import (
        evaluate_stop_floor_r_guard,
        mr_long_has_reversal_confirmation,
        resolve_notional_cap_usd,
    )
    from core.risk.trade_r_metrics import planned_r_multiple

    entry = float(getattr(sig, "entry_price", 0) or 0)
    sl = float(getattr(sig, "stop_loss", 0) or 0)
    side = str(getattr(sig, "side", "") or "")
    strat = str(getattr(sig, "strategy_name", "") or "")
    stop_pct = abs(entry - sl) / max(entry, 1e-9) if entry > 0 and sl > 0 else None
    s_copy = deepcopy(sig)
    sg = guard_cfg if isinstance(guard_cfg, dict) else {}
    min_r = 0.8
    try:
        from core.risk.candidate_quality import load_candidate_quality_config

        min_r = float(load_candidate_quality_config().get("min_candidate_r_multiple", 0.8) or 0.8)
    except Exception:
        pass
    floor_eval = evaluate_stop_floor_r_guard(
        s_copy,
        guard_cfg=sg,
        regime=str(regime or ""),
        min_candidate_r=min_r,
    )
    stop_after = (
        abs(float(s_copy.entry_price) - float(s_copy.stop_loss)) / max(float(s_copy.entry_price), 1e-9)
        if entry > 0
        else None
    )
    mr_ok, mr_diag = (
        mr_long_has_reversal_confirmation(klines_1h, cfg=(sg.get("mr_reversal_confirmation") or {}))
        if strat == "mean_reversion" and side == "long"
        else (True, {})
    )
    cap_usd = resolve_notional_cap_usd(
        guard_cfg=sg,
        strategy_name=strat,
        regime=str(regime or ""),
        equity_usd=float(equity_usd or 0),
    )
    cap_pct = (cap_usd / max(equity_usd, 1e-9)) if cap_usd and equity_usd > 0 else None
    risk_ceiling = None
    eff_ratio = None
    notional_pct = None
    cap_would_apply = "no"
    if entry > 0 and stop_after and stop_after > 0 and equity_usd > 0:
        try:
            from core.config import settings

            rp = float(getattr(settings, "default_risk_pct", 0.01) or 0.01)
            risk_ceiling = min(equity_usd, equity_usd * rp / stop_after)
            if risk_ceiling and risk_ceiling > 0 and cap_usd:
                final_est = min(risk_ceiling, cap_usd)
                eff_ratio = round(final_est / risk_ceiling, 4)
                notional_pct = round(final_est / equity_usd, 4)
                if float(cap_usd) + 1e-9 < float(risk_ceiling):
                    cap_would_apply = "yes"
        except Exception:
            pass
    pr = planned_r_multiple(sig)
    stage = "strategy_candidate"
    if strat == "mean_reversion" and side == "long" and not mr_ok:
        stage = "would_reject_mr_no_reversal"
    elif floor_eval.get("reject_low_r_after_floor"):
        stage = "would_reject_stop_floor_planned_r"
    row = {
        "stop_distance_pct": round(stop_pct, 6) if stop_pct is not None else None,
        "stop_distance_pct_after_floor": round(stop_after, 6) if stop_after is not None else None,
        "stop_floor_applied": "yes" if floor_eval.get("stop_floor_applied") else "no",
        "planned_r_preview": round(float(pr), 4) if pr is not None else None,
        "mr_reversal_confirmation": "yes" if mr_ok else "no",
        "max_notional_pct_cap": round(cap_pct, 4) if cap_pct is not None else None,
        "risk_efficiency_ratio_preview": eff_ratio,
        "final_notional_pct_preview": notional_pct,
        "rejection_stage": stage,
        "notional_cap_would_apply": cap_would_apply,
    }
    if isinstance(mr_diag, dict) and mr_diag.get("flags"):
        row["mr_reversal_flags"] = mr_diag.get("flags")
    return row
