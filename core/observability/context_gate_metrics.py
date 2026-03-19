"""Metrics for entry context gates from decision_log rows (validate / dashboard)."""
from __future__ import annotations

from collections import Counter

CONTEXT_GATE_REASON_PREFIX = "CONTEXT_GATE"


def analyze_entry_context_gates(rows: list[dict]) -> dict:
    """
    Effectiveness: entry_rejected with reason_code starting CONTEXT_GATE_*.
    Filter cost: share of entry funnel (opens + entry rejects).
    """
    opened = sum(1 for r in rows if r.get("event") == "entry_opened")
    entry_rejected = [r for r in rows if r.get("event") == "entry_rejected"]
    cg_rejects = [
        r
        for r in entry_rejected
        if (r.get("reason_code") or "").strip().startswith(CONTEXT_GATE_REASON_PREFIX)
    ]
    by_rc = Counter((r.get("reason_code") or "").strip() for r in cg_rejects)
    by_sym = Counter(((r.get("symbol") or "").strip().upper() or "?") for r in cg_rejects)
    by_st = Counter(((r.get("strategy_name") or "").strip() or "?") for r in cg_rejects)
    ft = opened + len(entry_rejected)
    n_cg = len(cg_rejects)
    n_er = len(entry_rejected)
    return {
        "context_gate_reject_count": n_cg,
        "entry_rejected_total": n_er,
        "context_gate_share_of_entry_rejects": round(n_cg / n_er, 6) if n_er else None,
        "by_reason_code": dict(by_rc.most_common(40)),
        "by_symbol_top": dict(by_sym.most_common(25)),
        "by_strategy_name_top": dict(by_st.most_common(25)),
        "filter_cost_vs_funnel": {
            "entry_funnel_total": ft,
            "entry_opened_count": opened,
            "context_gate_reject_share_of_funnel": round(n_cg / ft, 6) if ft else None,
            "non_context_entry_reject_share_of_funnel": round((n_er - n_cg) / ft, 6)
            if ft
            else None,
            "_note": "So sánh hai experiment (gates OFF vs ON) + trade DB after window để đánh giá edge vs overfilter.",
        },
    }
