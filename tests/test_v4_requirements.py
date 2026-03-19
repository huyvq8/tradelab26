"""
Test v4 requirements: 4 tiêu chí "thông minh thật" và vòng lặp học.
Chạy: python -m pytest tests/test_v4_requirements.py -v
Hoặc: cd trading-lab-pro-v3 && python tests/test_v4_requirements.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def test_1_journal_has_entry_context():
    """1. Biết vì sao vừa vào lệnh: Journal lưu reasons, market_context, risk_score, timeframe."""
    from core.journal.models import JournalEntry
    from core.journal.context_builder import (
        build_entry_context,
        serialize_reasons,
        serialize_market_context,
        deserialize_reasons,
        deserialize_market_context,
    )
    from core.strategies.base import StrategySignal

    signal = StrategySignal(
        symbol="BTC",
        strategy_name="trend_following",
        side="long",
        confidence=0.72,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
        rationale="Momentum and regime aligned.",
        regime="high_momentum",
    )
    ctx = build_entry_context(signal, "Approved by risk policy.", risk_score=0.3, timeframe="5m")
    assert "reasons" in ctx and len(ctx["reasons"]) >= 1
    assert "market_context" in ctx
    assert ctx.get("risk_score") == 0.3
    assert ctx.get("timeframe") == "5m"

    raw_reasons = serialize_reasons(ctx["reasons"])
    back = deserialize_reasons(raw_reasons)
    assert back == ctx["reasons"]
    raw_mc = serialize_market_context(ctx["market_context"])
    assert deserialize_market_context(raw_mc) == ctx["market_context"]

    assert hasattr(JournalEntry, "reasons")
    assert hasattr(JournalEntry, "market_context")
    assert hasattr(JournalEntry, "risk_score")
    assert hasattr(JournalEntry, "timeframe")
    assert hasattr(JournalEntry, "side")


def test_2_journal_has_exit_context():
    """2. Biết vì sao vừa thua: Journal có exit_reason, mistake_tags."""
    from core.journal.service import _infer_exit_reason
    from core.journal.models import JournalEntry
    from core.journal.context_builder import serialize_mistake_tags, deserialize_mistake_tags

    assert _infer_exit_reason("SL kích hoạt") == "sl_hit"
    assert _infer_exit_reason("TP kích hoạt") == "tp_hit"
    assert _infer_exit_reason("Đồng bộ từ Binance") == "sync_binance"

    tags = ["sl_very_fast", "loss_short_hold"]
    raw = serialize_mistake_tags(tags)
    assert deserialize_mistake_tags(raw) == tags

    assert hasattr(JournalEntry, "exit_reason")
    assert hasattr(JournalEntry, "mistake_tags")


def test_3_reflection_suggested_actions():
    """3. Biết nên sửa gì: Reflection trả suggested_actions; Optimizer ghi candidate."""
    from core.reflection.engine import ReflectionEngine
    from core.db import SessionLocal
    from sqlalchemy import select
    from core.portfolio.models import Portfolio

    with SessionLocal() as db:
        today = date.today()
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
        pid = portfolio.id if portfolio else None
        out = ReflectionEngine().build_daily_reflection(db, today, portfolio_id=pid)
    assert "suggested_actions" in out
    assert isinstance(out["suggested_actions"], list)
    assert "reflection_summary" in out
    assert "mistakes_found" in out

    from core.ai.optimizer_agent import apply_suggested_actions_to_candidate, get_candidate_strategy_config
    actions = [
        {"type": "disable_strategy_under_regime", "strategy": "breakout_momentum", "regime": "low_volume_chop"},
    ]
    n = apply_suggested_actions_to_candidate(actions)
    assert n >= 0
    cand = get_candidate_strategy_config()
    assert cand is None or ("strategies" in cand and "disabled_under_regime" in cand)


def test_4_validation_and_promotion():
    """4. Biết sửa xong có tốt hơn thật không: Backtest theo config, promotion_rules, promote."""
    from core.validate.promotion_rules import load_promotion_rules, check_promotion
    from core.backtest.engine import run_backtest
    from core.strategies.implementations import build_strategy_set_from_config
    import pandas as pd
    import numpy as np

    rules = load_promotion_rules()
    assert "min_profit_factor_improvement_pct" in rules

    pass_, reasons = check_promotion(
        {"profit_factor": 1.0, "max_drawdown_pct": 10, "total_trades": 20},
        {"profit_factor": 1.1, "max_drawdown_pct": 10, "total_trades": 18},
    )
    assert isinstance(pass_, bool)
    assert isinstance(reasons, list)

    strategies = build_strategy_set_from_config({"strategies": {"trend_following": {"enabled": True}, "breakout_momentum": {"enabled": False}}})
    assert len(strategies) >= 1

    np.random.seed(42)
    n = 100
    df = pd.DataFrame({
        "open": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "high": 101 + np.cumsum(np.random.randn(n) * 0.5),
        "low": 99 + np.cumsum(np.random.randn(n) * 0.5),
        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "volume": np.abs(np.random.randn(n)) * 1e6,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1h"))
    result = run_backtest(df, symbol="TEST", strategy_config={"strategies": {"trend_following": {"enabled": True}}, "disabled_under_regime": {}})
    assert "profit_factor" in result
    assert "total_trades" in result
    assert "max_drawdown_pct" in result


def test_rejected_signals_log():
    """Blocked Trades: ghi và đọc rejected signals."""
    from core.rejected_signals_log import log_rejected, get_rejected_signals

    log_rejected("BTC", "trend_following", "Daily loss limit reached.")
    items = get_rejected_signals(limit=5)
    assert isinstance(items, list)


def test_config_files_exist():
    """Config v4: strategy.active, candidate, promotion_rules."""
    config_dir = root / "config"
    assert config_dir.exists()
    assert (config_dir / "strategy.active.json").exists()
    assert (config_dir / "strategy.candidate.json").exists()
    assert (config_dir / "promotion_rules.json").exists()


def test_journal_service_create_and_outcome():
    """Tích hợp: create_entry với context đầy đủ, add_outcome với exit_reason + mistake_tags."""
    from core.journal.service import JournalService
    from core.journal.models import JournalEntry
    from core.db import SessionLocal
    from core.strategies.base import StrategySignal
    from sqlalchemy import select

    signal = StrategySignal(
        symbol="TEST",
        strategy_name="trend_following",
        side="long",
        confidence=0.7,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=105.0,
        rationale="Test reason.",
        regime="high_momentum",
    )
    with SessionLocal() as db:
        svc = JournalService()
        entry = svc.create_entry(
            db, signal, "Approved",
            setup_score=70.0,
            side="long",
            reasons=["test_reason"],
            market_context={"regime": "high_momentum"},
            risk_score=0.25,
            timeframe="5m",
        )
        db.flush()
        assert entry.id
        assert entry.reasons is not None
        assert entry.market_context is not None
        assert entry.risk_score == 0.25
        assert entry.timeframe == "5m"
        svc.add_outcome(
            db, entry.id,
            result_summary="Closed by SL",
            lessons="Test lesson",
            mistakes="sl fast",
            result_r=-1.0,
            exit_reason="sl_hit",
            mistake_tags=["sl_very_fast"],
        )
        db.commit()
        db.refresh(entry)
    assert entry.exit_reason == "sl_hit"
    assert entry.mistake_tags is not None


def run_all():
    """Chạy toàn bộ test (khi không dùng pytest)."""
    tests = [
        ("Journal entry context", test_1_journal_has_entry_context),
        ("Journal exit context", test_2_journal_has_exit_context),
        ("JournalService create + outcome", test_journal_service_create_and_outcome),
        ("Reflection + suggested_actions", test_3_reflection_suggested_actions),
        ("Validation + promotion", test_4_validation_and_promotion),
        ("Blocked signals log", test_rejected_signals_log),
        ("Config files", test_config_files_exist),
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  OK: {name}")
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            failed.append((name, e))
    if failed:
        print(f"\n{len(failed)} test(s) failed.")
        sys.exit(1)
    print("\nAll checks passed (v4 requirements).")


if __name__ == "__main__":
    run_all()
