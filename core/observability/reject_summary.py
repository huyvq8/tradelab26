"""Aggregate entry_rejected rows + dedupe stats for API / dashboard."""
from __future__ import annotations

from collections import Counter
from typing import Any

from core.observability.decision_log_tail import tail_decision_log_entries
from core.observability.reject_classification import attach_reject_bucket, classify_entry_reject
from core.observability.reject_dedupe import read_dedupe_suppressed_stats


def build_entry_reject_summary(
    *,
    limit: int = 400,
    symbols: set[str] | None = None,
) -> dict[str, Any]:
    rows = tail_decision_log_entries(
        limit=max(50, min(limit, 2000)),
        symbols=symbols,
        events={"entry_rejected"},
    )
    buckets: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    enriched: list[dict[str, Any]] = []
    for r in rows:
        rc = (r.get("reason_code") or "").strip()
        by_reason[rc] += 1
        rb = r.get("reject_bucket") or classify_entry_reject(rc)
        buckets[rb] += 1
        enriched.append(attach_reject_bucket(dict(r)))
    stats = read_dedupe_suppressed_stats()
    sup = stats.get("suppressed_by_reason") or {}
    noise_total = int(sum(int(v) for v in sup.values()))
    buckets["noise_reject_repeated"] = buckets.get("noise_reject_repeated", 0) + noise_total
    return {
        "by_bucket": dict(buckets),
        "by_reason_code": dict(by_reason.most_common(60)),
        "dedupe_suppressed_by_reason": sup,
        "dedupe_suppressed_total": noise_total,
        "recent": enriched[-40:],
    }
