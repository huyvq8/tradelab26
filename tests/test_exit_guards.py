from __future__ import annotations

from datetime import datetime, timedelta

from core.market_data.client import Kline1h
from core.orchestration.exit_guards import (
    fast_no_follow_through_should_close,
    mfe_pct_since_entry,
)
from core.portfolio.models import Position


def _klines(entry_ms: int, highs: list[float]) -> list[Kline1h]:
    out = []
    t = entry_ms
    for h in highs:
        out.append(
            Kline1h(open=1.0, high=h, low=0.99, close=1.0, volume=1.0, open_time_ms=t),
        )
        t += 3600000
    return out


def test_mfe_long_positive():
    pos = Position(
        portfolio_id=1,
        symbol="X",
        side="long",
        strategy_name="t",
        entry_price=100.0,
        quantity=1.0,
        stop_loss=98.0,
        take_profit=105.0,
        confidence=0.5,
        opened_at=datetime.utcnow() - timedelta(hours=2),
        is_open=True,
    )
    entry_ms = int(pos.opened_at.timestamp() * 1000)
    kl = _klines(entry_ms, [100.5, 101.0, 100.8])
    mfe = mfe_pct_since_entry(pos, kl, price_now=99.0, now=datetime.utcnow())
    assert mfe >= 0.009


def test_no_follow_through_triggers():
    pos = Position(
        portfolio_id=1,
        symbol="X",
        side="long",
        strategy_name="t",
        entry_price=100.0,
        quantity=1.0,
        stop_loss=98.0,
        take_profit=105.0,
        confidence=0.5,
        opened_at=datetime.utcnow() - timedelta(minutes=15),
        is_open=True,
        capital_bucket="fast",
    )
    entry_ms = int(pos.opened_at.timestamp() * 1000)
    kl = _klines(entry_ms, [100.05, 100.08, 100.02])
    cs = {
        "enabled": True,
        "fast_no_follow_through_enabled": True,
        "fast_no_follow_through_min_minutes": 10,
        "fast_no_follow_through_max_mfe_pct": 0.002,
    }
    ok, msg = fast_no_follow_through_should_close(
        pos, price_now=99.9, klines=kl, cs_cfg=cs, now=datetime.utcnow(),
    )
    assert ok is True
    assert "no_follow_through" in msg or "mfe" in msg.lower()
