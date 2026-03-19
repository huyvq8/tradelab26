from __future__ import annotations

from datetime import datetime

from core.market_data.client import Kline1h
from core.portfolio.models import Position


def mfe_pct_since_entry(
    position: Position,
    klines: list[Kline1h],
    price_now: float,
    *,
    now: datetime | None = None,
) -> float:
    entry = float(position.entry_price or 0)
    if entry <= 0:
        return 0.0
    opened = getattr(position, "opened_at", None)
    if opened is None or not klines:
        return 0.0
    now = now or datetime.utcnow()
    entry_ms = int(opened.timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)
    side = (position.side or "long").lower()
    bar_ms = 3600000

    highs: list[float] = []
    lows: list[float] = []
    for k in klines:
        if k.open_time_ms + bar_ms < entry_ms:
            continue
        if k.open_time_ms > now_ms + bar_ms:
            continue
        highs.append(float(k.high))
        lows.append(float(k.low))

    if side == "short":
        m = 0.0
        for lo in lows:
            m = max(m, (entry - lo) / entry)
        m = max(m, max(0.0, (entry - price_now) / entry))
        return float(m)
    m = 0.0
    for hi in highs:
        m = max(m, (hi - entry) / entry)
    m = max(m, max(0.0, (price_now - entry) / entry))
    return float(m)


def fast_no_follow_through_should_close(
    position: Position,
    *,
    price_now: float,
    klines: list[Kline1h],
    cs_cfg: dict,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if not cs_cfg.get("enabled"):
        return False, ""
    if (getattr(position, "capital_bucket", None) or "core") != "fast":
        return False, ""
    if not cs_cfg.get("fast_no_follow_through_enabled", False):
        return False, ""
    try:
        min_min = float(cs_cfg.get("fast_no_follow_through_min_minutes", 10) or 10)
    except (TypeError, ValueError):
        min_min = 10.0
    try:
        max_mfe = float(cs_cfg.get("fast_no_follow_through_max_mfe_pct", 0.002) or 0.002)
    except (TypeError, ValueError):
        max_mfe = 0.002
    now = now or datetime.utcnow()
    opened = getattr(position, "opened_at", None)
    if opened is None:
        return False, ""
    age_min = (now - opened).total_seconds() / 60.0
    if age_min < min_min:
        return False, ""

    direction = 1 if (position.side or "long").lower() == "long" else -1
    entry = float(position.entry_price or 0)
    if entry <= 0:
        return False, ""
    pnl_pct = (price_now - entry) / entry * direction
    if pnl_pct >= 0:
        return False, ""

    mfe = mfe_pct_since_entry(position, klines, price_now, now=now)
    if mfe > max_mfe:
        return False, ""

    return True, (
        f"fast no_follow_through age={age_min:.1f}m pnl_pct={pnl_pct*100:.3f}% "
        f"mfe_pct={mfe*100:.4f}% max_mfe={max_mfe*100:.4f}%"
    )
