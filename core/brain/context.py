"""Build BrainV4CycleContext once per worker tick (review + run share same ctx)."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import get_effective_kill_switch_enabled, get_effective_kill_switch_r_threshold, settings
from core.market_data.client import get_klines_1h, get_quotes_with_fallback
from core.portfolio.models import Portfolio, Trade
from core.regime.detector import derive_regime
from core.risk.daily_r import sum_daily_realized_r_from_trades

from core.brain.change_point import compute_change_point_for_symbol
from core.brain.meta_policy import btc_context_score_from_regime, choose_policy_mode
from core.brain.runtime_state import (
    hysteresis_pick,
    load_runtime_state,
    new_trace_id,
    save_runtime_state,
    update_after_cycle,
)
from core.brain.state_inference import infer_market_state
from core.brain.types import BrainV4CycleContext, ChangePointResult, MarketState

_ROOT = Path(__file__).resolve().parent.parent.parent


def _cfg() -> dict[str, Any]:
    p = _ROOT / "config" / "brain_v4.v1.json"
    if not p.exists():
        p = _ROOT / "config" / "brain_v4.v1.example.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _portfolio_stress(daily_realized_pnl_usd: float, capital_usd: float, max_daily_loss_pct: float) -> float:
    cap = max(float(capital_usd or 0), 1e-9)
    limit = cap * max(float(max_daily_loss_pct or 0.03), 1e-9)
    if daily_realized_pnl_usd >= 0:
        return 0.0
    return min(1.0, abs(daily_realized_pnl_usd) / limit)


def _daily_realized_for_portfolio(db: Session, portfolio_id: int) -> tuple[float, float | None]:
    today_start = datetime.combine(date.today(), time.min)
    today_end = today_start + timedelta(days=1)
    closed_today_q = select(Trade).where(
        Trade.action == "close",
        Trade.created_at >= today_start,
        Trade.created_at < today_end,
        Trade.portfolio_id == portfolio_id,
    )
    closed_today = list(db.scalars(closed_today_q))
    daily_realized = round(sum(t.pnl_usd for t in closed_today), 2)
    daily_realized_r = sum_daily_realized_r_from_trades(closed_today)
    return daily_realized, daily_realized_r


def build_brain_v4_cycle_context(
    *,
    symbols: list[str],
    quotes: dict[str, Any],
    daily_realized_pnl_usd: float,
    daily_realized_r: float | None,
    portfolio_capital_usd: float,
    max_daily_loss_pct: float,
    brain_cycle_id: str | None = None,
    db: Session | None = None,
    portfolio_id: int | None = None,
) -> BrainV4CycleContext | None:
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return None

    rt = load_runtime_state()
    trace_id = new_trace_id()
    previous_policy_mode = str(rt.policy_mode or "NORMAL")
    q = dict(quotes)
    if "BTC" not in q:
        try:
            q.update(get_quotes_with_fallback(["BTC"]))
        except Exception:
            pass

    btc_quote = q.get("BTC")
    if not btc_quote:
        btc_change, btc_vol = 0.0, 0.0
        btc_reg = "balanced"
    else:
        btc_change = float(btc_quote.percent_change_24h or 0)
        btc_vol = float(btc_quote.volume_24h or 0)
        btc_reg = derive_regime(btc_change, btc_vol)

    try:
        btc_klines = get_klines_1h("BTC", limit=24)
    except Exception:
        btc_klines = []

    cp_btc = compute_change_point_for_symbol(
        btc_klines,
        "long",
        prev_btc_regime=rt.last_btc_regime or btc_reg,
        curr_btc_regime=btc_reg,
        funding_rate=None,
    )

    alt_regimes: list[str] = []
    symbol_change_point_results: dict[str, ChangePointResult] = {}
    for s in symbols:
        su = (s or "").strip().upper()
        if not su:
            continue
        sq = q.get(su)
        if sq:
            alt_regimes.append(
                derive_regime(float(sq.percent_change_24h or 0), float(sq.volume_24h or 0))
            )
        try:
            kl_s = get_klines_1h(su, limit=25)
        except Exception:
            kl_s = []
        cp_s = compute_change_point_for_symbol(
            kl_s,
            "long",
            prev_btc_regime=rt.last_btc_regime or btc_reg,
            curr_btc_regime=btc_reg,
            funding_rate=None,
        )
        symbol_change_point_results[su] = cp_s

    si_cfg = cfg.get("state_inference", {})
    raw_market, mconf = infer_market_state(
        btc_change,
        btc_vol,
        alt_regimes,
        cp_btc.change_point_score,
        cfg=cfg,
    )
    emergency_states = frozenset(si_cfg.get("emergency_market_states", ["SHOCK_UNSTABLE"]))
    margin = float(si_cfg.get("state_switch_margin", 0.12))
    market_state_s, mconf2 = hysteresis_pick(
        raw_market,
        mconf,
        rt.market_state,
        float(si_cfg.get("default_prev_confidence", 0.55)),
        switch_margin=margin,
        emergency_states=emergency_states,
    )
    market_state: MarketState = market_state_s  # type: ignore[assignment]

    regime_stab = 0.85 if (rt.last_btc_regime == btc_reg or not rt.last_btc_regime) else 0.42

    stress = _portfolio_stress(daily_realized_pnl_usd, portfolio_capital_usd, max_daily_loss_pct)
    kill_on = get_effective_kill_switch_enabled()
    thr = float(get_effective_kill_switch_r_threshold())
    kill_near = bool(
        kill_on
        and daily_realized_r is not None
        and daily_realized_r <= -0.85 * thr
    )

    policy = choose_policy_mode(
        market_state=market_state,
        portfolio_stress_score=stress,
        change_point_market=cp_btc,
        btc_context_score=btc_context_score_from_regime(btc_reg),
        regime_stability_proxy=regime_stab,
        kill_risk_near_limit=kill_near,
        rt=rt,
        cfg=cfg,
    )

    meta_cfg = cfg.get("meta_policy", {})
    emergency = bool(
        kill_near
        or market_state == "SHOCK_UNSTABLE"
        or cp_btc.change_point_score >= float(meta_cfg.get("cp_force_exit_only", 0.92))
    )
    cooldown_blocked = "cooldown_kept_previous" in (policy.policy_reason_codes or [])

    market_decision_trace_id = str(uuid.uuid4())

    config_hash_v4 = ""
    if brain_cycle_id and db is not None:
        try:
            from core.brain.persistence import (
                insert_policy_mode_event,
                p1_persistence_enabled,
                sha256_brain_v4_config,
                start_brain_cycle,
            )

            if p1_persistence_enabled():
                config_hash_v4 = sha256_brain_v4_config()
                start_brain_cycle(
                    db,
                    brain_cycle_id,
                    portfolio_id,
                    config_hash_v4,
                    market_decision_trace_id=market_decision_trace_id,
                )
                insert_policy_mode_event(
                    db,
                    cycle_id=brain_cycle_id,
                    decision_trace_id=market_decision_trace_id,
                    previous_mode=previous_policy_mode,
                    policy=policy,
                    emergency_override=emergency,
                    cooldown_blocked=cooldown_blocked,
                    config_hash=config_hash_v4,
                )
        except Exception:
            config_hash_v4 = ""

    if not config_hash_v4:
        try:
            from core.brain.persistence import sha256_brain_v4_config

            config_hash_v4 = sha256_brain_v4_config()
        except Exception:
            config_hash_v4 = ""

    update_after_cycle(rt, btc_regime=btc_reg, market_state=market_state, policy_mode=policy.active_policy_mode)
    for sym, cpres in symbol_change_point_results.items():
        rt.last_cp_by_symbol[sym] = float(cpres.change_point_score)
    save_runtime_state(rt)

    symbol_change_points = {k: v.change_point_score for k, v in symbol_change_point_results.items()}

    return BrainV4CycleContext(
        enabled=True,
        market_state=market_state,
        market_state_confidence=mconf2,
        policy=policy,
        change_point_market=cp_btc,
        portfolio_stress_score=stress,
        kill_risk_near_limit=kill_near,
        btc_regime=btc_reg,
        regime_stability_proxy=regime_stab,
        trace_id=trace_id,
        brain_cycle_id=brain_cycle_id,
        config_hash_v4=config_hash_v4,
        previous_policy_mode=previous_policy_mode,
        symbol_change_points=symbol_change_points,
        symbol_change_point_results=symbol_change_point_results,
        market_decision_trace_id=market_decision_trace_id,
        symbol_decision_trace_ids={},
    )


def build_brain_v4_tick_context(
    db: Session,
    *,
    portfolio_name: str,
    symbols: list[str],
    brain_cycle_id: str,
) -> BrainV4CycleContext | None:
    """One call at worker tick start: same ctx for review_positions_and_act + run."""
    portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
    if not portfolio:
        return None
    dr, drr = _daily_realized_for_portfolio(db, portfolio.id)
    sym_list = list({(s or "").strip().upper() for s in symbols if (s or "").strip()})
    quotes = get_quotes_with_fallback(sym_list + ["BTC"])
    return build_brain_v4_cycle_context(
        symbols=sym_list,
        quotes=quotes,
        daily_realized_pnl_usd=dr,
        daily_realized_r=drr,
        portfolio_capital_usd=float(getattr(portfolio, "capital_usd", 0) or 0),
        max_daily_loss_pct=float(settings.max_daily_loss_pct),
        brain_cycle_id=brain_cycle_id,
        db=db,
        portfolio_id=portfolio.id,
    )


def should_block_cycle_symbol(v4: BrainV4CycleContext | None, symbol: str) -> bool:
    """
    When True, skip entire per-symbol branch in SimulationCycle.run (no new entries, no scale-in).
    Used for EXIT_ONLY and zero size multiplier per V4 policy templates.
    """
    del symbol  # reserved for per-symbol overrides later
    if not v4:
        return False
    if v4.policy.active_policy_mode == "EXIT_ONLY":
        return True
    if v4.policy.modifiers.size_multiplier <= 0:
        return True
    return False
