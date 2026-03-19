#!/usr/bin/env python3
"""
So sánh hai file JSON từ `validate_edge_patch_report.py` (hoặc snapshot experiment).

Usage:
  python scripts/compare_edge_reports.py reports/edge_validation_A.json reports/edge_validation_B.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _metrics(blob: dict) -> dict:
    """Normalize: support top-level before/after or nested metrics."""
    if "before" in blob and "after" in blob:
        return {"before": blob["before"], "after": blob["after"], "label": blob.get("label", "")}
    m = blob.get("metrics") or {}
    return {
        "before": m.get("before") or {},
        "after": m.get("after") or {},
        "label": blob.get("label", ""),
    }


def _row(name: str, b: float | None, a: float | None) -> str:
    if b is None and a is None:
        return f"| {name} | — | — | — |"
    bv = f"{b:.4f}" if b is not None else "—"
    av = f"{a:.4f}" if a is not None else "—"
    d = (a - b) if b is not None and a is not None else None
    dv = f"{d:+.4f}" if d is not None else "—"
    return f"| {name} | {bv} | {av} | {dv} |"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("report_a", type=Path, help="JSON report (baseline / pre-patch)")
    ap.add_argument("report_b", type=Path, help="JSON report (candidate / post-patch)")
    args = ap.parse_args()

    raw_a = _load(args.report_a)
    raw_b = _load(args.report_b)
    A = _metrics(raw_a)
    B = _metrics(raw_b)
    # So sánh cửa sổ "after" của B vs "after" của A (hoặc after vs after)
    ma, mb = A.get("after") or {}, B.get("after") or {}

    keys = [
        ("win_rate", "Win rate"),
        ("win_rate_wilson_95_low", "Win rate Wilson 95% low"),
        ("win_rate_wilson_95_high", "Win rate Wilson 95% high"),
        ("profit_factor", "Profit factor"),
        ("expectancy_usd", "Expectancy USD"),
        ("avg_hold_minutes", "Avg hold (min)"),
        ("avg_tp_distance_pct_at_entry", "Avg TP % @ entry"),
        ("sl_loss_lt_5min_count", "SL loss <5min count"),
        ("sl_loss_lt_5min_pct_of_losses", "SL <5min / losses"),
    ]

    sa = raw_a.get("sample_sufficiency") or {}
    sb = raw_b.get("sample_sufficiency") or {}

    lines = [
        "# Edge report comparison",
        "",
        f"- **A (baseline):** `{args.report_a}`",
        f"- **B (compare):** `{args.report_b}`",
        "",
        "So sánh khối **`after`** của mỗi file (cùng định nghĩa cửa sổ thời gian nếu split-ts khác nhau thì cần đọc lại ngữ cảnh).",
        "",
    ]
    if sa or sb:
        lines += [
            "## Sample sufficiency (from JSON)",
            "",
            f"| Report | sufficiency.ok | after_closed_trades | issues |",
            f"|--------|----------------|---------------------|--------|",
            f"| A | {sa.get('ok')} | {(sa.get('observed') or {}).get('after_closed_trades')} | {len(sa.get('issues') or [])} |",
            f"| B | {sb.get('ok')} | {(sb.get('observed') or {}).get('after_closed_trades')} | {len(sb.get('issues') or [])} |",
            "",
        ]

    lines += [
        "| Metric | A.after | B.after | B−A |",
        "|--------|---------|--------|-----|",
    ]
    for k, title in keys:
        def f(d, key):
            v = d.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        lines.append(_row(title, f(ma, k), f(mb, k)))

    dga = (raw_a.get("decision_log") or {}).get("entry_context_gates") or {}
    dgb = (raw_b.get("decision_log") or {}).get("entry_context_gates") or {}
    if dga or dgb:
        def _cg(d: dict, k: str):
            v = d.get(k)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        fv_a = (dga.get("filter_cost_vs_funnel") or {})
        fv_b = (dgb.get("filter_cost_vs_funnel") or {})
        lines += [
            "",
            "## Entry context gates (from decision_log in each report)",
            "",
            "| Gate metric | A | B | B−A |",
            "|-------------|---|---|-----|",
            _row("context_gate_reject_count", _cg(dga, "context_gate_reject_count"), _cg(dgb, "context_gate_reject_count")),
            _row(
                "context_gate_share_of_funnel",
                _cg(fv_a, "context_gate_reject_share_of_funnel"),
                _cg(fv_b, "context_gate_reject_share_of_funnel"),
            ),
            _row("entry_opened_count", _cg(fv_a, "entry_opened_count"), _cg(fv_b, "entry_opened_count")),
            "",
        ]

    print("\n".join(lines))


if __name__ == "__main__":
    main()
