"""
Build structured entry context for journal (v4 learning loop).
Provides: reasons, market_context, risk_score so the system "biết vì sao vừa vào lệnh".
"""
from __future__ import annotations

import json
from typing import Any

from core.strategies.base import StrategySignal


def _quote_to_dict(quote: Any) -> dict[str, Any]:
    """Extract market snapshot from quote object (e.g. from get_quotes_with_fallback)."""
    if quote is None:
        return {}
    return {
        "price": getattr(quote, "price", None),
        "change_24h": getattr(quote, "percent_change_24h", None),
        "volume_24h": getattr(quote, "volume_24h", None),
    }


def build_tp_sl_explanation(signal: StrategySignal) -> str:
    """
    Build a short explanation of why TP/SL were chosen (for dashboard).
    Uses strategy name and distances from entry so the journal shows "why TP was chosen".
    """
    entry = signal.entry_price
    sl = signal.stop_loss
    tp = signal.take_profit
    if entry is None or entry <= 0:
        return f"Strategy: {signal.strategy_name}. TP/SL from signal."
    risk_dist = abs(entry - sl) / entry * 100 if sl is not None else 0.0
    reward_dist = abs(tp - entry) / entry * 100 if tp is not None else 0.0
    rr = reward_dist / risk_dist if risk_dist and risk_dist > 0 else 0.0
    parts = [
        f"Strategy: {signal.strategy_name}.",
        f"SL: {risk_dist:.2f}% from entry.",
        f"TP: {reward_dist:.2f}% from entry.",
        f"Planned R:R = {rr:.2f}.",
    ]
    return " ".join(parts)


def build_entry_context(
    signal: StrategySignal,
    risk_reason: str,
    quote: Any = None,
    risk_score: float | None = None,
    timeframe: str = "5m",
) -> dict[str, Any]:
    """
    Build context dict for journal.create_entry.
    - reasons: list of strings (from rationale; strategies can later pass explicit list).
    - market_context: dict with price, change_24h, volume_24h, regime for AI learning.
    - risk_score: optional 0-1 (caller can compute from stop distance or leave None).
    """
    reasons = [s.strip() for s in (signal.rationale or "").split(". ") if s.strip()]
    if not reasons:
        reasons = [signal.rationale or "signal_based"]

    ctx = _quote_to_dict(quote)
    if signal.regime:
        ctx["regime"] = signal.regime
    market_context = {k: v for k, v in ctx.items() if v is not None}

    return {
        "reasons": reasons,
        "market_context": market_context,
        "risk_score": risk_score,
        "timeframe": timeframe or "5m",
    }


def serialize_reasons(reasons: list[str]) -> str | None:
    """Serialize reasons list to JSON string for DB."""
    if not reasons:
        return None
    return json.dumps(reasons, ensure_ascii=False)


def serialize_market_context(ctx: dict[str, Any]) -> str | None:
    """Serialize market_context dict to JSON string for DB."""
    if not ctx:
        return None
    return json.dumps(ctx, ensure_ascii=False)


def deserialize_reasons(raw: str | None) -> list[str]:
    """Deserialize reasons from DB."""
    if not raw:
        return []
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def deserialize_market_context(raw: str | None) -> dict[str, Any]:
    """Deserialize market_context from DB."""
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def serialize_mistake_tags(tags: list[str]) -> str | None:
    """Serialize mistake_tags list to JSON string for DB."""
    if not tags:
        return None
    return json.dumps(tags, ensure_ascii=False)


def deserialize_mistake_tags(raw: str | None) -> list[str]:
    """Deserialize mistake_tags from DB."""
    if not raw:
        return []
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:
        return []
