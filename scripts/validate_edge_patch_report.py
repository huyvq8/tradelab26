#!/usr/bin/env python3
"""
Phase 2 — đo lường negative-edge patch: before/after, combo SIREN, decision_log, experiment snapshot.

Usage (từ thư mục trading-lab-pro-v3):
  python scripts/validate_edge_patch_report.py
  python scripts/validate_edge_patch_report.py --portfolio "Paper Portfolio" --split-ts "2026-03-01T00:00:00"
  python scripts/validate_edge_patch_report.py --combo-audit --symbol SIREN

Env (optional): EDGE_EXPERIMENT, EDGE_SESSION, ENTRY_TIMING_CONFIG, ENTRY_CONTEXT_GATES_CONFIG, PROFIT_ACTIVE_OVERLAY

Decision log JSON includes entry_context_gates (CONTEXT_GATE_* counts & funnel share); see docs/entry_context_rollout.md.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# project root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability.context_gate_metrics import analyze_entry_context_gates


def _parse_ts(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def load_decision_log() -> list[dict]:
    path = ROOT / "data" / "decision_log.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def analyze_symbol_decisions(rows: list[dict], symbol: str, strategies: list[str]) -> dict:
    """Đếm event/reason theo symbol (SIREN audit)."""
    su = symbol.strip().upper()
    stset = set(strategies)
    counts: Counter = Counter()
    opened = 0
    rejected = 0
    chase = 0
    combo_blk = 0
    for r in rows:
        if (r.get("symbol") or "").upper() != su:
            continue
        st = (r.get("strategy_name") or "").strip()
        if st not in stset:
            continue
        ev = r.get("event") or ""
        rc = (r.get("reason_code") or "").strip()
        counts[f"{ev}|{rc}"] += 1
        if ev == "entry_opened":
            opened += 1
        if ev == "entry_rejected":
            rejected += 1
            if rc in ("ENTRY_CHASE_TOP", "ENTRY_EXTENDED_CANDLE", "ENTRY_NO_RETEST", "ENTRY_EXTENDED_ABOVE_MID"):
                chase += 1
            if rc == "COMBO_BLOCKED_EDGE":
                combo_blk += 1
    return {
        "symbol": su,
        "strategies_filtered": list(stset),
        "entry_opened_lines": opened,
        "entry_rejected_lines": rejected,
        "chase_or_extended_rejects": chase,
        "combo_blocked_rejects": combo_blk,
        "top_keys": dict(counts.most_common(15)),
    }


def wilson_ci95(successes: int, n: int) -> tuple[float | None, float | None]:
    """Wilson score interval for binomial proportion (95% default z=1.96)."""
    if n <= 0 or successes < 0:
        return None, None
    z = 1.96
    p = successes / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return max(0.0, centre - margin), min(1.0, centre + margin)


def reason_breakdown_by_event(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Nested: event -> reason_code -> count (empty reason -> '')."""
    nested: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        ev = (r.get("event") or "").strip() or "(empty_event)"
        rc = (r.get("reason_code") or "").strip() or "(no_reason_code)"
        nested[ev][rc] += 1
    return {ev: dict(ct) for ev, ct in sorted(nested.items(), key=lambda x: -sum(x[1].values()))}


def entry_funnel_filter_cost(rows: list[dict]) -> dict:
    """
    'Filter cost' = share of entry funnel consumed by rejects (measurement, not $).
    Per-reason shares are of rejects and of total funnel for comparability across experiments.
    """
    opened = sum(1 for r in rows if r.get("event") == "entry_opened")
    rejected = [r for r in rows if r.get("event") == "entry_rejected"]
    n_rej = len(rejected)
    funnel = opened + n_rej
    by_reason = Counter((r.get("reason_code") or "").strip() or "(no_reason_code)" for r in rejected)
    return {
        "entry_opened_count": opened,
        "entry_rejected_count": n_rej,
        "entry_funnel_total": funnel,
        "reject_share_of_funnel": round(n_rej / funnel, 6) if funnel else None,
        "opens_share_of_funnel": round(opened / funnel, 6) if funnel else None,
        "rejects_by_reason_count": dict(by_reason.most_common(50)),
        "reject_reason_share_of_rejects": {k: round(v / n_rej, 6) for k, v in by_reason.items()} if n_rej else {},
        "reject_reason_share_of_funnel": {k: round(v / funnel, 6) for k, v in by_reason.items()} if funnel else {},
    }


def native_signal_coverage(rows: list[dict]) -> dict:
    """How often entry_rejected / passthrough payloads carry native_signal (Phase 2 signal architecture)."""
    targets = [r for r in rows if r.get("event") in ("entry_rejected", "signal_levels_passthrough")]
    if not targets:
        return {"eligible_events": 0, "with_native_signal": 0, "coverage": None}
    pls = [r.get("payload") or {} for r in targets]
    with_ns = sum(1 for p in pls if isinstance(p.get("native_signal"), dict))
    return {
        "eligible_events": len(targets),
        "with_native_signal": with_ns,
        "coverage": round(with_ns / len(targets), 6) if targets else None,
    }


def analyze_decision_log(rows: list[dict]) -> dict:
    by_event = Counter(r.get("event") or "" for r in rows)
    by_reason = Counter((r.get("reason_code") or "").strip() for r in rows if r.get("reason_code"))
    combo_blocks = sum(
        1
        for r in rows
        if r.get("reason_code") == "COMBO_BLOCKED_EDGE"
        or (r.get("payload") or {}).get("reason_code") == "COMBO_BLOCKED_EDGE"
    )
    entry_rejects = [r for r in rows if r.get("event") == "entry_rejected"]
    funnel = entry_funnel_filter_cost(rows)
    return {
        "total_lines": len(rows),
        "events": dict(by_event),
        "reason_codes_top": dict(by_reason.most_common(25)),
        "reason_breakdown_by_event": reason_breakdown_by_event(rows),
        "entry_funnel_filter_cost": funnel,
        "entry_context_gates": analyze_entry_context_gates(rows),
        "native_signal_payload_coverage": native_signal_coverage(rows),
        "combo_blocked_events_est": combo_blocks,
        "entry_rejected_count": len(entry_rejects),
    }


def evaluate_sample_sufficiency(
    before: dict,
    after: dict,
    dlog: dict,
    *,
    min_closed_before: int,
    min_closed_after: int,
    min_decision_lines: int,
    min_entry_funnel: int,
) -> dict:
    """Guards for acceptance / cross-experiment comparability (warnings, not trading rules)."""
    issues: list[str] = []
    bc = int(before.get("closed_trades") or 0)
    ac = int(after.get("closed_trades") or 0)
    if bc < min_closed_before:
        issues.append(f"before.closed_trades={bc} < min_closed_before={min_closed_before}")
    if ac < min_closed_after:
        issues.append(f"after.closed_trades={ac} < min_closed_after={min_closed_after}")
    lines = int(dlog.get("total_lines") or 0)
    if lines < min_decision_lines:
        issues.append(f"decision_log.lines={lines} < min_decision_lines={min_decision_lines}")
    funnel = (dlog.get("entry_funnel_filter_cost") or {}).get("entry_funnel_total")
    if funnel is not None and int(funnel) < min_entry_funnel:
        issues.append(f"entry_funnel_total={funnel} < min_entry_funnel={min_entry_funnel}")
    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "thresholds": {
            "min_closed_before": min_closed_before,
            "min_closed_after": min_closed_after,
            "min_decision_lines": min_decision_lines,
            "min_entry_funnel": min_entry_funnel,
        },
        "observed": {
            "before_closed_trades": bc,
            "after_closed_trades": ac,
            "decision_log_lines": lines,
            "entry_funnel_total": funnel,
        },
    }


def trade_metrics_from_db(
    portfolio_name: str,
    split_ts: datetime | None,
) -> tuple[dict, dict]:
    from sqlalchemy import select
    from core.db import SessionLocal
    from core.portfolio.models import Portfolio, Trade, Position

    before_stats: dict = {"n": 0, "label": "before"}
    after_stats: dict = {"n": 0, "label": "after"}

    def bucket(ts: datetime) -> str:
        if split_ts is None:
            return "after"
        ts_n, sp_n = _naive(ts), _naive(split_ts)
        return "before" if ts_n < sp_n else "after"

    def fold(stats: dict, pnl: float, hold_min: float, tp_pct: float | None, target_usd: float | None) -> None:
        stats["n"] += 1
        stats.setdefault("pnls", []).append(pnl)
        stats.setdefault("hold_minutes", []).append(hold_min)
        if tp_pct is not None:
            stats.setdefault("tp_pct_at_entry", []).append(tp_pct)
        if target_usd and target_usd > 0 and pnl > 0:
            stats.setdefault("realized_over_target", []).append(pnl / target_usd)

    with SessionLocal() as db:
        port = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not port:
            return before_stats, after_stats
        closes = list(
            db.scalars(
                select(Trade).where(
                    Trade.portfolio_id == port.id,
                    Trade.action == "close",
                )
            )
        )

        for t in closes:
            ts = _naive(t.created_at)
            b = bucket(ts)
            stats = before_stats if b == "before" else after_stats
            pos = db.get(Position, t.position_id) if t.position_id else None
            hold_min = 0.0
            tp_pct = None
            target_usd = None
            entry = float(t.price)  # fallback
            qty = float(t.quantity or 0)
            if pos:
                entry = float(pos.entry_price or entry)
                oa = pos.opened_at
                if oa:
                    if oa.tzinfo is not None:
                        oa = oa.replace(tzinfo=None)
                    hold_min = max(0.0, (ts - oa).total_seconds() / 60.0)
                if pos.take_profit is not None and pos.side == "long":
                    tp_pct = abs(float(pos.take_profit) - entry) / max(entry, 1e-12) * 100.0
                    if pos.side == "long":
                        target_usd = max(0.0, (float(pos.take_profit) - entry) * qty)
                elif pos.take_profit is not None and pos.side == "short":
                    tp_pct = abs(entry - float(pos.take_profit)) / max(entry, 1e-12) * 100.0
                    target_usd = max(0.0, (entry - float(pos.take_profit)) * qty)

            pnl = float(t.pnl_usd or 0)
            fold(stats, pnl, hold_min, tp_pct, target_usd)
            if pnl < 0 and hold_min < 5:
                stats.setdefault("sl_fast_lt5min", 0)
                stats["sl_fast_lt5min"] = stats.get("sl_fast_lt5min", 0) + 1

    def finalize(stats: dict) -> dict:
        n = stats.pop("n", 0)
        pnls = stats.pop("pnls", [])
        holds = stats.pop("hold_minutes", [])
        tps = stats.pop("tp_pct_at_entry", [])
        rts = stats.pop("realized_over_target", [])
        if n == 0:
            return {**stats, "closed_trades": 0}
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        nw = len(wins)
        gp, gl = sum(wins), abs(sum(losses))
        pf = (gp / gl) if gl > 0 else (99.0 if gp > 0 else 0.0)
        w_lo, w_hi = wilson_ci95(nw, n)
        return {
            **stats,
            "closed_trades": n,
            "win_rate": round(nw / n, 4),
            "win_rate_wilson_95_low": round(w_lo, 4) if w_lo is not None else None,
            "win_rate_wilson_95_high": round(w_hi, 4) if w_hi is not None else None,
            "profit_factor": round(float(pf), 4),
            "expectancy_usd": round(sum(pnls) / n, 4),
            "avg_hold_minutes": round(sum(holds) / len(holds), 2) if holds else None,
            "avg_tp_distance_pct_at_entry": round(sum(tps) / len(tps), 4) if tps else None,
            "avg_realized_over_target_ratio": round(sum(rts) / len(rts), 4) if rts else None,
            "sl_loss_lt_5min_count": stats.get("sl_fast_lt5min", 0),
            "sl_loss_lt_5min_pct_of_losses": round(
                stats.get("sl_fast_lt5min", 0) / max(1, len(losses)), 4
            ),
        }

    return finalize(before_stats), finalize(after_stats)


def combo_audit(
    portfolio_name: str,
    symbol: str,
    strategies: list[str],
    split_ts: datetime | None,
) -> dict:
    from sqlalchemy import select
    from core.db import SessionLocal
    from core.portfolio.models import Portfolio, Trade, Position

    sym_u = symbol.strip().upper()
    out: dict = {}
    with SessionLocal() as db:
        port = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not port:
            return {"error": "no_portfolio"}
        for strat in strategies:
            closes = list(
                db.scalars(
                    select(Trade).where(
                        Trade.portfolio_id == port.id,
                        Trade.action == "close",
                        Trade.symbol == sym_u,
                        Trade.strategy_name == strat,
                        Trade.side == "long",
                    )
                )
            )
            filtered = []
            for t in closes:
                ts = t.created_at
                ts = _naive(ts)
                if split_ts is not None and ts < _naive(split_ts):
                    continue
                filtered.append(t)
            n = len(filtered)
            wins = sum(1 for t in filtered if (t.pnl_usd or 0) > 0)
            losses = [t for t in filtered if (t.pnl_usd or 0) < 0]
            fast = 0
            for t in losses:
                pos = db.get(Position, t.position_id) if t.position_id else None
                if not pos or not pos.opened_at:
                    continue
                oa = pos.opened_at.replace(tzinfo=None) if pos.opened_at.tzinfo else pos.opened_at
                ts = t.created_at.replace(tzinfo=None) if t.created_at.tzinfo else t.created_at
                if (ts - oa).total_seconds() / 60.0 < 5:
                    fast += 1
            out[f"{strat}+{sym_u}+long"] = {
                "closes_after_split": n,
                "win_rate": round(wins / n, 4) if n else None,
                "sl_loss_lt5min": fast,
                "note": "Chỉ đếm close trades; signal fire count xem decision_log (entry_rejected / entry_opened).",
            }
    return out


def improvement_delta(before: dict, after: dict) -> dict:
    """Chênh lệch số liệu chính (after - before) để chứng minh patch."""

    def _f(d: dict, k: str) -> float | None:
        v = d.get(k)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    keys = (
        "win_rate",
        "win_rate_wilson_95_low",
        "win_rate_wilson_95_high",
        "profit_factor",
        "expectancy_usd",
        "avg_hold_minutes",
        "avg_tp_distance_pct_at_entry",
        "sl_loss_lt_5min_count",
        "sl_loss_lt_5min_pct_of_losses",
        "avg_realized_over_target_ratio",
    )
    out: dict = {}
    for k in keys:
        b, a = _f(before, k), _f(after, k)
        if b is None and a is None:
            continue
        delta = (a - b) if b is not None and a is not None else None
        out[k] = {"before": b, "after": a, "delta": delta}
    out["_note"] = "delta = after - before. sl_loss: lower is better if patch works."
    return out


def build_markdown(
    before: dict,
    after: dict,
    dlog: dict,
    combo: dict | None,
    split_ts: str | None,
    siren_hints: dict | None = None,
    sufficiency: dict | None = None,
) -> str:
    lines = [
        "# Edge patch validation report",
        "",
        f"Split timestamp (UTC naive compare): `{split_ts or 'not set — single bucket may be empty'}`",
        "",
        "## Trade DB metrics",
        "",
        "### Before window",
        "```json",
        json.dumps(before, indent=2, ensure_ascii=False),
        "```",
        "",
        "### After window",
        "```json",
        json.dumps(after, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    if split_ts and (before.get("closed_trades") or 0) > 0 and (after.get("closed_trades") or 0) > 0:
        lines += [
            "## Measurable improvement (after − before)",
            "",
            "```json",
            json.dumps(improvement_delta(before, after), indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    if sufficiency:
        lines += [
            "## Sample sufficiency & comparability guards",
            "",
            "```json",
            json.dumps(sufficiency, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    lines += [
        "## Decision log",
        "```json",
        json.dumps(dlog, indent=2, ensure_ascii=False),
        "```",
    ]
    if combo:
        lines += [
            "",
            "## Combo audit (post-split, DB closes)",
            "```json",
            json.dumps(combo, indent=2, ensure_ascii=False),
            "```",
        ]
    if siren_hints:
        lines += [
            "",
            "## Root-cause deepening — decision_log (symbol filter)",
            "- **Đã bị block chưa:** `combo_blocked_rejects` và `COMBO_BLOCKED_EDGE` trong `top_keys`.",
            "- **Còn fire / vào lệnh:** `entry_opened_lines` vs `entry_rejected_lines`.",
            "- **Chase:** `chase_or_extended_rejects` (mã ENTRY_CHASE_TOP / EXTENDED / NO_RETEST).",
            "",
            "```json",
            json.dumps(siren_hints, indent=2, ensure_ascii=False),
            "```",
        ]
    lines += [
        "",
        "## Interpretation",
        "- **PF / expectancy**: so sánh khối `after` vs `before` (cần đủ sample).",
        "- **sl_loss_lt_5min**: số lệnh lỗ đóng trong &lt; 5 phút.",
        "- **avg_tp_distance_pct_at_entry**: TP trên position tại thời điểm đóng (ước lượng mức TP đặt lúc vào).",
        "- **decision_log**: `entry_rejected` + `reason_code`; `COMBO_BLOCKED_EDGE` giảm spam combo xấu.",
        "- **reason_breakdown_by_event** / **entry_funnel_filter_cost**: đo từ chối theo mã & phần funnel.",
        "- **win_rate_wilson_95_***: khoảng tin cậy Wilson cho win rate (n nhỏ → rộng).",
        "- **sample_sufficiency**: bảng ngưỡng tối thiểu để so sánh before/after có ý nghĩa thống kê.",
        "- **entry_context_gates**: đếm `CONTEXT_GATE_*`, share funnel vs `entry_opened`; xem `docs/entry_context_rollout.md`.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", default="Paper Portfolio")
    ap.add_argument(
        "--split-ts",
        default=None,
        help="ISO timestamp: trades strictly before = before, on or after = after",
    )
    ap.add_argument("--combo-audit", action="store_true")
    ap.add_argument("--symbol", default="SIREN")
    ap.add_argument("--record-experiment", action="store_true", help="Append storage/experiments/results_*.jsonl")
    ap.add_argument("--min-closed-before", type=int, default=30, help="Sufficiency guard: min closes in before window")
    ap.add_argument("--min-closed-after", type=int, default=80, help="Sufficiency guard: min closes in after window")
    ap.add_argument("--min-decision-lines", type=int, default=200, help="Sufficiency guard: min decision_log lines")
    ap.add_argument("--min-entry-funnel", type=int, default=40, help="Sufficiency guard: opened+rejected entry events")
    ap.add_argument(
        "--strict-sample",
        action="store_true",
        help="Exit with code 3 if sample_sufficiency.ok is false",
    )
    args = ap.parse_args()

    split = _parse_ts(args.split_ts)

    before_m, after_m = trade_metrics_from_db(args.portfolio, split)
    drows = load_decision_log()
    dlog = analyze_decision_log(drows)

    # Không có split → mọi lệnh vào bucket "after"; before=0 là đúng thiết kế.
    min_closed_before_eff = 0 if split is None else args.min_closed_before
    sufficiency = evaluate_sample_sufficiency(
        before_m,
        after_m,
        dlog,
        min_closed_before=min_closed_before_eff,
        min_closed_after=args.min_closed_after,
        min_decision_lines=args.min_decision_lines,
        min_entry_funnel=args.min_entry_funnel,
    )
    if split is None:
        sufficiency = {
            **sufficiency,
            "note": "No --split-ts: before-window guard disabled (min_closed_before treated as 0).",
        }

    combo = None
    siren_hints = None
    if args.combo_audit:
        combo = combo_audit(
            args.portfolio,
            args.symbol,
            ["trend_following", "breakout_momentum"],
            split,
        )
        siren_hints = analyze_symbol_decisions(
            drows, args.symbol, ["trend_following", "breakout_momentum"]
        )

    md = build_markdown(before_m, after_m, dlog, combo, args.split_ts, siren_hints, sufficiency)
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"edge_validation_{stamp}.md"
    json_path = out_dir / f"edge_validation_{stamp}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "before": before_m,
                "after": after_m,
                "improvement_delta": improvement_delta(before_m, after_m)
                if args.split_ts
                and (before_m.get("closed_trades") or 0) > 0
                and (after_m.get("closed_trades") or 0) > 0
                else None,
                "decision_log": dlog,
                "sample_sufficiency": sufficiency,
                "combo": combo,
                "siren_decision_hints": siren_hints,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(md)
    print(f"\nWrote: {md_path}")
    print(f"Wrote: {json_path}")
    if not sufficiency.get("ok"):
        print("\n[sample_sufficiency] NOT OK — increase paper runtime or lower thresholds:")
        for issue in sufficiency.get("issues") or []:
            print(f"  - {issue}")
        if args.strict_sample:
            raise SystemExit(3)

    if args.record_experiment:
        from core.experiments.session import record_experiment_snapshot

        record_experiment_snapshot(
            {
                "before": before_m,
                "after": after_m,
                "decision_log": dlog,
                "sample_sufficiency": sufficiency,
                "combo": combo,
            },
            label="validate_edge_patch_report",
        )


if __name__ == "__main__":
    main()
