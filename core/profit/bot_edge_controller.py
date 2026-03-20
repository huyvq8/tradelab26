"""
Adaptive trading mode (FAST / CORE / DEFENSIVE) — ưu tiên expectancy & kỷ luật, không tối ưu số lệnh.
Đọc config/bot_edge_modes.v1.json; chọn mode theo rolling PF, daily R, regime anchor, Brain market_state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Trade

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _ROOT / "config" / "bot_edge_modes.v1.json"


@dataclass
class BotEdgeDecision:
    """Kết quả một lần đánh giá (mỗi cycle có thể gọi lại)."""

    selected_mode: str
    risk_multiplier: float
    tp_profile: str
    max_hold_minutes_fast: int
    max_hold_minutes_core: int
    min_signal_score: float
    allow_scale_in: bool
    allow_fast_bucket: bool
    max_concurrent_trades: int
    bot_edge_score: float
    rolling_profit_factor: float | None
    rolling_trade_count: int
    reasons: list[str] = field(default_factory=list)


def load_bot_edge_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {"enabled": False}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}


def anchor_regime_from_quotes(quotes: dict) -> str:
    """Regime từ anchor (BTC nếu có trong watchlist), else symbol đầu."""
    from core.regime.detector import derive_regime

    if not quotes:
        return "balanced"
    for k, q in quotes.items():
        if "BTC" in (k or "").upper():
            return derive_regime(q.percent_change_24h, q.volume_24h)
    k0, q0 = next(iter(quotes.items()))
    return derive_regime(q0.percent_change_24h, q0.volume_24h)


def rolling_portfolio_profit_factor(
    db: Session,
    portfolio_id: int,
    *,
    lookback_days: int = 30,
) -> tuple[float | None, int]:
    """PF toàn portfolio (mọi strategy) từ lệnh đóng gần đây."""
    since = datetime.utcnow() - timedelta(days=max(1, lookback_days))
    trades = list(
        db.scalars(
            select(Trade).where(
                Trade.portfolio_id == portfolio_id,
                Trade.action == "close",
                Trade.created_at >= since,
            )
        )
    )
    if not trades:
        return None, 0
    pnls = [float(t.pnl_usd or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    if gl <= 0:
        pf = 2.0 if gp > 0 else 0.0
    else:
        pf = gp / gl
    return float(pf), len(trades)


def effective_signal_score(signal: Any) -> float:
    q = getattr(signal, "quality_score", None)
    c = float(getattr(signal, "confidence", 0) or 0)
    if q is not None:
        try:
            return max(c, float(q))
        except (TypeError, ValueError):
            return c
    return c


def apply_tp_profile_to_signal(signal: Any, profile: str, cfg: dict[str, Any]) -> None:
    """Co/giãn khoảng cách TP (và TP extended) từ entry theo tp_profile_scales."""
    scales = (cfg.get("tp_profile_scales") or {}) if cfg else {}
    scale = float(scales.get(profile, scales.get("normal", 1.0)) or 1.0)
    if scale <= 0 or abs(scale - 1.0) < 1e-6:
        return
    entry = float(getattr(signal, "entry_price", 0) or 0)
    tp = getattr(signal, "take_profit", None)
    if entry <= 0 or tp is None:
        return
    tp = float(tp)
    side = (getattr(signal, "side", "long") or "long").lower()
    if side == "long":
        dist = tp - entry
        if dist <= 0:
            return
        signal.take_profit = entry + dist * scale
        tex = getattr(signal, "take_profit_extended", None)
        if tex is not None:
            dist_e = float(tex) - entry
            if dist_e > 0:
                signal.take_profit_extended = entry + dist_e * scale
    else:
        dist = entry - tp
        if dist <= 0:
            return
        signal.take_profit = entry - dist * scale
        tex = getattr(signal, "take_profit_extended", None)
        if tex is not None:
            dist_e = entry - float(tex)
            if dist_e > 0:
                signal.take_profit_extended = entry - dist_e * scale


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _compute_bot_edge_score(
    *,
    rolling_pf: float | None,
    n_trades: int,
    regime: str,
    daily_r: float | None,
    min_trades: int,
    weights: dict[str, Any],
) -> float:
    w_pf = float(weights.get("w_pf", 0.35) or 0.35)
    w_reg = float(weights.get("w_regime", 0.25) or 0.25)
    w_dr = float(weights.get("w_daily_r", 0.25) or 0.25)
    w_cons = float(weights.get("w_consistency", 0.15) or 0.15)
    s_pf = 0.5
    if rolling_pf is not None and n_trades >= min_trades:
        s_pf = _clip01((rolling_pf - 0.85) / 0.35)
    reg = (regime or "").strip().lower()
    if reg == "high_momentum":
        s_reg = 0.85
    elif reg == "balanced":
        s_reg = 0.65
    else:
        s_reg = 0.4
    dr = float(daily_r) if daily_r is not None else 0.0
    s_dr = _clip01(0.5 + dr / 6.0)
    s_cons = _clip01(min(1.0, n_trades / float(max(min_trades, 8))))
    total_w = w_pf + w_reg + w_dr + w_cons
    if total_w <= 0:
        return 0.5
    score = (w_pf * s_pf + w_reg * s_reg + w_dr * s_dr + w_cons * s_cons) / total_w
    return round(float(score), 4)


def _mode_params(cfg: dict[str, Any], mode: str) -> dict[str, Any]:
    modes = cfg.get("modes") or {}
    m = modes.get(mode) or modes.get(cfg.get("default_mode", "CORE"), {})
    if not isinstance(m, dict):
        m = {}
    return m


def effective_min_signal_score(
    cfg: dict[str, Any] | None,
    *,
    selected_mode: str,
    strategy_name: str,
    mode_default_min: float,
) -> float:
    """
    Per-mode / per-strategy floor for the bot-edge signal gate (confidence vs quality_score).

    Config (optional) in bot_edge_modes.v1.json::

        "bot_edge_min_by_mode": {
          "DEFENSIVE": { "default": 0.78, "mean_reversion": 0.62 },
          "CORE": { "default": 0.62, "mean_reversion": 0.60 }
        }

    Strategy keys must match ``strategy_name`` (e.g. mean_reversion). Falls back to
    ``default`` inside the mode block, then to ``mode_default_min`` from ``modes.<MODE>.min_signal_score``.
    """
    base = float(mode_default_min or 0.0)
    if not cfg:
        return base
    by_mode = cfg.get("bot_edge_min_by_mode")
    if not isinstance(by_mode, dict):
        return base
    mode_key = (selected_mode or "").strip().upper()
    block = by_mode.get(mode_key)
    if block is None and selected_mode:
        block = by_mode.get(str(selected_mode).strip())
    if not isinstance(block, dict):
        return base
    strat = (strategy_name or "").strip()
    if strat:
        v = block.get(strat)
        if isinstance(v, (int, float)):
            return float(v)
    v_def = block.get("default")
    if isinstance(v_def, (int, float)):
        return float(v_def)
    return base


def compute_bot_edge_decision(
    db: Session,
    portfolio_id: int,
    *,
    quotes: dict,
    daily_realized_r: float | None,
    daily_realized_pnl_usd: float,
    risk_capital_usd: float,
    brain_market_state: str | None = None,
) -> BotEdgeDecision:
    flat = load_bot_edge_config()
    if not flat.get("enabled", True):
        m = _mode_params(flat, str(flat.get("default_mode", "CORE")))
        return BotEdgeDecision(
            selected_mode="OFF",
            risk_multiplier=1.0,
            tp_profile=str(m.get("tp_profile", "normal") or "normal"),
            max_hold_minutes_fast=int(m.get("max_hold_minutes_fast", 0) or 0),
            max_hold_minutes_core=int(m.get("max_hold_minutes_core", 0) or 0),
            min_signal_score=0.0,
            allow_scale_in=True,
            allow_fast_bucket=True,
            max_concurrent_trades=999,
            bot_edge_score=0.5,
            rolling_profit_factor=None,
            rolling_trade_count=0,
            reasons=["bot_edge disabled"],
        )

    lookback = int(flat.get("lookback_days_rolling_pf", 30) or 30)
    min_pf_global = int(flat.get("min_trades_for_rolling_pf", 8) or 8)
    rolling_pf, n_tr = rolling_portfolio_profit_factor(db, portfolio_id, lookback_days=lookback)
    anchor_regime = anchor_regime_from_quotes(quotes)
    sel = flat.get("selection") or {}
    def_cfg = sel.get("defensive") or {}
    fast_cfg = sel.get("fast") or {}

    reasons: list[str] = []
    mode = str(flat.get("default_mode", "CORE") or "CORE").upper()

    def_min_trades = int(def_cfg.get("min_trades_for_pf_rule", min_pf_global) or min_pf_global)
    daily_r_max = def_cfg.get("daily_r_max")
    if daily_r_max is not None and daily_realized_r is not None:
        if float(daily_realized_r) <= float(daily_r_max):
            mode = "DEFENSIVE"
            reasons.append(f"daily_r<={daily_r_max}")

    pf_max = def_cfg.get("rolling_pf_max")
    if mode != "DEFENSIVE" and pf_max is not None and rolling_pf is not None and n_tr >= def_min_trades:
        if float(rolling_pf) <= float(pf_max):
            mode = "DEFENSIVE"
            reasons.append(f"rolling_pf<={pf_max}")

    for r in def_cfg.get("anchor_regimes") or []:
        if mode != "DEFENSIVE" and (anchor_regime or "").lower() == str(r).lower():
            mode = "DEFENSIVE"
            reasons.append(f"regime={anchor_regime}")
            break

    bms = (brain_market_state or "").strip().upper()
    for s in def_cfg.get("brain_market_states") or []:
        if mode != "DEFENSIVE" and bms == str(s).strip().upper():
            mode = "DEFENSIVE"
            reasons.append(f"brain_market={bms}")
            break

    if mode != "DEFENSIVE":
        fast_min_tr = int(fast_cfg.get("min_trades_for_pf_rule", 12) or 12)
        daily_r_min = fast_cfg.get("daily_r_min")
        daily_ok = True
        if daily_r_min is not None and daily_realized_r is not None:
            daily_ok = float(daily_realized_r) >= float(daily_r_min)
        if not daily_ok:
            reasons.append("daily_r_below_fast_floor")
        else:
            pf_min = fast_cfg.get("rolling_pf_min")
            regimes_ok = {str(x).lower() for x in (fast_cfg.get("anchor_regimes_allow") or [])}
            reg_ok = not regimes_ok or (anchor_regime or "").lower() in regimes_ok
            if (
                pf_min is not None
                and rolling_pf is not None
                and n_tr >= fast_min_tr
                and float(rolling_pf) >= float(pf_min)
                and reg_ok
            ):
                mode = "FAST"
                reasons.append("fast_criteria_met")
            else:
                reasons.append("core_default")

    if mode == "DEFENSIVE" and not reasons:
        reasons.append("defensive_default")

    m = _mode_params(flat, mode)
    score_weights = flat.get("bot_edge_score") or {}
    bot_score = _compute_bot_edge_score(
        rolling_pf=rolling_pf,
        n_trades=n_tr,
        regime=anchor_regime,
        daily_r=daily_realized_r,
        min_trades=min_pf_global,
        weights=score_weights,
    )

    return BotEdgeDecision(
        selected_mode=mode,
        risk_multiplier=max(0.05, float(m.get("risk_multiplier", 1.0) or 1.0)),
        tp_profile=str(m.get("tp_profile", "normal") or "normal"),
        max_hold_minutes_fast=max(0, int(m.get("max_hold_minutes_fast", 0) or 0)),
        max_hold_minutes_core=max(0, int(m.get("max_hold_minutes_core", 0) or 0)),
        min_signal_score=float(m.get("min_signal_score", 0.0) or 0.0),
        allow_scale_in=bool(m.get("allow_scale_in", True)),
        allow_fast_bucket=bool(m.get("allow_fast_bucket", True)),
        max_concurrent_trades=max(1, int(m.get("max_concurrent_trades", 3) or 3)),
        bot_edge_score=bot_score,
        rolling_profit_factor=rolling_pf,
        rolling_trade_count=n_tr,
        reasons=reasons or [f"mode={mode}"],
    )
