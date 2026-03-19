"""
Capital split (Core / Fast) — config + virtual budgets + signal routing.
Spec: document/capital_split_fast_trading_module.md
ADR: docs/ADR_capital_split_fast.md
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.strategies.base import StrategySignal

_LOG = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "core_capital_pct": 0.7,
        "fast_capital_pct": 0.3,
        "max_concurrent_fast": 3,
        "max_daily_loss_fast_pct": 0.025,
        "max_consecutive_loss_fast": 5,
        "default_risk_pct_fast": 0.004,
        "max_notional_usd_fast": 0.0,
        "max_hold_minutes_fast": 90,
        "fast_regimes": ["high_momentum"],
        "fast_strategy_names": [],
        "fast_strategy_denylist": [],
        "correlation_guard_max_same_sector_fast": 0,
        "fast_no_follow_through_enabled": False,
        "fast_no_follow_through_min_minutes": 10.0,
        "fast_no_follow_through_max_mfe_pct": 0.002,
    }


def load_capital_split_config() -> dict[str, Any]:
    """Đọc config/capital_split.v1.json hoặc .example; merge default."""
    cfg = _default_config()
    for name in ("capital_split.v1.json", "capital_split.v1.example.json"):
        path = _CONFIG_DIR / name
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg.update(raw)
            except Exception as e:
                _LOG.warning("capital_split: cannot read %s: %s", path, e)
            break
    # Chuẩn hóa tỉ lệ
    try:
        fc = float(cfg.get("fast_capital_pct", 0.3) or 0.3)
        cc = float(cfg.get("core_capital_pct", 0.7) or 0.7)
        s = fc + cc
        if s > 0 and abs(s - 1.0) > 1e-6:
            cfg["fast_capital_pct"] = fc / s
            cfg["core_capital_pct"] = cc / s
    except (TypeError, ValueError):
        pass
    return cfg


def normalize_bucket(value: str | None) -> str:
    v = (value or "core").strip().lower()
    return v if v == "fast" else "core"


class CapitalSplitManager:
    """Slice ảo trên risk_capital_usd (thường = portfolio.capital_usd)."""

    def __init__(self, config: dict[str, Any], total_risk_capital_usd: float) -> None:
        self.config = config
        self.total = max(0.0, float(total_risk_capital_usd or 0.0))

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    def core_capital_usd(self) -> float:
        p = float(self.config.get("core_capital_pct", 0.7) or 0.7)
        return round(self.total * max(0.0, min(1.0, p)), 4)

    def fast_capital_usd(self) -> float:
        p = float(self.config.get("fast_capital_pct", 0.3) or 0.3)
        return round(self.total * max(0.0, min(1.0, p)), 4)

    def max_concurrent_fast(self) -> int:
        try:
            return max(1, int(self.config.get("max_concurrent_fast", 3) or 3))
        except (TypeError, ValueError):
            return 3

    def max_daily_loss_fast_pct(self) -> float:
        try:
            return max(0.0, float(self.config.get("max_daily_loss_fast_pct", 0.025) or 0.025))
        except (TypeError, ValueError):
            return 0.025

    def max_consecutive_loss_fast(self) -> int:
        try:
            return max(0, int(self.config.get("max_consecutive_loss_fast", 5) or 0))
        except (TypeError, ValueError):
            return 0

    def default_risk_pct_fast(self) -> float:
        try:
            v = float(self.config.get("default_risk_pct_fast", 0.004) or 0.004)
            return max(1e-6, min(0.5, v))
        except (TypeError, ValueError):
            return 0.004

    def max_notional_usd_fast(self) -> float:
        try:
            return max(0.0, float(self.config.get("max_notional_usd_fast", 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    def max_hold_minutes_fast(self) -> int:
        try:
            return max(0, int(self.config.get("max_hold_minutes_fast", 0) or 0))
        except (TypeError, ValueError):
            return 0


def assign_capital_bucket_to_signal(
    signal: StrategySignal,
    regime: str,
    config: dict[str, Any],
) -> None:
    """
    Gán signal.capital_bucket = fast nếu bật split và strategy+regime khớp whitelist.
    """
    if not config.get("enabled"):
        signal.capital_bucket = "core"
        return
    names = config.get("fast_strategy_names") or []
    if isinstance(names, str):
        names = [names]
    names_set = {str(x).strip().lower() for x in names if str(x).strip()}
    regimes = config.get("fast_regimes") or []
    if isinstance(regimes, str):
        regimes = [regimes]
    regimes_set = {str(x).strip().lower() for x in regimes if str(x).strip()}
    strat = (signal.strategy_name or "").strip().lower()
    reg = (regime or "").strip().lower()
    if names_set and strat not in names_set:
        signal.capital_bucket = "core"
        return
    if regimes_set and reg not in regimes_set:
        signal.capital_bucket = "core"
        return
    if not names_set:
        signal.capital_bucket = "core"
        return
    signal.capital_bucket = "fast"
    deny = config.get("fast_strategy_denylist") or []
    if isinstance(deny, str):
        deny = [deny]
    deny_set = {str(x).strip().lower() for x in deny if str(x).strip()}
    if strat in deny_set:
        signal.capital_bucket = "core"


def trade_bucket(trade: object) -> str:
    return normalize_bucket(getattr(trade, "capital_bucket", None))


def consecutive_loss_streak_for_bucket(trades_desc: list, bucket: str | None) -> int:
    """
    trades_desc: close trades mới nhất trước.
    bucket None = mọi trade theo thứ tự thời gian (legacy).
    bucket 'core'|'fast' = chỉ xét các lệnh đóng thuộc bucket, giữ thứ tự thời gian.
    """
    closes = [t for t in trades_desc if getattr(t, "action", None) == "close"]
    if bucket is None:
        seq = closes
    else:
        seq = [t for t in closes if trade_bucket(t) == bucket]
    n = 0
    for t in seq:
        if (getattr(t, "pnl_usd", None) or 0) < 0:
            n += 1
        else:
            break
    return n


def daily_realized_by_bucket(closed_today: list) -> tuple[float, float]:
    core = 0.0
    fast = 0.0
    for t in closed_today:
        if getattr(t, "action", None) != "close":
            continue
        pnl = float(getattr(t, "pnl_usd", 0) or 0)
        if trade_bucket(t) == "fast":
            fast += pnl
        else:
            core += pnl
    return round(core, 4), round(fast, 4)


def open_position_counts(open_positions: list) -> tuple[int, int]:
    oc = of = 0
    for p in open_positions:
        if normalize_bucket(getattr(p, "capital_bucket", None)) == "fast":
            of += 1
        else:
            oc += 1
    return oc, of
