from __future__ import annotations

import logging
import time as _time
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.market_data.client import get_market_client, get_quotes_with_fallback, get_klines_1h, get_klines_4h
from core.orchestration.cycle_market import (
    CycleMarketSnapshot,
    build_cycle_market_snapshot,
    klines_1h_last_n,
    klines_4h_last_n,
    merge_quotes_for_positions,
)
from core.orchestration.scale_in_rescan import (
    record_scale_in_max_reached,
    should_skip_scale_in_rescan,
    track_position_qty_for_scale_in,
)
from core.regime.detector import derive_regime
from core.strategies.implementations import build_strategy_set
from core.risk.engine import RiskEngine, RiskDecision, effective_risk_capital_usd
from core.risk.daily_r import MIN_RISK_USD_FOR_R_AGGREGATION, sum_daily_realized_r_from_trades
from core.profit.volatility_guard import check_volatility_guard, load_profit_config
from core.profit.bot_edge_controller import (
    apply_tp_profile_to_signal,
    compute_bot_edge_decision,
    effective_min_signal_score,
    effective_signal_score,
    load_bot_edge_config,
)
from core.profit.position_sizer import (
    apply_dynamic_sizing,
    get_confidence_multiplier,
    get_regime_score,
)
from core.profit.strategy_weight_engine import (
    compute_combo_multipliers,
    compute_strategy_weights,
    get_combo_multiplier,
    get_strategy_weight,
)
from core.signals.entry_timing import (
    evaluate_entry_timing,
    load_entry_timing_config,
    record_entry_opened,
)
from core.signals.entry_context_gates import (
    evaluate_entry_context_gates,
    load_entry_context_gates_config,
    maybe_log_context_pass,
)
from core.profit.signal_level_adjust import adjust_signal_sl_tp
from core.observability.decision_log import log_decision
from core.observability.reject_reason_codes import classify_risk_reject_reason_code
from core.profit.allocation_engine import compute_allocation_mult
from core.config import settings, get_effective_single_strategy_mode, get_effective_max_consecutive_loss_stop
from core.portfolio.capital_split import (
    assign_capital_bucket_to_signal,
    CapitalSplitManager,
    consecutive_loss_streak_for_bucket,
    daily_realized_by_bucket,
    load_capital_split_config,
    normalize_bucket,
    open_position_counts,
)
from core.portfolio.correlation_guard import correlation_guard_rejects_fast_entry
from core.orchestration.regime_strategy_filter import (
    filter_and_order_strategies,
    load_regime_strategy_config,
)
from core.orchestration.exit_guards import fast_no_follow_through_should_close
from core.risk.quick_sizing import estimate_max_size_usd_from_risk, is_likely_below_min_position_usd
from core.risk.sizing_reject_diagnosis import (
    build_sizing_stage_diagnostics,
    classify_post_sizing_reject,
    diagnose_size_too_small,
    effective_internal_min_trade_usd,
    exchange_qty_preview,
)
from core.risk.under_risk_sizing import apply_risk_ceiling_and_under_risk_floor
from core.execution.binance_futures import try_exchange_lot_for_executor
from core.risk.trade_r_metrics import risk_usd_for_full_close, planned_r_multiple
from core.risk.candidate_quality import load_candidate_quality_config
from core.orchestration.runtime_strategy_governance import filter_strategy_objects
from core.execution import get_execution_backend
from core.execution.simulator import PaperExecutionSimulator
from core.journal.service import JournalService
from core.journal.context_builder import build_entry_context
from core.portfolio.models import Portfolio, Position, DailySnapshot, Trade
from core.strategies.base import StrategySignal
from core.strategies.short import evaluate_short, ShortSignal
from core.strategies.short.short_config import load_short_config
from core.intelligence import build_token_features, classify_token, route_for_profile, TokenProfile
from core.intelligence.intelligence_config import load_classification_config
from core.position import ScaleInEngine, load_scale_in_config, ScaleInAction
from core.position.scale_in_queries import last_scale_in_at
from core.brain.context import should_block_cycle_symbol
from core.brain.types import BrainV4CycleContext

# Cache token features + profile + routing 120s — log chỉ khi refresh (document/request: giảm TOKEN_FEATURES_BUILT mỗi 10s).
_TOKEN_INTEL_CACHE: dict[str, tuple[float, dict, "TokenProfile", object]] = {}
_TOKEN_INTEL_TTL = 120.0


def _build_sizing_trace_payload(
    *,
    post_risk_engine_usd: float,
    size_after_vol: float,
    after_dynamic_usd: float,
    after_combo_usd: float,
    pre_modifier_usd: float,
    post_policy_usd: float,
    post_modifier_usd: float,
    signal: StrategySignal,
    regime: str,
    profit_cfg: dict,
    strategy_weight: float,
    portfolio_heat_mult: float,
    entry_combo_mult: float,
    confidence_mult: float,
    regime_score: float,
    mod_breakdown: dict[str, Any],
    bot_edge_mult: float,
    available_cash: float,
    cap_quick: float,
    rp_quick: float,
    min_notional_usd: float = 25.0,
) -> dict[str, Any]:
    est = estimate_max_size_usd_from_risk(
        signal,
        available_cash=available_cash,
        capital_usd_for_risk=cap_quick,
        risk_pct=rp_quick,
    )
    return {
        "post_risk_engine_usd": round(float(post_risk_engine_usd), 4),
        "after_volatility_guard_usd": round(float(size_after_vol), 4),
        "confidence_mult": round(float(confidence_mult), 4),
        "regime_score": round(float(regime_score), 4),
        "strategy_weight": round(float(strategy_weight), 4),
        "portfolio_heat_mult": round(float(portfolio_heat_mult), 4),
        "after_dynamic_sizing_usd": round(float(after_dynamic_usd), 4),
        "entry_combo_mult": round(float(entry_combo_mult), 4),
        "after_combo_rounded_usd": round(float(after_combo_usd), 4),
        "pre_brain_v4_policy_usd": round(float(pre_modifier_usd), 4),
        "post_policy_usd": round(float(post_policy_usd), 4),
        "policy_modifier_breakdown": mod_breakdown or {},
        "bot_edge_risk_mult": round(float(bot_edge_mult), 4),
        "post_all_modifiers_usd": round(float(post_modifier_usd), 4),
        "min_notional_usd": float(min_notional_usd),
        "available_cash_usd": round(float(available_cash), 4),
        "estimate_max_from_risk_usd": round(float(est), 4),
    }


def _attach_policy_squeeze_reject_audit(
    payload: dict[str, Any],
    *,
    symbol: str,
    strategy_name: str | None,
    reason_code: str,
    post_modifier_usd: float,
    eff_min_trade_usd: float,
    profit_cfg: dict[str, Any],
    single_strategy_mode: str,
    blocking_stage: str | None,
    sizing_stage_diagnostics: dict[str, Any],
    mod_breakdown: dict[str, Any] | None,
    pre_modifier_usd: float,
    post_policy_usd: float,
    bot_edge_mult: float,
) -> None:
    """
    Top-level audit on entry_rejected payload when policy/brain/bot-edge chain squeezed size
    below the effective floor (reason REDUCED_TOO_MUCH_BY_POLICY). Keeps nested sizing_trace
    but adds a stable, grep-friendly object for logs and dashboards.
    """
    if reason_code != "REDUCED_TOO_MUCH_BY_POLICY":
        return
    sizing = (profit_cfg or {}).get("sizing") or {}
    payload["policy_squeeze_detail"] = {
        "final_size_usd": round(float(post_modifier_usd), 4),
        "required_min_usd": round(float(eff_min_trade_usd), 4),
        "blocking_stage": blocking_stage or "",
        "sizing_stage_diagnostics": dict(sizing_stage_diagnostics),
        "policy_modifier_breakdown": dict(mod_breakdown or {}),
        "pre_brain_v4_policy_usd": round(float(pre_modifier_usd), 4),
        "post_brain_v4_policy_usd": round(float(post_policy_usd), 4),
        "bot_edge_risk_mult": round(float(bot_edge_mult), 4),
        "internal_min_trade_usd_config": float(sizing.get("internal_min_trade_usd", 25) or 25),
        "mr_only_min_trade_usd_config": sizing.get("mr_only_min_trade_usd"),
        "single_strategy_mode": (single_strategy_mode or "").strip() or None,
    }
    logging.getLogger(__name__).info(
        "POLICY_SQUEEZE_REJECT symbol=%s strategy=%s final_usd=%.4f required_min_usd=%.4f "
        "blocking_stage=%s internal_min_cfg=%s mr_only_min_cfg=%s single_strategy=%r",
        symbol,
        strategy_name or "",
        float(post_modifier_usd),
        float(eff_min_trade_usd),
        blocking_stage or "",
        sizing.get("internal_min_trade_usd", 25),
        sizing.get("mr_only_min_trade_usd"),
        (single_strategy_mode or "").strip(),
    )


def _build_risk_efficiency_fields(
    *,
    final_size_usd: float,
    risk_ceiling_usd: float,
    effective_min_trade_usd: float,
    sizing_cfg: dict[str, Any] | None,
    under_risk_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "risk_ceiling_usd": round(float(risk_ceiling_usd), 4),
        "final_size_usd": round(float(final_size_usd), 4),
        "effective_min_trade_usd": round(float(effective_min_trade_usd), 4),
        "under_risk_floor_fraction": float((sizing_cfg or {}).get("under_risk_min_fraction_of_risk_ceiling", 0) or 0),
    }
    rc = float(risk_ceiling_usd or 0)
    out["risk_efficiency_ratio"] = round(float(final_size_usd) / rc, 6) if rc > 0 else None
    ur = (under_risk_meta or {}).get("under_risk_rescale") if isinstance(under_risk_meta, dict) else None
    out["under_risk_rescale_applied"] = bool(ur)
    if isinstance(ur, dict):
        out["under_risk_rescale_from_usd"] = ur.get("from_usd")
        out["under_risk_rescale_to_usd"] = ur.get("to_usd")
    return out


def _native_signal_log_slice(signal: StrategySignal) -> dict:
    """Structured fields for decision_log / experiments (comparability)."""
    return {
        "setup_quality": getattr(signal, "setup_quality", None),
        "entry_style": getattr(signal, "entry_style", None),
        "extension_score": getattr(signal, "extension_score", None),
        "quality_score": getattr(signal, "quality_score", None),
        "side": getattr(signal, "side", None),
        "regime": getattr(signal, "regime", None),
    }


def _decision_candle_id_1h(klines: list | None) -> str | None:
    """Dedupe key: last closed 1h bar open time (ms) from Binance-style kline rows."""
    if not klines:
        return None
    row = klines[-1]
    if not row:
        return None
    try:
        return f"1h:{int(row[0])}"
    except (TypeError, ValueError, IndexError):
        return None


def _apply_entry_edge_pipeline(
    signal: StrategySignal,
    *,
    symbol: str,
    price_now: float,
    klines_full: list,
    combo_mults: dict[str, float],
    combo_cfg: dict,
    current_regime: str | None = None,
    volume_24h: float | None = None,
) -> tuple[bool, dict | None, float, dict]:
    """
    Combo → entry context gates (native + recent + pullback) → entry timing → ATR/cap SL-TP adjust.
    Returns (ok, rejected_record_or_none, combo_multiplier, entry_timing_cfg).
    """
    cm = 1.0
    candle_id = _decision_candle_id_1h(klines_full or [])
    if combo_cfg.get("enabled", True):
        cm = get_combo_multiplier(
            combo_mults,
            signal.strategy_name,
            symbol,
            current_regime or getattr(signal, "regime", None),
            side=getattr(signal, "side", None),
        )
        if cm <= 0:
            combo_meta = {
                "combo_multiplier": 0.0,
                "regime": current_regime or getattr(signal, "regime", None),
                "side": getattr(signal, "side", None),
                "native_signal": _native_signal_log_slice(signal),
            }
            if candle_id:
                combo_meta["candle_id"] = candle_id
            rec = {
                "symbol": signal.symbol,
                "strategy_name": signal.strategy_name,
                "reason": "Combo strategy+symbol(+regime+side) underperforming (rolling PF/WR); entry blocked.",
                "reason_code": "COMBO_BLOCKED_EDGE",
                "meta": combo_meta,
            }
            log_decision(
                "entry_rejected",
                combo_meta,
                symbol=symbol,
                strategy_name=signal.strategy_name,
                reason_code=rec["reason_code"],
            )
            return False, rec, 0.0, {}

    ctx_cfg = load_entry_context_gates_config()
    ctx_res = evaluate_entry_context_gates(
        signal,
        symbol=symbol,
        strategy_name=(signal.strategy_name or "").strip(),
        side=signal.side or "long",
        price_now=float(price_now),
        klines=klines_full or [],
        volume_24h=volume_24h,
        cfg=ctx_cfg,
    )
    if not ctx_res.ok:
        ctx_payload = {**ctx_res.details, "native_signal": _native_signal_log_slice(signal)}
        if candle_id:
            ctx_payload["candle_id"] = candle_id
        rec = {
            "symbol": signal.symbol,
            "strategy_name": signal.strategy_name,
            "reason": ctx_res.message,
            "reason_code": ctx_res.reason_code,
            "meta": ctx_payload,
        }
        log_decision(
            "entry_rejected",
            ctx_payload,
            symbol=symbol,
            strategy_name=signal.strategy_name,
            reason_code=ctx_res.reason_code,
        )
        return False, rec, cm, {}
    maybe_log_context_pass(
        log_decision,
        symbol=symbol,
        strategy_name=(signal.strategy_name or "").strip(),
        details=ctx_res.details,
        cfg=ctx_cfg,
    )

    eti_cfg = load_entry_timing_config()
    et = evaluate_entry_timing(
        strategy_name=signal.strategy_name,
        symbol=symbol,
        side=signal.side or "long",
        price_now=price_now,
        klines_1h=klines_full,
        cfg=eti_cfg,
    )
    if not et.ok:
        payload = {**et.details, "native_signal": _native_signal_log_slice(signal)}
        if candle_id:
            payload["candle_id"] = candle_id
        rec = {
            "symbol": signal.symbol,
            "strategy_name": signal.strategy_name,
            "reason": et.message,
            "reason_code": et.reason_code,
            "meta": payload,
        }
        log_decision(
            "entry_rejected",
            payload,
            symbol=symbol,
            strategy_name=signal.strategy_name,
            reason_code=et.reason_code,
        )
        return False, rec, cm, eti_cfg

    sl_adj = eti_cfg.get("signal_levels") or {}
    if getattr(signal, "levels_from_structure", False):
        adj_meta = {
            "adjusted": False,
            "skipped": "levels_from_structure",
            "entry_zone": [
                getattr(signal, "entry_zone_low", None),
                getattr(signal, "entry_zone_high", None),
            ],
            "take_profit_extended": getattr(signal, "take_profit_extended", None),
            "atr_estimate_1h": getattr(signal, "atr_estimate_1h", None),
            "structure_meta": getattr(signal, "structure_meta", None),
            "native_signal": _native_signal_log_slice(signal),
        }
        log_decision(
            "signal_levels_passthrough",
            adj_meta,
            symbol=symbol,
            strategy_name=signal.strategy_name,
            reason_code="STRUCTURAL_LEVELS",
        )
    else:
        adj_meta = adjust_signal_sl_tp(signal, klines_full, sl_adj)
        if adj_meta.get("adjusted"):
            signal.rationale = (
                (signal.rationale or "")
                + f" | SL/TP ATR+caps: SL~{adj_meta.get('sl_pct_after')}%, TP~{adj_meta.get('tp_pct_after')}%."
            )
            log_decision(
                "signal_levels_adjusted",
                adj_meta,
                symbol=symbol,
                strategy_name=signal.strategy_name,
                reason_code="SIGNAL_LEVELS_ATR_CAP",
            )
    return True, None, cm, eti_cfg


def _log_scale_in_rejected(symbol: str, side: str, reason: str, position: Position, si_flat: dict) -> None:
    """Log từ chối scale-in; thêm scale_in_count / max hiệu lực để debug config vs runtime."""
    log = logging.getLogger(__name__)
    suffix = ""
    if reason == "max_scale_in_reached":
        cnt = int(getattr(position, "scale_in_count", 0) or 0)
        mx = int(si_flat.get("max_scale_in_times", 1) or 0)
        suffix = f" | scale_in_count={cnt} max_scale_in_times_config={mx}"
    elif reason == "scale_in_cooldown_active":
        cd = float(si_flat.get("cooldown_between_scale_ins_seconds", 0) or 0)
        suffix = f" | cooldown_between_scale_ins_sec={cd}"
    log.info("SCALE_IN_REJECTED symbol=%s side=%s reason=%s%s", symbol, side, reason, suffix)


def _persist_brain_v4_symbol_events(
    db: Session,
    ctx: BrainV4CycleContext,
    symbol: str,
    quote: Any,
    regime: str,
    klines_full: list,
) -> None:
    cy = ctx.brain_cycle_id
    if not cy:
        return
    try:
        from core.brain.persistence import (
            insert_change_point_event,
            insert_state_inference_event,
            p1_persistence_enabled,
        )
        from core.brain.state_inference import infer_token_state

        if not p1_persistence_enabled():
            return
        sym_trace = ctx.symbol_decision_trace_ids.get(symbol)
        if not sym_trace:
            sym_trace = str(uuid.uuid4())
            ctx.symbol_decision_trace_ids[symbol] = sym_trace
        mkt_trace = ctx.market_decision_trace_id or None
        h = ctx.config_hash_v4 or ""
        chg = float(getattr(quote, "percent_change_24h", 0) or 0)
        tok, ct = infer_token_state(chg, klines_full, ctx.market_state)
        cp_res = ctx.symbol_change_point_results.get(symbol)
        if cp_res is None:
            from core.brain.change_point import compute_change_point_for_symbol
            from core.brain.runtime_state import load_runtime_state

            rt0 = load_runtime_state()
            cp_res = compute_change_point_for_symbol(
                klines_full,
                "long",
                prev_btc_regime=rt0.last_btc_regime or ctx.btc_regime,
                curr_btc_regime=ctx.btc_regime,
                funding_rate=None,
            )
        feat: dict[str, Any] = {
            "regime": regime,
            "btc_regime": ctx.btc_regime,
            "trace_id": ctx.trace_id,
            "policy_mode": ctx.policy.active_policy_mode,
            "decision_trace_id": sym_trace,
            "market_decision_trace_id": mkt_trace,
        }
        insert_state_inference_event(
            db,
            cycle_id=cy,
            decision_trace_id=sym_trace,
            market_decision_trace_id=mkt_trace,
            symbol=symbol,
            market_state=str(ctx.market_state),
            token_state=str(tok),
            position_state=None,
            conf_m=float(ctx.market_state_confidence),
            conf_t=float(ct),
            conf_p=None,
            feature_snapshot=feat,
            reason_codes=[],
            config_hash=h,
        )
        insert_change_point_event(
            db,
            cycle_id=cy,
            decision_trace_id=sym_trace,
            market_decision_trace_id=mkt_trace,
            symbol=symbol,
            cp=cp_res,
            config_hash=h,
        )
    except Exception:
        pass


def _brain_v4_scale_in_gate_ok(
    brain_v4_ctx: BrainV4CycleContext | None,
    symbol: str,
    quote: Any,
    klines: list,
    position: Position,
    quotes: dict[str, Any],
) -> bool:
    if not brain_v4_ctx:
        return True
    from core.brain.change_point import compute_change_point_for_symbol
    from core.brain.policy_apply import scale_in_policy_gate
    from core.brain.runtime_state import load_runtime_state
    from core.brain.state_inference import infer_position_state, infer_token_state

    rt_si = load_runtime_state()
    qbtc_lp = quotes.get("BTC")
    btc_r_lp = (
        derive_regime(float(qbtc_lp.percent_change_24h or 0), float(qbtc_lp.volume_24h or 0))
        if qbtc_lp
        else brain_v4_ctx.btc_regime
    )
    cp_live = compute_change_point_for_symbol(
        klines,
        position.side or "long",
        prev_btc_regime=rt_si.last_btc_regime or btc_r_lp,
        curr_btc_regime=btc_r_lp,
        funding_rate=None,
    )
    prev_cp = rt_si.last_cp_by_symbol.get(symbol)
    chg_t = float(getattr(quote, "percent_change_24h", 0) or 0)
    tok_st, _ = infer_token_state(chg_t, klines, brain_v4_ctx.market_state)
    direction = 1 if position.side == "long" else -1
    risk_u = None
    if position.stop_loss is not None and position.quantity:
        risk_u = abs(float(position.entry_price) - float(position.stop_loss)) * float(position.quantity)
    pnl_u = (float(getattr(quote, "price", 0) or 0) - float(position.entry_price)) * direction * float(
        position.quantity or 0
    )
    unreal_r = (pnl_u / risk_u) if risk_u and risk_u > 0 else 0.0
    pos_st, _ = infer_position_state(
        side=position.side or "long",
        entry_price=float(position.entry_price),
        stop_loss=float(position.stop_loss) if position.stop_loss is not None else None,
        price_now=float(getattr(quote, "price", 0) or 0),
        unrealized_r=unreal_r,
        token_state=tok_st,
        market_state=brain_v4_ctx.market_state,
        change_point_score=cp_live.change_point_score,
    )
    return scale_in_policy_gate(
        brain_v4_ctx,
        symbol,
        change_point_score=cp_live.change_point_score,
        position_state=str(pos_st),
        prev_cp=prev_cp,
    )


def _get_volatility_tier_for_position(
    symbol: str,
    quote: object | None,
    klines_1h: list,
    now_mono: float,
) -> str:
    """Lấy volatility_tier (low/medium/high/extreme) cho symbol; dùng cache token intel nếu còn hạn, không thì build từ quote + klines. Default medium."""
    try:
        class_cfg = load_classification_config()
        if not class_cfg.get("enabled", True):
            return "medium"
        cached = _TOKEN_INTEL_CACHE.get(symbol)
        if cached is not None and cached[0] >= now_mono:
            return getattr(cached[2], "volatility_tier", None) or "medium"
        if quote is None or not klines_1h:
            return "medium"
        features = build_token_features(symbol, quote, klines_1h, class_cfg)
        profile = classify_token(symbol, features, class_cfg)
        return getattr(profile, "volatility_tier", None) or "medium"
    except Exception:
        return "medium"


class SimulationCycle:
    def __init__(self):
        self.client = get_market_client()
        self.strategies = build_strategy_set()
        self.risk = RiskEngine()
        self.execution = get_execution_backend()
        self.journal = JournalService()

    def _risk_assess_entry(
        self,
        signal: StrategySignal,
        available_cash: float,
        *,
        daily_realized: float,
        daily_realized_r: float | None,
        risk_capital_usd: float,
        open_positions_total: int,
        open_core: int,
        open_fast: int,
        daily_core: float,
        daily_fast: float,
        consecutive_loss_core: int,
        consecutive_loss_fast: int,
        consecutive_loss_all: int,
        override_risk_pct: float | None,
        cs_mgr: CapitalSplitManager,
        bot_edge_max_concurrent: int | None = None,
    ) -> RiskDecision:
        """Risk cho mở lệnh mới — hỗ trợ capital split (core/fast) khi cs_mgr.enabled."""
        bucket = normalize_bucket(getattr(signal, "capital_bucket", None))
        if not cs_mgr.enabled:
            return self.risk.assess(
                signal,
                available_cash,
                open_positions_total,
                daily_realized,
                daily_realized_r=daily_realized_r,
                consecutive_loss_count=consecutive_loss_all,
                override_risk_pct=override_risk_pct,
                capital_usd_for_risk=risk_capital_usd,
                max_concurrent_trades_override=bot_edge_max_concurrent,
            )
        if bucket == "fast":
            cap_nf = cs_mgr.max_notional_usd_fast()
            mc_f = cs_mgr.max_concurrent_fast()
            if bot_edge_max_concurrent is not None:
                mc_f = min(mc_f, int(bot_edge_max_concurrent))
            return self.risk.assess(
                signal,
                available_cash,
                open_positions_total,
                daily_realized,
                daily_realized_r=daily_realized_r,
                consecutive_loss_count=consecutive_loss_fast,
                override_risk_pct=override_risk_pct,
                capital_usd_for_risk=risk_capital_usd,
                capital_scope="fast",
                open_positions_in_scope=open_fast,
                daily_realized_pnl_in_scope=daily_fast,
                risk_capital_for_scope=cs_mgr.fast_capital_usd(),
                max_concurrent_in_scope=mc_f,
                max_daily_loss_pct_in_scope=cs_mgr.max_daily_loss_fast_pct(),
                consecutive_loss_in_scope=consecutive_loss_fast,
                max_consecutive_loss_for_scope=cs_mgr.max_consecutive_loss_fast(),
                max_position_usd_cap=cap_nf if cap_nf > 0 else None,
            )
        mc_c = settings.max_concurrent_trades
        if bot_edge_max_concurrent is not None:
            mc_c = min(mc_c, int(bot_edge_max_concurrent))
        return self.risk.assess(
            signal,
            available_cash,
            open_positions_total,
            daily_realized,
            daily_realized_r=daily_realized_r,
            consecutive_loss_count=consecutive_loss_core,
            override_risk_pct=override_risk_pct,
            capital_usd_for_risk=risk_capital_usd,
            capital_scope="core",
            open_positions_in_scope=open_core,
            daily_realized_pnl_in_scope=daily_core,
            risk_capital_for_scope=cs_mgr.core_capital_usd(),
            max_concurrent_in_scope=mc_c,
            max_daily_loss_pct_in_scope=settings.max_daily_loss_pct,
            consecutive_loss_in_scope=consecutive_loss_core,
            max_consecutive_loss_for_scope=get_effective_max_consecutive_loss_stop(),
            max_position_usd_cap=None,
        )

    def run(
        self,
        db: Session,
        portfolio_name: str,
        symbols: list[str],
        market_snapshot: CycleMarketSnapshot | None = None,
        brain_v4_ctx: BrainV4CycleContext | None = None,
    ) -> dict:
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if portfolio is None:
            portfolio = Portfolio(name=portfolio_name)
            db.add(portfolio)
            db.flush()
        _cycle_started = _time.monotonic()
        if market_snapshot is not None:
            quotes: dict = {}
            for s in symbols:
                su = (s or "").strip().upper()
                if su in market_snapshot.quotes:
                    quotes[su] = market_snapshot.quotes[su]
            missing = [(s or "").strip().upper() for s in symbols if (s or "").strip().upper() not in quotes]
            if missing:
                quotes.update(get_quotes_with_fallback(missing))
        else:
            quotes = get_quotes_with_fallback(symbols)
        opened = 0
        evaluated = 0
        opened_positions: list[dict] = []
        signals_fired: list[dict] = []
        rejected_signals: list[dict] = []  # Tín hiệu có nhưng risk từ chối
        skipped_already_open: list[str] = []  # Symbol có tín hiệu nhưng đã có lệnh mở -> bỏ qua
        scale_ins_done: list[dict] = []  # Scale-in thanh cong (ADD_TO_POSITION)
        open_positions = list(
            db.scalars(select(Position).where(Position.is_open == True))
        )
        # Vốn cho % risk + daily loss USD: đồng nhất với scale-in (portfolio.capital_usd), không dùng default_capital_usd lệch DB
        risk_capital_usd = effective_risk_capital_usd(getattr(portfolio, "capital_usd", None))
        open_symbols = {p.symbol for p in open_positions}
        max_per_symbol = max(1, getattr(settings, "max_positions_per_symbol", 1))
        # Binance gộp mọi vị thế cùng (symbol, side) thành một trên sàn → chỉ nên có tối đa 1 vị thế/symbol+side để tránh mở lệnh mới mỗi cycle rồi sync gộp liên tục và rối TP/SL (-4130).
        if getattr(self.execution, "__class__", None) and getattr(self.execution.__class__, "__name__", "") == "BinanceFuturesExecutor":
            max_per_symbol = min(max_per_symbol, 1)
        entry_zone_pct = getattr(settings, "entry_zone_pct", 0.005) or 0.005
        today_start = datetime.combine(date.today(), time.min)
        today_end = today_start + timedelta(days=1)
        closed_today_q = select(Trade).where(
            Trade.action == "close",
            Trade.created_at >= today_start,
            Trade.created_at < today_end,
            Trade.portfolio_id == portfolio.id,
        )
        closed_today = list(db.scalars(closed_today_q))
        daily_realized = round(sum(t.pnl_usd for t in closed_today), 2)
        daily_realized_r = sum_daily_realized_r_from_trades(closed_today)
        last_closed = list(db.scalars(
            select(Trade).where(
                Trade.portfolio_id == portfolio.id,
                Trade.action == "close",
            ).order_by(Trade.created_at.desc()).limit(50)
        ))
        consecutive_loss_core = consecutive_loss_streak_for_bucket(last_closed, "core")
        consecutive_loss_fast = consecutive_loss_streak_for_bucket(last_closed, "fast")
        consecutive_loss_all = consecutive_loss_streak_for_bucket(last_closed, None)
        cs_cfg = load_capital_split_config()
        cs_mgr = CapitalSplitManager(cs_cfg, risk_capital_usd)
        regime_rs_cfg = load_regime_strategy_config()
        open_core, open_fast = open_position_counts(open_positions)
        daily_realized_core, daily_realized_fast = daily_realized_by_bucket(closed_today)
        cand_quality_cfg = load_candidate_quality_config()
        min_candidate_r = float(cand_quality_cfg.get("min_candidate_r_multiple", 0.8) or 0.8)
        single_strategy = get_effective_single_strategy_mode()
        strategies_to_use = (
            [s for s in self.strategies if s.name == single_strategy]
            if single_strategy
            else self.strategies
        )
        strategies_to_use = filter_strategy_objects(strategies_to_use)
        strategy_scope_names = [s.name for s in strategies_to_use]
        symbols_with_strategy_signal: set[str] = set()
        strategy_evaluate_hit_count = 0
        all_strategy_names = [s.name for s in self.strategies]
        class_cfg = load_classification_config()
        # Phase 3 v6: strategy weights và allocation (một lần mỗi cycle)
        profit_cfg_cycle = load_profit_config()
        eff_min_trade_usd = effective_internal_min_trade_usd(
            profit_cfg_cycle,
            single_strategy_mode=single_strategy,
        )
        sw_cfg = profit_cfg_cycle.get("strategy_weight") or {}
        strategy_weights = compute_strategy_weights(
            db,
            portfolio_id=portfolio.id,
            lookback_days=int(sw_cfg.get("lookback_days", 30)),
            min_sample=int(sw_cfg.get("min_sample", 5)),
            weight_min=float(sw_cfg.get("weight_min", 0.25)),
            weight_max=float(sw_cfg.get("weight_max", 1.5)),
        )
        per_sw = sw_cfg.get("per_strategy") or {}
        for name, w in per_sw.items():
            nk = (str(name) if name is not None else "").strip()
            if nk and isinstance(w, (int, float)) and float(w) > 0:
                strategy_weights[nk] = round(float(w), 4)
        open_positions_for_allocation = [
            {"strategy_name": getattr(p, "strategy_name", None) or "?"}
            for p in open_positions
        ]
        alloc_cfg = profit_cfg_cycle.get("allocation") or {}
        combo_cfg = profit_cfg_cycle.get("combo_performance") or {}
        combo_mults: dict[str, float] = {}
        if combo_cfg.get("enabled", True):
            min_sr = combo_cfg.get("min_sample_regime")
            min_sq = combo_cfg.get("min_sample_quad")
            combo_mults = compute_combo_multipliers(
                db,
                portfolio_id=portfolio.id,
                lookback_days=int(combo_cfg.get("lookback_days", 60)),
                min_sample=int(combo_cfg.get("min_sample", 15)),
                min_sample_regime=int(min_sr) if min_sr is not None else None,
                include_regime_in_key=bool(combo_cfg.get("include_regime_in_key", True)),
                include_side_in_key=bool(combo_cfg.get("include_side_in_key", False)),
                min_sample_quad=int(min_sq) if min_sq is not None else None,
                block_pf_below=float(combo_cfg.get("block_pf_below", 0.92)),
                block_wr_below=float(combo_cfg.get("block_wr_below", 0.36)),
                soft_pf_below=float(combo_cfg.get("soft_pf_below", 1.0)),
                soft_mult=float(combo_cfg.get("soft_mult", 0.5)),
            )

        if brain_v4_ctx is None:
            try:
                from core.brain.context import build_brain_v4_cycle_context

                brain_v4_ctx = build_brain_v4_cycle_context(
                    symbols=[s for s in quotes.keys()],
                    quotes=quotes,
                    daily_realized_pnl_usd=daily_realized,
                    daily_realized_r=daily_realized_r,
                    portfolio_capital_usd=float(getattr(portfolio, "capital_usd", 0) or 0),
                    max_daily_loss_pct=float(settings.max_daily_loss_pct),
                    brain_cycle_id=None,
                    db=None,
                    portfolio_id=portfolio.id,
                )
            except Exception:
                brain_v4_ctx = None

        bot_edge_cfg = load_bot_edge_config()
        bot_edge = compute_bot_edge_decision(
            db,
            portfolio.id,
            quotes=quotes,
            daily_realized_r=daily_realized_r,
            daily_realized_pnl_usd=daily_realized,
            risk_capital_usd=risk_capital_usd,
            brain_market_state=(
                str(getattr(brain_v4_ctx, "market_state", "") or "")
                if brain_v4_ctx
                else None
            ),
        )
        be_max_conc = (
            bot_edge.max_concurrent_trades
            if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF"
            else None
        )

        for symbol, quote in quotes.items():
            if brain_v4_ctx and should_block_cycle_symbol(brain_v4_ctx, symbol):
                continue
            opened_this_symbol = False
            klines_full: list = list(market_snapshot.klines_1h_by_symbol.get(symbol, [])) if market_snapshot else []
            if not klines_full:
                try:
                    klines_full = get_klines_1h(symbol, limit=25)
                except Exception:
                    klines_full = []
            if market_snapshot and symbol in market_snapshot.regime_by_symbol:
                regime = market_snapshot.regime_by_symbol[symbol]
            else:
                regime = derive_regime(quote.percent_change_24h, quote.volume_24h)
            logging.getLogger(__name__).info(
                "QUOTE_REGIME symbol=%s price=%s change_24h=%.4f volume_24h=%.0f regime=%s",
                symbol, quote.price, quote.percent_change_24h, quote.volume_24h, regime,
            )
            if brain_v4_ctx:
                _persist_brain_v4_symbol_events(db, brain_v4_ctx, symbol, quote, regime=str(regime), klines_full=klines_full)
            profile = None
            route = None
            if class_cfg.get("enabled", True):
                now_mono = _time.monotonic()
                cached = _TOKEN_INTEL_CACHE.get(symbol)
                if cached is not None and cached[0] >= now_mono:
                    _, features, profile, route = cached
                    strategies_for_symbol = filter_and_order_strategies(
                        [s for s in strategies_to_use if s.name in route.allowed_strategies],
                        regime,
                        regime_rs_cfg,
                    )
                else:
                    klines_ti = klines_full
                    features = build_token_features(symbol, quote, klines_ti, class_cfg)
                    profile = classify_token(symbol, features, class_cfg)
                    route = route_for_profile(profile, all_strategy_names)
                    _TOKEN_INTEL_CACHE[symbol] = (now_mono + _TOKEN_INTEL_TTL, features, profile, route)
                    logging.getLogger(__name__).info(
                        "TOKEN_SLOW_FEATURES_REFRESHED symbol=%s reason=cache_expiry token_type=%s allowed=%s blocked=%s",
                        symbol, profile.token_type, route.allowed_strategies, route.blocked_strategies,
                    )
                    strategies_for_symbol = filter_and_order_strategies(
                        [s for s in strategies_to_use if s.name in route.allowed_strategies],
                        regime,
                        regime_rs_cfg,
                    )
            else:
                strategies_for_symbol = filter_and_order_strategies(strategies_to_use, regime, regime_rs_cfg)
            for strategy in strategies_for_symbol:
                evaluated += 1
                signal = strategy.evaluate(
                    symbol,
                    quote.price,
                    quote.percent_change_24h,
                    quote.volume_24h,
                    regime,
                    klines_1h=klines_full or None,
                )
                if signal is None:
                    continue
                symbols_with_strategy_signal.add(symbol)
                strategy_evaluate_hit_count += 1
                logging.getLogger(__name__).debug(
                    "SIGNAL_CANDIDATE symbol=%s strategy=%s side=%s entry=%s price_now=%s",
                    symbol, signal.strategy_name, signal.side, signal.entry_price, quote.price,
                )
                if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF":
                    sc = effective_signal_score(signal)
                    be_min = effective_min_signal_score(
                        bot_edge_cfg,
                        selected_mode=bot_edge.selected_mode,
                        strategy_name=getattr(signal, "strategy_name", "") or "",
                        mode_default_min=float(bot_edge.min_signal_score),
                    )
                    if be_min > 0 and sc + 1e-9 < be_min:
                        be_meta = {
                            "effective_signal_score": round(float(sc), 4),
                            "bot_edge_min_required": round(float(be_min), 4),
                            "bot_edge_mode": bot_edge.selected_mode,
                            "bot_edge_system_score": round(float(bot_edge.bot_edge_score), 4),
                            "native_signal": _native_signal_log_slice(signal),
                        }
                        rejected_signals.append({
                            "symbol": symbol,
                            "strategy_name": signal.strategy_name,
                            "reason": (
                                f"Rejected by bot-edge gate: score {sc:.3f} < required {be_min:.2f} ({bot_edge.selected_mode})"
                            ),
                            "reason_code": "BOT_EDGE_MIN_SCORE",
                            "meta": be_meta,
                        })
                        log_decision(
                            "entry_rejected",
                            be_meta,
                            symbol=symbol,
                            strategy_name=signal.strategy_name,
                            reason_code="BOT_EDGE_MIN_SCORE",
                        )
                        continue
                    apply_tp_profile_to_signal(signal, bot_edge.tp_profile, bot_edge_cfg)
                # Bộ lọc xu hướng 4h (tùy chọn): chỉ vào long khi nến 4h tăng, short khi nến 4h giảm. Cache 5 phút.
                if getattr(settings, "use_4h_trend_filter", False):
                    try:
                        k4 = klines_4h_last_n(market_snapshot, symbol, 2)
                        if not k4:
                            k4 = get_klines_4h(symbol, limit=2)
                        if k4:
                            last_4h = k4[-1]
                            bullish_4h = last_4h.close >= last_4h.open
                            if signal.side == "long" and not bullish_4h:
                                continue
                            if signal.side == "short" and bullish_4h:
                                continue
                    except Exception:
                        pass
                ok_edge, edge_reject, entry_combo_mult, eti_cfg_used = _apply_entry_edge_pipeline(
                    signal,
                    symbol=symbol,
                    price_now=quote.price,
                    klines_full=klines_full,
                    combo_mults=combo_mults,
                    combo_cfg=combo_cfg,
                    current_regime=regime,
                    volume_24h=float(getattr(quote, "volume_24h", 0) or 0) or None,
                )
                if not ok_edge:
                    rejected_signals.append(edge_reject)
                    continue
                assign_capital_bucket_to_signal(signal, regime, cs_cfg)
                if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF" and not bot_edge.allow_fast_bucket:
                    signal.capital_bucket = "core"
                corr_rej, corr_msg = correlation_guard_rejects_fast_entry(
                    open_positions, symbol, cs_cfg,
                )
                if corr_rej:
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s",
                        symbol, signal.strategy_name, corr_msg,
                    )
                    rejected_signals.append({
                        "symbol": symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": corr_msg,
                    })
                    continue
                if brain_v4_ctx:
                    try:
                        from core.brain.policy_apply import apply_policy_entry_overlay

                        rej_v4 = apply_policy_entry_overlay(
                            signal,
                            brain_v4_ctx,
                            regime=str(regime),
                            market_state=str(brain_v4_ctx.market_state),
                        )
                        if rej_v4:
                            rejected_signals.append(rej_v4)
                            continue
                    except Exception:
                        pass
                # Số lệnh đang mở cho symbol này
                opens_for_symbol = [p for p in open_positions if p.symbol == symbol]
                count_open_for_symbol = len(opens_for_symbol)
                existing_same_side = [p for p in opens_for_symbol if (p.side or "").lower() == (signal.side or "").lower()]
                is_binance = getattr(self.execution, "__class__", None) and getattr(self.execution.__class__, "__name__", "") == "BinanceFuturesExecutor"
                scale_in_cfg = load_scale_in_config()
                scale_in_enabled = (scale_in_cfg.get("scale_in") or {}).get("enabled", False)
                if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF" and not bot_edge.allow_scale_in:
                    scale_in_enabled = False
                # Smart Scale-In (document/budget): 1 position cung chieu -> danh gia scale-in thay vi skip theo count
                if len(existing_same_side) == 1 and is_binance and scale_in_enabled:
                    position = existing_same_side[0]
                    si_flat = scale_in_cfg.get("scale_in") or {}
                    if not _brain_v4_scale_in_gate_ok(brain_v4_ctx, symbol, quote, klines_full, position, quotes):
                        _log_scale_in_rejected(symbol, signal.side, "brain_v4_policy_gate", position, si_flat)
                        try:
                            from core.rejected_signals_log import log_rejected

                            log_rejected(
                                symbol,
                                (signal.strategy_name or "").strip() or "?",
                                "Scale-in rejected: brain_v4_policy_gate",
                                reason_code="SCALE_IN_REJECTED",
                                meta={"detail": "brain_v4_policy_gate", "side": signal.side},
                            )
                        except Exception:
                            pass
                        skipped_already_open.append(
                            f"{symbol} ({signal.strategy_name}) — scale-in: brain_v4_policy_gate"
                        )
                        continue
                    engine = ScaleInEngine(scale_in_cfg)
                    decision = engine.evaluate(
                        signal, position, quote.price, portfolio, open_positions,
                        close_signal_active=False, reduce_only_pending=False,
                        last_scale_in_at=last_scale_in_at(db, position.id),
                    )
                    if decision.action == ScaleInAction.ADD_TO_POSITION:
                        if hasattr(self.execution, "add_to_position"):
                            trade = self.execution.add_to_position(db, position, decision.add_qty, quote.price, signal)
                            if trade:
                                db.refresh(position)
                                scale_ins_done.append({
                                    "symbol": symbol, "side": signal.side, "strategy_name": signal.strategy_name,
                                    "add_qty": decision.add_qty, "add_notional": decision.add_notional,
                                    "expected_avg_entry": decision.expected_avg_entry, "reason": decision.reason,
                                })
                                logging.getLogger(__name__).info(
                                    "SCALE_IN_DECISION symbol=%s side=%s action=ADD_TO_POSITION add_qty=%s add_notional=%s reason=%s",
                                    symbol, signal.side, decision.add_qty, decision.add_notional, decision.reason,
                                )
                        continue
                    _log_scale_in_rejected(symbol, signal.side, decision.reason, position, si_flat)
                    try:
                        from core.rejected_signals_log import log_rejected

                        log_rejected(
                            symbol,
                            (signal.strategy_name or "").strip() or "?",
                            f"Scale-in rejected: {decision.reason}",
                            reason_code="SCALE_IN_REJECTED",
                            meta={"detail": decision.reason, "side": signal.side},
                        )
                    except Exception:
                        pass
                    skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — scale-in: {decision.reason}")
                    continue
                if count_open_for_symbol >= max_per_symbol:
                    open_strategies = [p.strategy_name or "?" for p in opens_for_symbol]
                    same_type = signal.strategy_name in open_strategies
                    hint = "cung chien luoc" if same_type else "khac chien luoc"
                    skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — da co {count_open_for_symbol} vi the ({', '.join(open_strategies)}) [{hint}]")
                    continue
                # Nếu đã có ít nhất 1 lệnh và cho phép vào thêm (max_per_symbol >= 2): áp dụng quy tắc chuyên gia để tránh đánh trùng
                if count_open_for_symbol >= 1 and max_per_symbol >= 2:
                    open_strategies = [p.strategy_name or "?" for p in opens_for_symbol]
                    # 1) Chỉ vào khi giá còn trong vùng đẹp (theo entry tín hiệu hiện tại)
                    zone = signal.entry_price * entry_zone_pct
                    zone_low = signal.entry_price - zone
                    zone_high = signal.entry_price + zone
                    if not (zone_low <= quote.price <= zone_high):
                        skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — giá ngoài vùng đẹp {zone_low:.4f}–{zone_high:.4f}")
                        continue
                    # 2) Tránh trùng chiến lược: chỉ thêm nếu tín hiệu từ chiến lược KHÁC với lệnh đang mở (tránh hai lệnh cùng một thesis)
                    if getattr(settings, "add_only_different_strategy", True):
                        if signal.strategy_name in open_strategies:
                            skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — da co vi the cung chien luoc, khong them (tranh trung lenh)")
                            continue
                    # 3) Khoảng cách tối thiểu với entry đã mở: không thêm nếu giá quá sát lệnh cũ (tránh hai lệnh cùng mức)
                    min_dist_pct = max(0.0, float(getattr(settings, "min_add_distance_pct", 0) or 0))
                    if min_dist_pct > 0:
                        too_close = any(
                            abs(quote.price - p.entry_price) / max(p.entry_price, 1e-9) < min_dist_pct
                            for p in opens_for_symbol
                        )
                        if too_close:
                            skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — gia qua sat entry vi the co (can >= {min_dist_pct*100:.2f}%), tranh trung muc gia")
                            continue
                planned_r = planned_r_multiple(signal)
                if planned_r is not None and planned_r < min_candidate_r:
                    low_r_payload = {
                        "side": signal.side or "long",
                        "planned_r_multiple": round(float(planned_r), 4),
                        "min_candidate_r_multiple": round(float(min_candidate_r), 4),
                        "native_signal": _native_signal_log_slice(signal),
                    }
                    _cid_lr = _decision_candle_id_1h(klines_full)
                    if _cid_lr:
                        low_r_payload["candle_id"] = _cid_lr
                    log_decision(
                        "entry_rejected",
                        low_r_payload,
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        reason_code="CANDIDATE_LOW_PLANNED_R",
                    )
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": f"Candidate planned R {planned_r:.2f} < min {min_candidate_r:.2f}",
                        "reason_code": "CANDIDATE_LOW_PLANNED_R",
                        "meta": low_r_payload,
                    })
                    continue
                signals_fired.append({
                    "symbol": signal.symbol,
                    "strategy_name": signal.strategy_name,
                    "side": signal.side,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "planned_r_multiple": round(float(planned_r), 4) if planned_r is not None else None,
                    "rationale": signal.rationale,
                    "confidence": signal.confidence,
                    "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                })
                # Phase 1 v6: Volatility guard — block hoặc giảm size khi ATR/volatility cao
                profit_cfg = load_profit_config()
                try:
                    klines_1h = get_klines_1h(symbol, limit=20)
                except Exception:
                    klines_1h = []
                vol_result = check_volatility_guard(symbol, quote, klines_1h, config=profit_cfg)
                if not vol_result.allow_trade:
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s",
                        signal.symbol, signal.strategy_name, vol_result.block_reason,
                    )
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": vol_result.block_reason,
                        "reason_code": "VOLATILITY_GUARD_BLOCK",
                    })
                    continue
                available_cash = portfolio.cash_usd
                if hasattr(self.execution, "get_available_balance_usd"):
                    binance_bal = self.execution.get_available_balance_usd()
                    if binance_bal is not None and binance_bal >= 0:
                        available_cash = binance_bal
                override_risk_pct = None
                if profit_cfg.get("sizing") and "base_risk_pct" in profit_cfg["sizing"]:
                    try:
                        override_risk_pct = float(profit_cfg["sizing"]["base_risk_pct"])
                    except (TypeError, ValueError):
                        pass
                if cs_mgr.enabled and normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                    override_risk_pct = cs_mgr.default_risk_pct_fast()
                if profile and profile.risk_profile and "risk_per_trade_pct" in profile.risk_profile:
                    try:
                        override_risk_pct = float(profile.risk_profile["risk_per_trade_pct"]) / 100.0
                    except (TypeError, ValueError):
                        pass
                if cs_mgr.enabled:
                    if normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                        cap_quick = cs_mgr.fast_capital_usd()
                    else:
                        cap_quick = cs_mgr.core_capital_usd()
                else:
                    cap_quick = risk_capital_usd
                rp_quick = (
                    override_risk_pct
                    if override_risk_pct is not None and 0 < float(override_risk_pct) < 1
                    else float(getattr(settings, "default_risk_pct", 0.01) or 0.01)
                )
                if is_likely_below_min_position_usd(
                    signal,
                    available_cash=available_cash,
                    capital_usd_for_risk=cap_quick,
                    risk_pct=rp_quick,
                    min_usd=float(eff_min_trade_usd),
                ):
                    est_pre = estimate_max_size_usd_from_risk(
                        signal,
                        available_cash=available_cash,
                        capital_usd_for_risk=cap_quick,
                        risk_pct=rp_quick,
                    )
                    pre_cid = _decision_candle_id_1h(klines_1h)
                    pre_payload = {
                        "side": signal.side or "long",
                        "estimate_max_from_risk_usd": round(float(est_pre), 4),
                        "min_notional_usd": float(eff_min_trade_usd),
                        "native_signal": _native_signal_log_slice(signal),
                    }
                    if pre_cid:
                        pre_payload["candle_id"] = pre_cid
                    log_decision(
                        "entry_rejected",
                        pre_payload,
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        reason_code="PRE_SIZING_BELOW_MIN_EXECUTABLE",
                    )
                    rejected_signals.append({
                        "symbol": symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": "Pre-check: estimated position size below minimum notional.",
                        "reason_code": "PRE_SIZING_BELOW_MIN_EXECUTABLE",
                        "meta": pre_payload,
                    })
                    continue
                decision = self._risk_assess_entry(
                    signal,
                    available_cash,
                    daily_realized=daily_realized,
                    daily_realized_r=daily_realized_r,
                    risk_capital_usd=risk_capital_usd,
                    open_positions_total=len(open_positions),
                    open_core=open_core,
                    open_fast=open_fast,
                    daily_core=daily_realized_core,
                    daily_fast=daily_realized_fast,
                    consecutive_loss_core=consecutive_loss_core,
                    consecutive_loss_fast=consecutive_loss_fast,
                    consecutive_loss_all=consecutive_loss_all,
                    override_risk_pct=override_risk_pct,
                    cs_mgr=cs_mgr,
                    bot_edge_max_concurrent=be_max_conc,
                )
                if not decision.approved:
                    _risk_reject_code = classify_risk_reject_reason_code(decision.reason)
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s available_cash=%s open_positions=%s",
                        signal.symbol, signal.strategy_name, decision.reason, available_cash, len(open_positions),
                    )
                    log_decision(
                        "entry_rejected",
                        {
                            "side": signal.side or "long",
                            "strategy": signal.strategy_name,
                            "risk_reject_reason": decision.reason,
                            "native_signal": _native_signal_log_slice(signal),
                        },
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        reason_code=_risk_reject_code,
                    )
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": decision.reason,
                        "reason_code": _risk_reject_code,
                    })
                    continue
                post_risk_engine_usd = float(decision.size_usd)
                # Phase 1 v6: áp dụng giảm size theo volatility guard
                size_after_vol = decision.size_usd
                if vol_result.reduce_size_pct > 0:
                    size_after_vol = round(decision.size_usd * (1.0 - vol_result.reduce_size_pct), 2)
                    if size_after_vol < eff_min_trade_usd:
                        reason = f"Position size too small after volatility reduction ({vol_result.block_reason})"
                        logging.getLogger(__name__).info(
                            "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s size_after_vol=%s",
                            signal.symbol, signal.strategy_name, reason, size_after_vol,
                        )
                        rejected_signals.append({
                            "symbol": signal.symbol,
                            "strategy_name": signal.strategy_name,
                            "reason": reason,
                        })
                        continue
                # Phase 2–3 v6: dynamic sizing — confidence_mult × regime_score × strategy_weight × portfolio_heat_mult
                sizing_cfg = (profit_cfg.get("sizing") or {})
                allocation_result = compute_allocation_mult(
                    open_positions_for_allocation,
                    max_portfolio_heat_r=float(alloc_cfg.get("max_portfolio_heat_r", 4.0)),
                    same_regime_reduce=float(alloc_cfg.get("same_regime_reduce", 0.8)),
                    same_strategy_reduce=float(alloc_cfg.get("same_strategy_reduce", 0.75)),
                    current_regime=regime,
                    current_strategy=signal.strategy_name,
                )
                strategy_weight = get_strategy_weight(strategy_weights, signal.strategy_name)
                conf_mult = get_confidence_multiplier(signal.confidence, profit_cfg)
                reg_score = get_regime_score(regime, profit_cfg, strategy_name=signal.strategy_name)
                if sizing_cfg.get("enabled", True):
                    after_dynamic_usd = apply_dynamic_sizing(
                        size_after_vol,
                        signal.confidence,
                        regime,
                        profit_cfg,
                        strategy_weight=strategy_weight,
                        portfolio_heat_mult=allocation_result.portfolio_heat_mult,
                        strategy_name=signal.strategy_name,
                    )
                else:
                    after_dynamic_usd = float(size_after_vol)
                final_size_usd = float(after_dynamic_usd)
                if entry_combo_mult < 1.0:
                    final_size_usd = round(float(final_size_usd) * float(entry_combo_mult), 2)
                after_combo_usd = float(final_size_usd)
                pre_modifier_usd = float(final_size_usd)
                mod_breakdown: dict[str, Any] = {}
                if brain_v4_ctx:
                    try:
                        from core.brain.policy_apply import apply_policy_size_breakdown

                        final_size_usd, mod_breakdown = apply_policy_size_breakdown(
                            pre_modifier_usd, brain_v4_ctx, symbol=symbol
                        )
                    except Exception:
                        mod_breakdown = {}
                post_policy_usd = float(final_size_usd)
                be_mult = (
                    float(bot_edge.risk_multiplier)
                    if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF"
                    else 1.0
                )
                if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF":
                    final_size_usd = round(float(final_size_usd) * float(bot_edge.risk_multiplier), 2)
                final_size_usd, _under_risk_meta = apply_risk_ceiling_and_under_risk_floor(
                    final_size_usd=float(final_size_usd),
                    post_risk_engine_usd=float(post_risk_engine_usd),
                    eff_min_trade_usd=float(eff_min_trade_usd),
                    available_cash=float(available_cash),
                    sizing_cfg=sizing_cfg,
                )
                post_modifier_usd = float(final_size_usd)
                if _under_risk_meta.get("under_risk_rescale"):
                    logging.getLogger(__name__).info(
                        "UNDER_RISK_RESCALE symbol=%s strategy=%s %s",
                        symbol,
                        signal.strategy_name,
                        _under_risk_meta["under_risk_rescale"],
                    )
                if final_size_usd < eff_min_trade_usd:
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=post_sizing_below_min final_size_usd=%s min=%s",
                        signal.symbol, signal.strategy_name, round(final_size_usd, 2), eff_min_trade_usd,
                    )
                    sizing_trace = _build_sizing_trace_payload(
                        post_risk_engine_usd=post_risk_engine_usd,
                        size_after_vol=float(size_after_vol),
                        after_dynamic_usd=float(after_dynamic_usd),
                        after_combo_usd=after_combo_usd,
                        pre_modifier_usd=pre_modifier_usd,
                        post_policy_usd=post_policy_usd,
                        post_modifier_usd=post_modifier_usd,
                        signal=signal,
                        regime=regime,
                        profit_cfg=profit_cfg,
                        strategy_weight=strategy_weight,
                        portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                        entry_combo_mult=float(entry_combo_mult),
                        confidence_mult=conf_mult,
                        regime_score=reg_score,
                        mod_breakdown=mod_breakdown,
                        bot_edge_mult=be_mult,
                        available_cash=float(available_cash),
                        cap_quick=float(cap_quick),
                        rp_quick=float(rp_quick),
                        min_notional_usd=float(eff_min_trade_usd),
                    )
                    sizing_trace["reject_diagnosis"] = diagnose_size_too_small(
                        post_risk_engine_usd=float(post_risk_engine_usd),
                        size_after_vol=float(size_after_vol),
                        after_dynamic_usd=float(after_dynamic_usd),
                        pre_policy_usd=float(pre_modifier_usd),
                        post_policy_usd=float(post_policy_usd),
                        post_modifier_usd=float(post_modifier_usd),
                        estimate_max_from_risk_usd=float(sizing_trace["estimate_max_from_risk_usd"]),
                        min_notional_usd=float(sizing_trace["min_notional_usd"]),
                        signal=signal,
                        entry_combo_mult=float(entry_combo_mult),
                        confidence_mult=float(conf_mult),
                        regime_score=float(reg_score),
                        strategy_weight=float(strategy_weight),
                        portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                        bot_edge_mult=float(be_mult),
                        mod_breakdown=mod_breakdown,
                    )
                    _lot = try_exchange_lot_for_executor(self.execution, symbol)
                    _ex_prev = (
                        exchange_qty_preview(
                            post_notional_usd=float(post_modifier_usd),
                            entry_price=float(signal.entry_price or 0),
                            lot=_lot,
                        )
                        if _lot
                        else None
                    )
                    sizing_trace["sizing_stage_diagnostics"] = build_sizing_stage_diagnostics(
                        post_risk_engine_usd=float(post_risk_engine_usd),
                        size_after_vol=float(size_after_vol),
                        after_dynamic_usd=float(after_dynamic_usd),
                        after_combo_usd=float(after_combo_usd),
                        pre_modifier_usd=float(pre_modifier_usd),
                        post_policy_usd=float(post_policy_usd),
                        post_modifier_usd=float(post_modifier_usd),
                        confidence_mult=float(conf_mult),
                        regime_score=float(reg_score),
                        strategy_weight=float(strategy_weight),
                        portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                    )
                    _s_code, _s_detail = classify_post_sizing_reject(
                        post_modifier_usd=float(post_modifier_usd),
                        pre_modifier_usd=float(pre_modifier_usd),
                        post_policy_usd=float(post_policy_usd),
                        internal_min_trade_usd=float(eff_min_trade_usd),
                        mod_breakdown=mod_breakdown,
                        exchange_preview=_ex_prev,
                    )
                    sizing_trace["exchange_qty_preview"] = _ex_prev
                    sizing_trace["sizing_reject_classification"] = {"reason_code": _s_code, **_s_detail}
                    rej_payload = {
                        "side": signal.side or "long",
                        "strategy": signal.strategy_name,
                        "sizing_trace": sizing_trace,
                        "native_signal": _native_signal_log_slice(signal),
                        "sizing_reject_reason_code": _s_code,
                    }
                    rej_payload.update(
                        _build_risk_efficiency_fields(
                            final_size_usd=float(post_modifier_usd),
                            risk_ceiling_usd=float(post_risk_engine_usd),
                            effective_min_trade_usd=float(eff_min_trade_usd),
                            sizing_cfg=sizing_cfg,
                            under_risk_meta=_under_risk_meta,
                        )
                    )
                    _sz_cid = _decision_candle_id_1h(klines_1h)
                    if _sz_cid:
                        rej_payload["candle_id"] = _sz_cid
                    _attach_policy_squeeze_reject_audit(
                        rej_payload,
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        reason_code=_s_code,
                        post_modifier_usd=float(post_modifier_usd),
                        eff_min_trade_usd=float(eff_min_trade_usd),
                        profit_cfg=profit_cfg,
                        single_strategy_mode=single_strategy or "",
                        blocking_stage=_s_detail.get("blocking_stage"),
                        sizing_stage_diagnostics=sizing_trace["sizing_stage_diagnostics"],
                        mod_breakdown=mod_breakdown,
                        pre_modifier_usd=float(pre_modifier_usd),
                        post_policy_usd=float(post_policy_usd),
                        bot_edge_mult=float(be_mult),
                    )
                    _rej_msg = (
                        f"Sizing reject ({_s_code}): final {float(post_modifier_usd):.2f} USD vs required min "
                        f"{float(eff_min_trade_usd):.2f} USD — stage={_s_detail.get('blocking_stage', '')}"
                    )
                    log_decision(
                        "entry_rejected",
                        rej_payload,
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        reason_code=_s_code,
                    )
                    _rej_meta = {
                        "combo_mult": entry_combo_mult,
                        "sizing_trace": sizing_trace,
                        "required_min_usd": float(eff_min_trade_usd),
                        "blocking_stage": _s_detail.get("blocking_stage"),
                    }
                    _rej_meta.update(
                        _build_risk_efficiency_fields(
                            final_size_usd=float(post_modifier_usd),
                            risk_ceiling_usd=float(post_risk_engine_usd),
                            effective_min_trade_usd=float(eff_min_trade_usd),
                            sizing_cfg=sizing_cfg,
                            under_risk_meta=_under_risk_meta,
                        )
                    )
                    if _s_code == "REDUCED_TOO_MUCH_BY_POLICY" and rej_payload.get("policy_squeeze_detail"):
                        _rej_meta["policy_squeeze_detail"] = rej_payload["policy_squeeze_detail"]
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": _rej_msg,
                        "reason_code": _s_code,
                        "meta": _rej_meta,
                    })
                    continue
                final_executable_usd = min(final_size_usd, available_cash)
                final_size_usd = final_executable_usd
                logging.getLogger(__name__).info(
                    "OPENING_POSITION symbol=%s strategy=%s side=%s size_usd=%s combo_mult=%s",
                    signal.symbol, signal.strategy_name, signal.side, round(final_size_usd, 2), entry_combo_mult,
                )
                position = self.execution.open_position(
                    db, portfolio.id, signal, final_size_usd
                )
                if not hasattr(self.execution, "get_available_balance_usd"):
                    portfolio.cash_usd -= final_size_usd
                open_trade = db.scalar(
                    select(Trade).where(
                        Trade.position_id == position.id,
                        Trade.action == "open",
                    )
                )
                if open_trade and brain_v4_ctx and brain_v4_ctx.brain_cycle_id:
                    open_trade.brain_cycle_id = brain_v4_ctx.brain_cycle_id
                    _tid = brain_v4_ctx.symbol_decision_trace_ids.get(symbol)
                    if _tid:
                        open_trade.decision_trace_id = _tid
                try:
                    from core.brain.persistence import insert_brain_sizing_event, p1_persistence_enabled

                    if (
                        brain_v4_ctx
                        and brain_v4_ctx.brain_cycle_id
                        and p1_persistence_enabled()
                    ):
                        insert_brain_sizing_event(
                            db,
                            cycle_id=brain_v4_ctx.brain_cycle_id,
                            decision_trace_id=brain_v4_ctx.symbol_decision_trace_ids.get(symbol),
                            market_decision_trace_id=brain_v4_ctx.market_decision_trace_id or None,
                            symbol=symbol,
                            strategy_name=signal.strategy_name or "",
                            side=signal.side or "",
                            post_risk_engine_usd=post_risk_engine_usd,
                            pre_modifier_usd=pre_modifier_usd,
                            post_modifier_usd=post_modifier_usd,
                            final_executable_usd=final_executable_usd,
                            available_cash_usd=float(available_cash),
                            modifier_breakdown=mod_breakdown,
                            config_hash=brain_v4_ctx.config_hash_v4 or "",
                        )
                except Exception:
                    pass
                # v4: full entry context for "biết vì sao vừa vào lệnh"
                stop_distance = abs(signal.entry_price - signal.stop_loss) / max(signal.entry_price, 1e-9)
                risk_score = min(1.0, stop_distance * 15) if stop_distance > 0 else None  # proxy 0-1
                entry_ctx = build_entry_context(
                    signal, decision.reason, quote,
                    risk_score=risk_score,
                    timeframe=getattr(settings, "default_timeframe", "5m") or "5m",
                )
                self.journal.create_entry(
                    db, signal, decision.reason,
                    setup_score=signal.confidence * 100,
                    trade_id=open_trade.id if open_trade else None,
                    side=signal.side,
                    reasons=entry_ctx.get("reasons"),
                    market_context=entry_ctx.get("market_context"),
                    risk_score=entry_ctx.get("risk_score"),
                    timeframe=entry_ctx.get("timeframe"),
                    token_type=profile.token_type if profile else None,
                    liquidity_tier=profile.liquidity_tier if profile else None,
                    volatility_tier=profile.volatility_tier if profile else None,
                    manipulation_risk=profile.manipulation_risk if profile else None,
                    was_strategy_allowed=True if route else None,
                    short_allowed_flag=(profile.shortability != "disabled") if profile else None,
                    hedge_allowed_flag=(profile.hedge_policy != "disabled") if profile else None,
                    capital_bucket=normalize_bucket(getattr(signal, "capital_bucket", None)),
                )
                try:
                    record_entry_opened(symbol, eti_cfg_used)
                except Exception:
                    pass
                _opened_payload = {
                    "size_usd": round(final_size_usd, 2),
                    "combo_mult": entry_combo_mult,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                }
                if _under_risk_meta.get("under_risk_rescale"):
                    _opened_payload["under_risk_rescale"] = _under_risk_meta["under_risk_rescale"]
                _opened_payload.update(
                    _build_risk_efficiency_fields(
                        final_size_usd=float(final_size_usd),
                        risk_ceiling_usd=float(post_risk_engine_usd),
                        effective_min_trade_usd=float(eff_min_trade_usd),
                        sizing_cfg=sizing_cfg,
                        under_risk_meta=_under_risk_meta,
                    )
                )
                log_decision(
                    "entry_opened",
                    _opened_payload,
                    symbol=symbol,
                    strategy_name=signal.strategy_name,
                    reason_code="ENTRY_OPENED",
                )
                opened += 1
                opened_positions.append({
                    "symbol": signal.symbol,
                    "strategy": signal.strategy_name,
                    "side": signal.side,
                    "entry": position.entry_price,
                    "size_usd": round(final_size_usd, 2),
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                    "risk_ceiling_usd": round(float(post_risk_engine_usd), 4),
                    "risk_efficiency_ratio": (
                        round(float(final_size_usd) / float(post_risk_engine_usd), 6)
                        if float(post_risk_engine_usd) > 0
                        else None
                    ),
                })
                if normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                    open_fast += 1
                else:
                    open_core += 1
                open_positions.append(position)
                open_positions_for_allocation.append({"strategy_name": signal.strategy_name})
                opened_this_symbol = True
                break
            # Smart Short Engine (v6): one short candidate per symbol when no long opened; respect profile.shortability
            if not opened_this_symbol:
                if profile and profile.shortability == "disabled":
                    logging.getLogger(__name__).info("STRATEGY_BLOCKED_BY_PROFILE symbol=%s short=disabled (token_type=%s)", symbol, profile.token_type)
                else:
                    short_cfg = load_short_config()
                    if profile and profile.short_min_score_override is not None:
                        short_cfg = {**short_cfg, "min_score": profile.short_min_score_override}
                    if short_cfg.get("enabled", True):
                        try:
                            klines_1h_short = get_klines_1h(symbol, limit=20)
                        except Exception:
                            klines_1h_short = []
                        if len(klines_1h_short) >= 6:
                            htf_downtrend = False
                            try:
                                k4 = get_klines_4h(symbol, limit=3)
                                if k4:
                                    htf_downtrend = k4[-1].close < k4[-1].open
                            except Exception:
                                pass
                            short_sig = evaluate_short(
                                symbol, quote.price, klines_1h_short, htf_downtrend, regime, short_cfg
                            )
                            if short_sig and isinstance(short_sig, ShortSignal):
                                signal = StrategySignal(
                                    symbol=short_sig.symbol,
                                    strategy_name="short_" + short_sig.setup_type,
                                    side="short",
                                    confidence=short_sig.confidence_score,
                                    entry_price=short_sig.entry_price,
                                    stop_loss=short_sig.stop_loss,
                                    take_profit=short_sig.take_profit,
                                    rationale="; ".join(short_sig.reasons),
                                    regime=short_sig.regime,
                                )
                                if getattr(settings, "use_4h_trend_filter", False):
                                    try:
                                        k4 = get_klines_4h(symbol, limit=2)
                                        if k4 and k4[-1].close >= k4[-1].open:
                                            pass
                                        else:
                                            signal = None
                                    except Exception:
                                        pass
                                if signal is not None:
                                    if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF":
                                        sc_s = effective_signal_score(signal)
                                        be_min_s = effective_min_signal_score(
                                            bot_edge_cfg,
                                            selected_mode=bot_edge.selected_mode,
                                            strategy_name=getattr(signal, "strategy_name", "") or "",
                                            mode_default_min=float(bot_edge.min_signal_score),
                                        )
                                        if be_min_s > 0 and sc_s + 1e-9 < be_min_s:
                                            be_meta_s = {
                                                "effective_signal_score": round(float(sc_s), 4),
                                                "bot_edge_min_required": round(float(be_min_s), 4),
                                                "bot_edge_mode": bot_edge.selected_mode,
                                                "bot_edge_system_score": round(float(bot_edge.bot_edge_score), 4),
                                                "native_signal": _native_signal_log_slice(signal),
                                            }
                                            rejected_signals.append({
                                                "symbol": symbol,
                                                "strategy_name": signal.strategy_name,
                                                "reason": (
                                                    f"Rejected by bot-edge gate: score {sc_s:.3f} < required {be_min_s:.2f} ({bot_edge.selected_mode})"
                                                ),
                                                "reason_code": "BOT_EDGE_MIN_SCORE",
                                                "meta": be_meta_s,
                                            })
                                            log_decision(
                                                "entry_rejected",
                                                be_meta_s,
                                                symbol=symbol,
                                                strategy_name=signal.strategy_name,
                                                reason_code="BOT_EDGE_MIN_SCORE",
                                            )
                                            continue
                                        apply_tp_profile_to_signal(signal, bot_edge.tp_profile, bot_edge_cfg)
                                    ok_edge_s, edge_reject_s, entry_combo_mult_s, eti_cfg_short = _apply_entry_edge_pipeline(
                                        signal,
                                        symbol=symbol,
                                        price_now=quote.price,
                                        klines_full=klines_1h_short,
                                        combo_mults=combo_mults,
                                        combo_cfg=combo_cfg,
                                        current_regime=regime,
                                        volume_24h=float(getattr(quote, "volume_24h", 0) or 0) or None,
                                    )
                                    if not ok_edge_s:
                                        rejected_signals.append(edge_reject_s)
                                        continue
                                    assign_capital_bucket_to_signal(signal, regime, cs_cfg)
                                    if (
                                        bot_edge_cfg.get("enabled", True)
                                        and bot_edge.selected_mode != "OFF"
                                        and not bot_edge.allow_fast_bucket
                                    ):
                                        signal.capital_bucket = "core"
                                    corr_rej_s, corr_msg_s = correlation_guard_rejects_fast_entry(
                                        open_positions, symbol, cs_cfg,
                                    )
                                    if corr_rej_s:
                                        logging.getLogger(__name__).info(
                                            "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s",
                                            symbol, signal.strategy_name, corr_msg_s,
                                        )
                                        rejected_signals.append({
                                            "symbol": symbol,
                                            "strategy_name": signal.strategy_name,
                                            "reason": corr_msg_s,
                                        })
                                        continue
                                    if brain_v4_ctx:
                                        try:
                                            from core.brain.policy_apply import apply_policy_entry_overlay

                                            rej_vs = apply_policy_entry_overlay(
                                                signal,
                                                brain_v4_ctx,
                                                regime=str(regime),
                                                market_state=str(brain_v4_ctx.market_state),
                                            )
                                            if rej_vs:
                                                rejected_signals.append(rej_vs)
                                                continue
                                        except Exception:
                                            pass
                                    opens_for_symbol = [p for p in open_positions if p.symbol == symbol]
                                    existing_same_side_short = [p for p in opens_for_symbol if (p.side or "").lower() == "short"]
                                    scale_in_enabled_short = (load_scale_in_config().get("scale_in") or {}).get("enabled", False)
                                    if (
                                        bot_edge_cfg.get("enabled", True)
                                        and bot_edge.selected_mode != "OFF"
                                        and not bot_edge.allow_scale_in
                                    ):
                                        scale_in_enabled_short = False
                                    if len(existing_same_side_short) == 1 and is_binance and scale_in_enabled_short:
                                        position = existing_same_side_short[0]
                                        si_flat_short = scale_in_cfg.get("scale_in") or {}
                                        if not _brain_v4_scale_in_gate_ok(
                                            brain_v4_ctx, symbol, quote, klines_1h_short, position, quotes
                                        ):
                                            _log_scale_in_rejected(
                                                symbol, "short", "brain_v4_policy_gate", position, si_flat_short
                                            )
                                            try:
                                                from core.rejected_signals_log import log_rejected

                                                log_rejected(
                                                    symbol,
                                                    (signal.strategy_name or "").strip() or "?",
                                                    "Scale-in rejected (short): brain_v4_policy_gate",
                                                    reason_code="SCALE_IN_REJECTED",
                                                    meta={"detail": "brain_v4_policy_gate", "side": "short"},
                                                )
                                            except Exception:
                                                pass
                                            skipped_already_open.append(
                                                f"{symbol} ({signal.strategy_name}) — scale-in: brain_v4_policy_gate"
                                            )
                                            continue
                                        engine_short = ScaleInEngine(scale_in_cfg)
                                        decision_short = engine_short.evaluate(
                                            signal, position, quote.price, portfolio, open_positions,
                                            close_signal_active=False, reduce_only_pending=False,
                                            last_scale_in_at=last_scale_in_at(db, position.id),
                                        )
                                        if decision_short.action == ScaleInAction.ADD_TO_POSITION:
                                            if hasattr(self.execution, "add_to_position"):
                                                trade_short = self.execution.add_to_position(db, position, decision_short.add_qty, quote.price, signal)
                                                if trade_short:
                                                    db.refresh(position)
                                                    scale_ins_done.append({
                                                        "symbol": symbol, "side": signal.side, "strategy_name": signal.strategy_name,
                                                        "add_qty": decision_short.add_qty, "add_notional": decision_short.add_notional,
                                                        "expected_avg_entry": decision_short.expected_avg_entry, "reason": decision_short.reason,
                                                    })
                                                    logging.getLogger(__name__).info(
                                                        "SCALE_IN_DECISION symbol=%s side=short action=ADD_TO_POSITION add_qty=%s add_notional=%s reason=%s",
                                                        symbol, decision_short.add_qty, decision_short.add_notional, decision_short.reason,
                                                    )
                                            continue
                                        _log_scale_in_rejected(symbol, "short", decision_short.reason, position, si_flat_short)
                                        try:
                                            from core.rejected_signals_log import log_rejected

                                            log_rejected(
                                                symbol,
                                                (signal.strategy_name or "").strip() or "?",
                                                f"Scale-in rejected (short): {decision_short.reason}",
                                                reason_code="SCALE_IN_REJECTED",
                                                meta={"detail": decision_short.reason, "side": "short"},
                                            )
                                        except Exception:
                                            pass
                                        skipped_already_open.append(f"{symbol} ({signal.strategy_name}) — scale-in: {decision_short.reason}")
                                        continue
                                    if len(opens_for_symbol) < max_per_symbol:
                                        planned_r_s = planned_r_multiple(signal)
                                        if planned_r_s is not None and planned_r_s < min_candidate_r:
                                            low_r_payload_s = {
                                                "side": signal.side or "short",
                                                "planned_r_multiple": round(float(planned_r_s), 4),
                                                "min_candidate_r_multiple": round(float(min_candidate_r), 4),
                                                "native_signal": _native_signal_log_slice(signal),
                                            }
                                            _cid_lrs = _decision_candle_id_1h(klines_1h_short)
                                            if _cid_lrs:
                                                low_r_payload_s["candle_id"] = _cid_lrs
                                            log_decision(
                                                "entry_rejected",
                                                low_r_payload_s,
                                                symbol=symbol,
                                                strategy_name=signal.strategy_name,
                                                reason_code="CANDIDATE_LOW_PLANNED_R",
                                            )
                                            rejected_signals.append({
                                                "symbol": signal.symbol,
                                                "strategy_name": signal.strategy_name,
                                                "reason": f"Candidate planned R {planned_r_s:.2f} < min {min_candidate_r:.2f}",
                                                "reason_code": "CANDIDATE_LOW_PLANNED_R",
                                                "meta": low_r_payload_s,
                                            })
                                            continue
                                        signals_fired.append({
                                            "symbol": signal.symbol,
                                            "strategy_name": signal.strategy_name,
                                            "side": signal.side,
                                            "entry_price": signal.entry_price,
                                            "stop_loss": signal.stop_loss,
                                            "take_profit": signal.take_profit,
                                            "planned_r_multiple": round(float(planned_r_s), 4) if planned_r_s is not None else None,
                                            "rationale": signal.rationale,
                                            "confidence": signal.confidence,
                                            "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                                        })
                                        profit_cfg = load_profit_config()
                                        vol_result = check_volatility_guard(symbol, quote, klines_1h_short, config=profit_cfg)
                                        if vol_result.allow_trade:
                                            available_cash = portfolio.cash_usd
                                            if hasattr(self.execution, "get_available_balance_usd"):
                                                binance_bal = self.execution.get_available_balance_usd()
                                                if binance_bal is not None and binance_bal >= 0:
                                                    available_cash = binance_bal
                                            override_risk_pct = None
                                            if profit_cfg.get("sizing") and "base_risk_pct" in profit_cfg["sizing"]:
                                                try:
                                                    override_risk_pct = float(profit_cfg["sizing"]["base_risk_pct"])
                                                except (TypeError, ValueError):
                                                    pass
                                            if cs_mgr.enabled and normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                                                override_risk_pct = cs_mgr.default_risk_pct_fast()
                                            if profile and profile.risk_profile and "risk_per_trade_pct" in profile.risk_profile:
                                                try:
                                                    override_risk_pct = float(profile.risk_profile["risk_per_trade_pct"]) / 100.0
                                                except (TypeError, ValueError):
                                                    pass
                                            if cs_mgr.enabled:
                                                if normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                                                    cap_quick_s = cs_mgr.fast_capital_usd()
                                                else:
                                                    cap_quick_s = cs_mgr.core_capital_usd()
                                            else:
                                                cap_quick_s = risk_capital_usd
                                            rp_quick_s = (
                                                override_risk_pct
                                                if override_risk_pct is not None and 0 < float(override_risk_pct) < 1
                                                else float(getattr(settings, "default_risk_pct", 0.01) or 0.01)
                                            )
                                            if is_likely_below_min_position_usd(
                                                signal,
                                                available_cash=available_cash,
                                                capital_usd_for_risk=cap_quick_s,
                                                risk_pct=rp_quick_s,
                                                min_usd=float(eff_min_trade_usd),
                                            ):
                                                logging.getLogger(__name__).debug(
                                                    "REJECTED_SIGNAL symbol=%s strategy=%s reason=pre_size_below_min (short)",
                                                    symbol, signal.strategy_name,
                                                )
                                                est_pre_s = estimate_max_size_usd_from_risk(
                                                    signal,
                                                    available_cash=available_cash,
                                                    capital_usd_for_risk=cap_quick_s,
                                                    risk_pct=rp_quick_s,
                                                )
                                                pre_cid_s = _decision_candle_id_1h(klines_1h_short)
                                                pre_payload_s = {
                                                    "side": signal.side or "short",
                                                    "estimate_max_from_risk_usd": round(float(est_pre_s), 4),
                                                    "min_notional_usd": float(eff_min_trade_usd),
                                                    "blocked_by_exchange_min": True,
                                                    "native_signal": _native_signal_log_slice(signal),
                                                }
                                                if pre_cid_s:
                                                    pre_payload_s["candle_id"] = pre_cid_s
                                                log_decision(
                                                    "entry_rejected",
                                                    pre_payload_s,
                                                    symbol=symbol,
                                                    strategy_name=signal.strategy_name,
                                                    reason_code="PRE_SIZING_BELOW_MIN_EXECUTABLE",
                                                )
                                                rejected_signals.append({
                                                    "symbol": symbol,
                                                    "strategy_name": signal.strategy_name,
                                                    "reason": "Pre-check: estimated position size below minimum.",
                                                    "reason_code": "PRE_SIZING_BELOW_MIN_EXECUTABLE",
                                                    "meta": pre_payload_s,
                                                })
                                                continue
                                            decision = self._risk_assess_entry(
                                                signal,
                                                available_cash,
                                                daily_realized=daily_realized,
                                                daily_realized_r=daily_realized_r,
                                                risk_capital_usd=risk_capital_usd,
                                                open_positions_total=len(open_positions),
                                                open_core=open_core,
                                                open_fast=open_fast,
                                                daily_core=daily_realized_core,
                                                daily_fast=daily_realized_fast,
                                                consecutive_loss_core=consecutive_loss_core,
                                                consecutive_loss_fast=consecutive_loss_fast,
                                                consecutive_loss_all=consecutive_loss_all,
                                                override_risk_pct=override_risk_pct,
                                                cs_mgr=cs_mgr,
                                                bot_edge_max_concurrent=be_max_conc,
                                            )
                                            if decision.approved:
                                                post_risk_engine_usd = float(decision.size_usd)
                                                size_after_vol = decision.size_usd
                                                if vol_result.reduce_size_pct > 0:
                                                    size_after_vol = round(decision.size_usd * (1.0 - vol_result.reduce_size_pct), 2)
                                                if size_after_vol >= eff_min_trade_usd:
                                                    allocation_result = compute_allocation_mult(
                                                        open_positions_for_allocation,
                                                        max_portfolio_heat_r=float(alloc_cfg.get("max_portfolio_heat_r", 4.0)),
                                                        same_regime_reduce=float(alloc_cfg.get("same_regime_reduce", 0.8)),
                                                        same_strategy_reduce=float(alloc_cfg.get("same_strategy_reduce", 0.75)),
                                                        current_regime=regime,
                                                        current_strategy=signal.strategy_name,
                                                    )
                                                    strategy_weight = get_strategy_weight(strategy_weights, signal.strategy_name)
                                                    conf_mult_s = get_confidence_multiplier(signal.confidence, profit_cfg)
                                                    reg_score_s = get_regime_score(regime, profit_cfg, strategy_name=signal.strategy_name)
                                                    sizing_cfg = (profit_cfg.get("sizing") or {})
                                                    if sizing_cfg.get("enabled", True):
                                                        after_dynamic_usd_s = apply_dynamic_sizing(
                                                            size_after_vol, signal.confidence, regime, profit_cfg,
                                                            strategy_weight=strategy_weight,
                                                            portfolio_heat_mult=allocation_result.portfolio_heat_mult,
                                                            strategy_name=signal.strategy_name,
                                                        )
                                                    else:
                                                        after_dynamic_usd_s = float(size_after_vol)
                                                    final_size_usd = float(after_dynamic_usd_s)
                                                    if entry_combo_mult_s < 1.0:
                                                        final_size_usd = round(float(final_size_usd) * float(entry_combo_mult_s), 2)
                                                    after_combo_usd_s = float(final_size_usd)
                                                    pre_modifier_usd = float(final_size_usd)
                                                    mod_breakdown = {}
                                                    if brain_v4_ctx:
                                                        try:
                                                            from core.brain.policy_apply import apply_policy_size_breakdown

                                                            final_size_usd, mod_breakdown = apply_policy_size_breakdown(
                                                                pre_modifier_usd, brain_v4_ctx, symbol=symbol
                                                            )
                                                        except Exception:
                                                            mod_breakdown = {}
                                                    post_policy_usd_s = float(final_size_usd)
                                                    be_mult_s = (
                                                        float(bot_edge.risk_multiplier)
                                                        if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF"
                                                        else 1.0
                                                    )
                                                    if bot_edge_cfg.get("enabled", True) and bot_edge.selected_mode != "OFF":
                                                        final_size_usd = round(
                                                            float(final_size_usd) * float(bot_edge.risk_multiplier), 2
                                                        )
                                                    final_size_usd, _under_risk_meta_s = apply_risk_ceiling_and_under_risk_floor(
                                                        final_size_usd=float(final_size_usd),
                                                        post_risk_engine_usd=float(post_risk_engine_usd),
                                                        eff_min_trade_usd=float(eff_min_trade_usd),
                                                        available_cash=float(available_cash),
                                                        sizing_cfg=sizing_cfg,
                                                    )
                                                    post_modifier_usd = float(final_size_usd)
                                                    if _under_risk_meta_s.get("under_risk_rescale"):
                                                        logging.getLogger(__name__).info(
                                                            "UNDER_RISK_RESCALE symbol=%s strategy=%s %s (short)",
                                                            symbol,
                                                            signal.strategy_name,
                                                            _under_risk_meta_s["under_risk_rescale"],
                                                        )
                                                    if final_size_usd >= eff_min_trade_usd:
                                                        final_executable_usd = min(final_size_usd, available_cash)
                                                        final_size_usd = final_executable_usd
                                                        position = self.execution.open_position(db, portfolio.id, signal, final_size_usd)
                                                        if not hasattr(self.execution, "get_available_balance_usd"):
                                                            portfolio.cash_usd -= final_size_usd
                                                        open_trade = db.scalar(
                                                            select(Trade).where(
                                                                Trade.position_id == position.id,
                                                                Trade.action == "open",
                                                            )
                                                        )
                                                        if open_trade and brain_v4_ctx and brain_v4_ctx.brain_cycle_id:
                                                            open_trade.brain_cycle_id = brain_v4_ctx.brain_cycle_id
                                                            _tid_s = brain_v4_ctx.symbol_decision_trace_ids.get(symbol)
                                                            if _tid_s:
                                                                open_trade.decision_trace_id = _tid_s
                                                        try:
                                                            from core.brain.persistence import (
                                                                insert_brain_sizing_event,
                                                                p1_persistence_enabled,
                                                            )

                                                            if (
                                                                brain_v4_ctx
                                                                and brain_v4_ctx.brain_cycle_id
                                                                and p1_persistence_enabled()
                                                            ):
                                                                insert_brain_sizing_event(
                                                                    db,
                                                                    cycle_id=brain_v4_ctx.brain_cycle_id,
                                                                    decision_trace_id=brain_v4_ctx.symbol_decision_trace_ids.get(symbol),
                                                                    market_decision_trace_id=brain_v4_ctx.market_decision_trace_id or None,
                                                                    symbol=symbol,
                                                                    strategy_name=signal.strategy_name or "",
                                                                    side=signal.side or "",
                                                                    post_risk_engine_usd=post_risk_engine_usd,
                                                                    pre_modifier_usd=pre_modifier_usd,
                                                                    post_modifier_usd=post_modifier_usd,
                                                                    final_executable_usd=final_executable_usd,
                                                                    available_cash_usd=float(available_cash),
                                                                    modifier_breakdown=mod_breakdown,
                                                                    config_hash=brain_v4_ctx.config_hash_v4 or "",
                                                                )
                                                        except Exception:
                                                            pass
                                                        stop_distance = abs(signal.entry_price - signal.stop_loss) / max(signal.entry_price, 1e-9)
                                                        risk_score = min(1.0, stop_distance * 15) if stop_distance > 0 else None
                                                        entry_ctx = build_entry_context(
                                                            signal, decision.reason, quote,
                                                            risk_score=risk_score,
                                                            timeframe=getattr(settings, "default_timeframe", "5m") or "5m",
                                                        )
                                                        self.journal.create_entry(
                                                            db, signal, decision.reason,
                                                            setup_score=signal.confidence * 100,
                                                            trade_id=open_trade.id if open_trade else None,
                                                            side=signal.side,
                                                            reasons=entry_ctx.get("reasons"),
                                                            market_context=entry_ctx.get("market_context"),
                                                            risk_score=entry_ctx.get("risk_score"),
                                                            timeframe=entry_ctx.get("timeframe"),
                                                            setup_type=short_sig.setup_type,
                                                            token_type=profile.token_type if profile else None,
                                                            liquidity_tier=profile.liquidity_tier if profile else None,
                                                            volatility_tier=profile.volatility_tier if profile else None,
                                                            manipulation_risk=profile.manipulation_risk if profile else None,
                                                            was_strategy_allowed=True if profile else None,
                                                            short_allowed_flag=(profile.shortability != "disabled") if profile else None,
                                                            hedge_allowed_flag=(profile.hedge_policy != "disabled") if profile else None,
                                                            capital_bucket=normalize_bucket(getattr(signal, "capital_bucket", None)),
                                                        )
                                                        try:
                                                            record_entry_opened(symbol, eti_cfg_short)
                                                        except Exception:
                                                            pass
                                                        _opened_short = {
                                                            "size_usd": round(final_size_usd, 2),
                                                            "combo_mult": entry_combo_mult_s,
                                                            "setup": short_sig.setup_type,
                                                            "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                                                        }
                                                        if _under_risk_meta_s.get("under_risk_rescale"):
                                                            _opened_short["under_risk_rescale"] = _under_risk_meta_s[
                                                                "under_risk_rescale"
                                                            ]
                                                        _opened_short.update(
                                                            _build_risk_efficiency_fields(
                                                                final_size_usd=float(final_size_usd),
                                                                risk_ceiling_usd=float(post_risk_engine_usd),
                                                                effective_min_trade_usd=float(eff_min_trade_usd),
                                                                sizing_cfg=sizing_cfg,
                                                                under_risk_meta=_under_risk_meta_s,
                                                            )
                                                        )
                                                        log_decision(
                                                            "entry_opened",
                                                            _opened_short,
                                                            symbol=symbol,
                                                            strategy_name=signal.strategy_name,
                                                            reason_code="ENTRY_OPENED_SHORT",
                                                        )
                                                        logging.getLogger(__name__).info("SHORT_SIGNAL_FOUND symbol=%s setup=%s", symbol, short_sig.setup_type)
                                                        opened += 1
                                                        opened_positions.append({
                                                            "symbol": signal.symbol,
                                                            "strategy": signal.strategy_name,
                                                            "side": signal.side,
                                                            "entry": position.entry_price,
                                                            "size_usd": round(final_size_usd, 2),
                                                            "stop_loss": signal.stop_loss,
                                                            "take_profit": signal.take_profit,
                                                            "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                                                            "risk_ceiling_usd": round(float(post_risk_engine_usd), 4),
                                                            "risk_efficiency_ratio": (
                                                                round(float(final_size_usd) / float(post_risk_engine_usd), 6)
                                                                if float(post_risk_engine_usd) > 0
                                                                else None
                                                            ),
                                                        })
                                                        if normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                                                            open_fast += 1
                                                        else:
                                                            open_core += 1
                                                        open_positions.append(position)
                                                        open_positions_for_allocation.append({"strategy_name": signal.strategy_name})
                                                    else:
                                                        sizing_trace_s = _build_sizing_trace_payload(
                                                            post_risk_engine_usd=post_risk_engine_usd,
                                                            size_after_vol=float(size_after_vol),
                                                            after_dynamic_usd=float(after_dynamic_usd_s),
                                                            after_combo_usd=after_combo_usd_s,
                                                            pre_modifier_usd=pre_modifier_usd,
                                                            post_policy_usd=post_policy_usd_s,
                                                            post_modifier_usd=post_modifier_usd,
                                                            signal=signal,
                                                            regime=regime,
                                                            profit_cfg=profit_cfg,
                                                            strategy_weight=strategy_weight,
                                                            portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                                                            entry_combo_mult=float(entry_combo_mult_s),
                                                            confidence_mult=conf_mult_s,
                                                            regime_score=reg_score_s,
                                                            mod_breakdown=mod_breakdown,
                                                            bot_edge_mult=be_mult_s,
                                                            available_cash=float(available_cash),
                                                            cap_quick=float(cap_quick_s),
                                                            rp_quick=float(rp_quick_s),
                                                            min_notional_usd=float(eff_min_trade_usd),
                                                        )
                                                        sizing_trace_s["reject_diagnosis"] = diagnose_size_too_small(
                                                            post_risk_engine_usd=float(post_risk_engine_usd),
                                                            size_after_vol=float(size_after_vol),
                                                            after_dynamic_usd=float(after_dynamic_usd_s),
                                                            pre_policy_usd=float(pre_modifier_usd),
                                                            post_policy_usd=float(post_policy_usd_s),
                                                            post_modifier_usd=float(post_modifier_usd),
                                                            estimate_max_from_risk_usd=float(
                                                                sizing_trace_s["estimate_max_from_risk_usd"]
                                                            ),
                                                            min_notional_usd=float(sizing_trace_s["min_notional_usd"]),
                                                            signal=signal,
                                                            entry_combo_mult=float(entry_combo_mult_s),
                                                            confidence_mult=float(conf_mult_s),
                                                            regime_score=float(reg_score_s),
                                                            strategy_weight=float(strategy_weight),
                                                            portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                                                            bot_edge_mult=float(be_mult_s),
                                                            mod_breakdown=mod_breakdown,
                                                        )
                                                        _lot_s = try_exchange_lot_for_executor(self.execution, symbol)
                                                        _ex_prev_s = (
                                                            exchange_qty_preview(
                                                                post_notional_usd=float(post_modifier_usd),
                                                                entry_price=float(signal.entry_price or 0),
                                                                lot=_lot_s,
                                                            )
                                                            if _lot_s
                                                            else None
                                                        )
                                                        sizing_trace_s["sizing_stage_diagnostics"] = build_sizing_stage_diagnostics(
                                                            post_risk_engine_usd=float(post_risk_engine_usd),
                                                            size_after_vol=float(size_after_vol),
                                                            after_dynamic_usd=float(after_dynamic_usd_s),
                                                            after_combo_usd=float(after_combo_usd_s),
                                                            pre_modifier_usd=float(pre_modifier_usd),
                                                            post_policy_usd=float(post_policy_usd_s),
                                                            post_modifier_usd=float(post_modifier_usd),
                                                            confidence_mult=float(conf_mult_s),
                                                            regime_score=float(reg_score_s),
                                                            strategy_weight=float(strategy_weight),
                                                            portfolio_heat_mult=float(allocation_result.portfolio_heat_mult),
                                                        )
                                                        _s_code_s, _s_detail_s = classify_post_sizing_reject(
                                                            post_modifier_usd=float(post_modifier_usd),
                                                            pre_modifier_usd=float(pre_modifier_usd),
                                                            post_policy_usd=float(post_policy_usd_s),
                                                            internal_min_trade_usd=float(eff_min_trade_usd),
                                                            mod_breakdown=mod_breakdown,
                                                            exchange_preview=_ex_prev_s,
                                                        )
                                                        sizing_trace_s["exchange_qty_preview"] = _ex_prev_s
                                                        sizing_trace_s["sizing_reject_classification"] = {
                                                            "reason_code": _s_code_s,
                                                            **_s_detail_s,
                                                        }
                                                        rej_pl_s = {
                                                            "side": signal.side or "short",
                                                            "strategy": signal.strategy_name,
                                                            "sizing_trace": sizing_trace_s,
                                                            "native_signal": _native_signal_log_slice(signal),
                                                            "setup": getattr(short_sig, "setup_type", None),
                                                            "sizing_reject_reason_code": _s_code_s,
                                                        }
                                                        rej_pl_s.update(
                                                            _build_risk_efficiency_fields(
                                                                final_size_usd=float(post_modifier_usd),
                                                                risk_ceiling_usd=float(post_risk_engine_usd),
                                                                effective_min_trade_usd=float(eff_min_trade_usd),
                                                                sizing_cfg=sizing_cfg,
                                                                under_risk_meta=_under_risk_meta_s,
                                                            )
                                                        )
                                                        _sz_cid_s = _decision_candle_id_1h(klines_1h_short)
                                                        if _sz_cid_s:
                                                            rej_pl_s["candle_id"] = _sz_cid_s
                                                        _attach_policy_squeeze_reject_audit(
                                                            rej_pl_s,
                                                            symbol=symbol,
                                                            strategy_name=signal.strategy_name,
                                                            reason_code=_s_code_s,
                                                            post_modifier_usd=float(post_modifier_usd),
                                                            eff_min_trade_usd=float(eff_min_trade_usd),
                                                            profit_cfg=profit_cfg,
                                                            single_strategy_mode=single_strategy or "",
                                                            blocking_stage=_s_detail_s.get("blocking_stage"),
                                                            sizing_stage_diagnostics=sizing_trace_s["sizing_stage_diagnostics"],
                                                            mod_breakdown=mod_breakdown,
                                                            pre_modifier_usd=float(pre_modifier_usd),
                                                            post_policy_usd=float(post_policy_usd_s),
                                                            bot_edge_mult=float(be_mult_s),
                                                        )
                                                        _rej_msg_s = (
                                                            f"Sizing reject ({_s_code_s}): final {float(post_modifier_usd):.2f} USD vs required min "
                                                            f"{float(eff_min_trade_usd):.2f} USD — stage={_s_detail_s.get('blocking_stage', '')} (short)"
                                                        )
                                                        log_decision(
                                                            "entry_rejected",
                                                            rej_pl_s,
                                                            symbol=symbol,
                                                            strategy_name=signal.strategy_name,
                                                            reason_code=_s_code_s,
                                                        )
                                                        _rej_meta_s = {
                                                            "combo_mult": entry_combo_mult_s,
                                                            "sizing_trace": sizing_trace_s,
                                                            "required_min_usd": float(eff_min_trade_usd),
                                                            "blocking_stage": _s_detail_s.get("blocking_stage"),
                                                        }
                                                        _rej_meta_s.update(
                                                            _build_risk_efficiency_fields(
                                                                final_size_usd=float(post_modifier_usd),
                                                                risk_ceiling_usd=float(post_risk_engine_usd),
                                                                effective_min_trade_usd=float(eff_min_trade_usd),
                                                                sizing_cfg=sizing_cfg,
                                                                under_risk_meta=_under_risk_meta_s,
                                                            )
                                                        )
                                                        if (
                                                            _s_code_s == "REDUCED_TOO_MUCH_BY_POLICY"
                                                            and rej_pl_s.get("policy_squeeze_detail")
                                                        ):
                                                            _rej_meta_s["policy_squeeze_detail"] = rej_pl_s["policy_squeeze_detail"]
                                                        rejected_signals.append({
                                                            "symbol": signal.symbol,
                                                            "strategy_name": signal.strategy_name,
                                                            "reason": _rej_msg_s,
                                                            "reason_code": _s_code_s,
                                                            "meta": _rej_meta_s,
                                                        })
                                        else:
                                            _risk_reject_code_s = classify_risk_reject_reason_code(decision.reason)
                                            log_decision(
                                                "entry_rejected",
                                                {
                                                    "side": signal.side or "short",
                                                    "strategy": signal.strategy_name,
                                                    "risk_reject_reason": decision.reason,
                                                    "setup": getattr(short_sig, "setup_type", None),
                                                    "native_signal": _native_signal_log_slice(signal),
                                                },
                                                symbol=symbol,
                                                strategy_name=signal.strategy_name,
                                                reason_code=_risk_reject_code_s,
                                            )
                                            rejected_signals.append({
                                                "symbol": signal.symbol,
                                                "strategy_name": signal.strategy_name,
                                                "reason": decision.reason,
                                                "reason_code": _risk_reject_code_s,
                                            })
        db.flush()
        _opened_syms = [str(p.get("symbol") or "").strip().upper() for p in (opened_positions or []) if p.get("symbol")]
        _signals_fired_syms = sorted(
            {str(s.get("symbol") or "").strip().upper() for s in signals_fired if s.get("symbol")}
        )
        def _reject_row_for_summary(_r: dict) -> dict:
            row = {
                "symbol": str(_r.get("symbol") or ""),
                "strategy_name": str(_r.get("strategy_name") or ""),
                "reason_code": str(_r.get("reason_code") or ""),
                "reason": (str(_r.get("reason") or "")[:200]),
            }
            meta = _r.get("meta")
            if isinstance(meta, dict):
                for k in (
                    "effective_signal_score",
                    "bot_edge_min_required",
                    "bot_edge_mode",
                    "bot_edge_system_score",
                    "required_min_usd",
                    "blocking_stage",
                ):
                    if meta.get(k) is not None:
                        row[k] = meta.get(k)
                st = meta.get("sizing_trace")
                if isinstance(st, dict):
                    fin = st.get("sizing_reject_classification")
                    if isinstance(fin, dict) and fin.get("final_size_usd") is not None:
                        row["final_size_usd"] = fin.get("final_size_usd")
                    elif st.get("post_all_modifiers_usd") is not None:
                        row["final_size_usd"] = st.get("post_all_modifiers_usd")
            return row

        _rej_sample = [_reject_row_for_summary(_r) for _r in (rejected_signals or [])[:100]]
        _cycle_duration_sec = round(float(_time.monotonic() - _cycle_started), 4)
        try:
            log_decision(
                "cycle_execution_summary",
                {
                    "effective_execution_symbols": [(s or "").strip().upper() for s in symbols],
                    "strategy_scope_in_cycle": strategy_scope_names,
                    "evaluated_strategy_rows_total": evaluated,
                    "evaluated_candidate_symbols": sorted(symbols_with_strategy_signal),
                    "candidate_rows_after_strategy_filter": strategy_evaluate_hit_count,
                    "signals_fired_count": len(signals_fired),
                    "signals_fired_symbols": _signals_fired_syms,
                    "rejected_count": len(rejected_signals),
                    "rejected_symbols_sample": _rej_sample,
                    "opened_count": opened,
                    "opened_symbols": _opened_syms,
                    "bot_edge": {
                        "selected_mode": bot_edge.selected_mode,
                        "bot_edge_system_score": round(float(bot_edge.bot_edge_score), 4),
                        "min_signal_score_mode_default": round(float(bot_edge.min_signal_score), 4),
                    },
                    "cycle_duration_sec": _cycle_duration_sec,
                },
                symbol=None,
                strategy_name=",".join(strategy_scope_names) if strategy_scope_names else None,
                reason_code="CYCLE_SUMMARY",
            )
        except Exception:
            pass
        return {
            "evaluated": evaluated,
            "opened": opened,
            "cycle_duration_sec": _cycle_duration_sec,
            "symbols": len(symbols),
            "strategy_scope_in_cycle": strategy_scope_names,
            "execution_universe_symbols": [(s or "").strip().upper() for s in symbols],
            "evaluated_candidate_symbols": sorted(symbols_with_strategy_signal),
            "candidate_rows_after_strategy_filter": strategy_evaluate_hit_count,
            "signals_fired_symbols": _signals_fired_syms,
            "opened_symbols": _opened_syms,
            "rejected_symbols_with_reasons": _rej_sample,
            "opened_positions": opened_positions,
            "signals_fired": signals_fired,
            "rejected_signals": rejected_signals,
            "skipped_already_open": skipped_already_open,
            "scale_ins_done": scale_ins_done,
            # Cùng công thức Dashboard / Kill switch (core.risk.daily_r)
            "daily_realized_usd": daily_realized,
            "daily_realized_r": round(float(daily_realized_r), 4),
            "risk_capital_usd": round(float(risk_capital_usd), 2),
            "capital_split_enabled": bool(cs_mgr.enabled),
            "daily_realized_core_usd": daily_realized_core,
            "daily_realized_fast_usd": daily_realized_fast,
            "open_core_positions": open_core,
            "open_fast_positions": open_fast,
            "brain_cycle_id": (brain_v4_ctx.brain_cycle_id if brain_v4_ctx else None),
            "market_decision_trace_id": (
                brain_v4_ctx.market_decision_trace_id if brain_v4_ctx else None
            ),
            "bot_edge": {
                "mode": bot_edge.selected_mode,
                "score": bot_edge.bot_edge_score,
                "rolling_pf": bot_edge.rolling_profit_factor,
                "rolling_n": bot_edge.rolling_trade_count,
                "reasons": list(bot_edge.reasons),
                "risk_mult": bot_edge.risk_multiplier,
                "tp_profile": bot_edge.tp_profile,
                "min_signal_score": bot_edge.min_signal_score,
                "max_concurrent": bot_edge.max_concurrent_trades,
            },
        }

    def sync_positions_from_binance(self, db: Session, portfolio_name: str) -> dict:
        """
        Đồng bộ DB với Binance:
        1) Vị thế đã đóng trên sàn (TP/SL/Trailing) → đánh dấu đóng trong DB, ghi Trade + PnL từ sàn.
        2) Binance gộp vị thế cùng symbol+side thành một → nếu DB có nhiều bản ghi Position cho cùng (symbol, side)
           thì cập nhật một bản ghi khớp sàn (quantity, entry_price), đánh dấu các bản ghi còn lại là đã gộp (is_open=False).
        """
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return {"closed": 0, "merged": 0}
        open_positions = list(db.scalars(
            select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id)
        ))
        if not open_positions:
            return {"closed": 0, "merged": 0}
        executor = get_execution_backend()
        if not hasattr(executor, "get_binance_open_positions"):
            return {"closed": 0, "merged": 0}
        try:
            binance_list = executor.get_binance_open_positions()
        except Exception:
            return {"closed": 0, "merged": 0}
        binance_set = {(b["symbol"], b["position_side"]) for b in binance_list}
        binance_by_key = {(b["symbol"], b["position_side"]): b for b in binance_list}
        hedge = getattr(executor, "_hedge_mode", None)
        if hedge is None and hasattr(executor, "_signed_request"):
            from core.execution.binance_futures import _is_hedge_mode
            hedge = _is_hedge_mode(executor)

        # Bước 1: Gộp theo sàn — Binance chỉ có 1 vị thế mỗi (symbol, side); nếu DB có 2+ bản ghi thì đồng bộ về 1.
        merged_count = 0
        for (sym, pos_side), binance_pos in binance_by_key.items():
            side = binance_pos.get("side", "long" if pos_side == "LONG" else "short")
            ours = [p for p in open_positions if p.is_open and p.symbol == sym and p.side == side]
            if len(ours) < 2:
                continue
            qty_b = float(binance_pos.get("quantity", 0) or 0)
            entry_b = float(binance_pos.get("entry_price", 0) or 0)
            if qty_b <= 0:
                continue
            ours_sorted = sorted(ours, key=lambda p: p.id)
            primary = ours_sorted[0]
            primary.quantity = qty_b
            primary.entry_price = entry_b
            if hasattr(executor, "get_current_sl_tp_from_binance"):
                sl_b, tp_b = executor.get_current_sl_tp_from_binance(sym, pos_side)
                if sl_b is not None:
                    primary.stop_loss = sl_b
                if tp_b is not None:
                    primary.take_profit = tp_b
            for extra in ours_sorted[1:]:
                extra.is_open = False
                extra.closed_at = datetime.utcnow()
                merged_count += 1
            open_positions = [p for p in open_positions if p.is_open]
        if merged_count:
            db.flush()

        # Bước 2: Vị thế có trong DB nhưng không còn trên sàn → đóng trong DB, ghi Trade + PnL.
        to_close = []
        for pos in open_positions:
            position_side = ("LONG" if pos.side == "long" else "SHORT") if hedge else "BOTH"
            if (pos.symbol, position_side) not in binance_set:
                to_close.append(pos)
        if not to_close:
            return {"closed": 0, "merged": merged_count}
        # Lấy PnL thực tế từ Binance (REALIZED_PNL) để ghi đúng lịch sử thay vì 0
        income_by_symbol: dict[str, list] = {}
        if hasattr(executor, "get_recent_realized_pnl_for_symbol"):
            for pos in to_close:
                if pos.symbol not in income_by_symbol:
                    income_by_symbol[pos.symbol] = list(executor.get_recent_realized_pnl_for_symbol(pos.symbol))
        closed_count = 0
        for pos in to_close:
            pnl_usd = 0.0
            exit_price = pos.entry_price
            incomes = income_by_symbol.get(pos.symbol) or []
            if incomes:
                used = incomes.pop(0)
                pnl_usd = used["income"]
                qty = float(pos.quantity) if pos.quantity else 1e-9
                if qty > 0:
                    if pos.side == "long":
                        exit_price = pos.entry_price + pnl_usd / qty
                    else:
                        exit_price = pos.entry_price - pnl_usd / qty
            pos.is_open = False
            pos.closed_at = datetime.utcnow()
            risk_usd = risk_usd_for_full_close(pos)
            if risk_usd is not None and float(risk_usd) < MIN_RISK_USD_FOR_R_AGGREGATION:
                risk_usd = None
            realized_r = (
                round(float(pnl_usd) / float(risk_usd), 4)
                if risk_usd is not None and float(risk_usd) > 0
                else None
            )
            close_trade = Trade(
                portfolio_id=pos.portfolio_id,
                position_id=pos.id,
                symbol=pos.symbol,
                side=pos.side,
                strategy_name=pos.strategy_name or "",
                action="close",
                price=round(exit_price, 8),
                quantity=pos.quantity,
                fee_usd=0.0,
                pnl_usd=round(pnl_usd, 4),
                risk_usd=round(risk_usd, 4) if risk_usd is not None else None,
                close_source="sync_binance_reconcile",
                realized_r_multiple=realized_r,
                note="Đồng bộ từ Binance: không còn vị thế trên sàn (TP/SL/Trailing đã kích hoạt)",
                capital_bucket=normalize_bucket(getattr(pos, "capital_bucket", None)),
            )
            db.add(close_trade)
            # Đồng bộ từ Binance: tiền thật ở trên sàn, không cập nhật portfolio.cash_usd (khi mở Binance ta cũng không trừ cash). Cộng notional vào cash sẽ gây số dư cộng dồn sai.
            closed_count += 1
            self.journal.record_outcome_from_close(db, pos, close_trade)
        db.flush()
        return {"closed": closed_count, "merged": merged_count}

    def check_sl_tp_and_close(self, db: Session, portfolio_name: str) -> dict:
        """
        Kiểm tra mọi lệnh đang mở: nếu giá hiện tại đã chạm SL hoặc TP thì đóng lệnh.
        Paper: đóng trong DB. Binance: lệnh TP/SL do sàn xử lý; đây chỉ đồng bộ DB khi cần.
        """
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return {"closed": 0, "reason": "no_portfolio"}
        open_positions = list(db.scalars(
            select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id)
        ))
        if not open_positions:
            return {"closed": 0, "reason": "no_open_positions"}
        symbols = list({p.symbol for p in open_positions})
        quotes = get_quotes_with_fallback(symbols)
        executor = get_execution_backend()
        paper = PaperExecutionSimulator()
        closed = 0
        for pos in open_positions:
            if pos.symbol not in quotes:
                continue
            price_now = quotes[pos.symbol].price
            sl = float(pos.stop_loss) if pos.stop_loss is not None else None
            tp = float(pos.take_profit) if pos.take_profit is not None else None
            exit_price = None
            note = ""
            if pos.side == "long":
                if sl is not None and price_now <= sl:
                    exit_price = sl
                    note = "SL kích hoạt (giá chạm stop loss)"
                elif tp is not None and price_now >= tp:
                    exit_price = tp
                    note = "TP kích hoạt (giá chạm take profit)"
            else:
                if sl is not None and price_now >= sl:
                    exit_price = sl
                    note = "SL kích hoạt (giá chạm stop loss)"
                elif tp is not None and price_now <= tp:
                    exit_price = tp
                    note = "TP kích hoạt (giá chạm take profit)"
            if exit_price is not None:
                close_trade = None
                try:
                    close_trade = executor.close_position(db, pos, exit_price, note=note)
                    closed += 1
                except Exception:
                    try:
                        close_trade = paper.close_position(db, pos, exit_price, note=note)
                        closed += 1
                    except Exception:
                        pass
                if close_trade:
                    self.journal.record_outcome_from_close(db, pos, close_trade)
        db.flush()
        return {"closed": closed}

    def review_positions_and_act(
        self,
        db: Session,
        portfolio_name: str,
        brain_v4_ctx: BrainV4CycleContext | None = None,
        brain_cycle_id: str | None = None,
    ) -> list[dict]:
        """
        Chủ động đọc từng vị thế đang mở, quyết định hành động (CLOSE / UPDATE_TP_SL / HOLD) và thực hiện.
        Trả về danh sách [{symbol, side, action, reason}, ...] để log. Đây là cơ chế 'giải pháp cho vị thế hiện tại'.
        """
        from core.patterns.candlestick import detect_patterns
        from core.reflection.sl_tp_update import suggest_sl_tp_update, get_learned_max_tp_pct

        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return []
        open_positions = list(db.scalars(
            select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id)
        ))
        if not open_positions:
            return []
        quotes = get_quotes_with_fallback([p.symbol for p in open_positions])
        executor = get_execution_backend()
        paper = PaperExecutionSimulator()
        try:
            from core.brain.portfolio_state import maybe_persist_portfolio_state_tick

            maybe_persist_portfolio_state_tick(
                db,
                portfolio_id=portfolio.id,
                equity_usd=float(portfolio.capital_usd or 0),
                open_positions=open_positions,
                quotes=quotes,
                brain_cycle_id=brain_cycle_id or (brain_v4_ctx.brain_cycle_id if brain_v4_ctx else None),
                decision_trace_id=(brain_v4_ctx.market_decision_trace_id if brain_v4_ctx else None),
                daily_realized_pnl_usd=0.0,
            )
        except Exception:
            pass
        max_hours = max(0.0, float(getattr(settings, "max_hold_hours", 0) or 0))
        close_if_risk_off = bool(getattr(settings, "proactive_close_if_risk_off", False))
        now = datetime.utcnow()
        actions = []
        # Binance chỉ có 1 bộ TP/SL cho mỗi (symbol, side); tránh gọi update_position_sl_tp nhiều lần cùng cycle → race/-4130
        updated_symbol_side: set[tuple[str, str]] = set()

        _class_cfg_review = load_classification_config()
        cs_rev = load_capital_split_config()
        max_min_fast = int(cs_rev.get("max_hold_minutes_fast", 0) or 0) if cs_rev.get("enabled") else 0
        be_cfg_r = load_bot_edge_config()
        bot_edge_r = None
        if be_cfg_r.get("enabled", True):
            today_start_r = datetime.combine(date.today(), time.min)
            today_end_r = today_start_r + timedelta(days=1)
            closed_today_r = list(
                db.scalars(
                    select(Trade).where(
                        Trade.action == "close",
                        Trade.portfolio_id == portfolio.id,
                        Trade.created_at >= today_start_r,
                        Trade.created_at < today_end_r,
                    )
                )
            )
            dr_r = sum_daily_realized_r_from_trades(closed_today_r)
            dp_r = round(sum(float(t.pnl_usd or 0) for t in closed_today_r), 2)
            rc_r = effective_risk_capital_usd(getattr(portfolio, "capital_usd", None))
            bot_edge_r = compute_bot_edge_decision(
                db,
                portfolio.id,
                quotes=quotes,
                daily_realized_r=dr_r,
                daily_realized_pnl_usd=dp_r,
                risk_capital_usd=rc_r,
                brain_market_state=(
                    str(getattr(brain_v4_ctx, "market_state", "") or "")
                    if brain_v4_ctx
                    else None
                ),
            )
        max_min_fast_bot = max_min_fast
        if (
            bot_edge_r
            and bot_edge_r.selected_mode != "OFF"
            and bot_edge_r.max_hold_minutes_fast > 0
        ):
            if max_min_fast_bot > 0:
                max_min_fast_bot = min(max_min_fast_bot, bot_edge_r.max_hold_minutes_fast)
            else:
                max_min_fast_bot = bot_edge_r.max_hold_minutes_fast
        for pos in open_positions:
            if pos.symbol not in quotes:
                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": "không có giá"})
                continue
            price_now = quotes[pos.symbol].price
            try:
                from core.brain.thesis_persistence import thesis_tick_update_position

                k_th: list = []
                try:
                    k_th = get_klines_1h(pos.symbol, limit=48)
                except Exception:
                    k_th = []
                cid_t = brain_cycle_id or (brain_v4_ctx.brain_cycle_id if brain_v4_ctx else None)
                dtrace_t = None
                if brain_v4_ctx:
                    dtrace_t = brain_v4_ctx.symbol_decision_trace_ids.get(pos.symbol) or brain_v4_ctx.trace_id
                th = thesis_tick_update_position(
                    db,
                    pos,
                    price_now,
                    k_th,
                    brain_cycle_id=cid_t,
                    decision_trace_id=dtrace_t,
                )
                if th.get("force_close") and th.get("close_note"):
                    note_tf = th["close_note"]
                    try:
                        executor.close_position(db, pos, price_now, note=f"Thesis: {note_tf}")
                        actions.append(
                            {"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": note_tf}
                        )
                    except Exception:
                        try:
                            paper.close_position(db, pos, price_now, note=f"Thesis: {note_tf}")
                            actions.append(
                                {"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": note_tf}
                            )
                        except Exception:
                            actions.append(
                                {
                                    "symbol": pos.symbol,
                                    "side": pos.side,
                                    "action": "HOLD",
                                    "reason": f"thesis close failed ({note_tf})",
                                }
                            )
                    continue
            except Exception:
                pass
            try:
                from core.brain.integration import try_brain_v4_reflex_for_position

                handled, reflex_action = try_brain_v4_reflex_for_position(
                    db,
                    portfolio,
                    pos,
                    price_now,
                    quotes,
                    executor,
                    paper,
                    brain_cycle_id=brain_cycle_id or (brain_v4_ctx.brain_cycle_id if brain_v4_ctx else None),
                    market_decision_trace_id=(
                        brain_v4_ctx.market_decision_trace_id if brain_v4_ctx else None
                    ),
                )
                if handled and reflex_action:
                    actions.append(reflex_action)
                    continue
            except Exception:
                pass
            direction = 1 if pos.side == "long" else -1
            pnl_pct = (price_now - pos.entry_price) / pos.entry_price * direction * 100 if pos.entry_price else 0
            age_hours = (now - pos.opened_at).total_seconds() / 3600.0 if getattr(pos, "opened_at", None) else 0
            pos_profile_review = None
            if _class_cfg_review.get("enabled", True):
                try:
                    klines_rev = get_klines_1h(pos.symbol, limit=25)
                    quote_rev = quotes.get(pos.symbol)
                    if quote_rev and klines_rev:
                        features_rev = build_token_features(pos.symbol, quote_rev, klines_rev, _class_cfg_review)
                        pos_profile_review = classify_token(pos.symbol, features_rev, _class_cfg_review)
                except Exception:
                    pass
            max_hours_pos = max_hours
            if pos_profile_review and pos_profile_review.risk_profile and "timeout_bars" in pos_profile_review.risk_profile:
                try:
                    max_hours_pos = float(pos_profile_review.risk_profile["timeout_bars"])
                except (TypeError, ValueError):
                    pass
            # 1) Có cần đóng chủ động không?
            note_close = ""
            if cs_rev.get("enabled") and normalize_bucket(getattr(pos, "capital_bucket", None)) == "fast":
                try:
                    k_nft = get_klines_1h(pos.symbol, limit=48)
                except Exception:
                    k_nft = []
                do_nft, nft_reason = fast_no_follow_through_should_close(
                    pos, price_now=price_now, klines=k_nft, cs_cfg=cs_rev, now=now,
                )
                if do_nft:
                    note_close = nft_reason
            if (
                not note_close
                and bot_edge_r
                and bot_edge_r.selected_mode != "OFF"
                and bot_edge_r.max_hold_minutes_core > 0
                and normalize_bucket(getattr(pos, "capital_bucket", None)) == "core"
                and getattr(pos, "opened_at", None)
            ):
                age_min_c = (now - pos.opened_at).total_seconds() / 60.0
                if age_min_c >= bot_edge_r.max_hold_minutes_core:
                    note_close = (
                        f"bot_edge core time-stop ({age_min_c:.0f}m >= {bot_edge_r.max_hold_minutes_core}m)"
                    )
            if (
                not note_close
                and max_min_fast_bot > 0
                and normalize_bucket(getattr(pos, "capital_bucket", None)) == "fast"
            ):
                age_min = (now - pos.opened_at).total_seconds() / 60.0 if getattr(pos, "opened_at", None) else 0
                if age_min >= max_min_fast_bot:
                    note_close = f"fast capital time-stop ({age_min:.0f}m >= {max_min_fast_bot}m)"
            if not note_close and max_hours_pos > 0 and age_hours >= max_hours_pos:
                note_close = f"đã giữ {age_hours:.1f}h (tối đa {max_hours_pos}h)"
            if not note_close and close_if_risk_off:
                regime = derive_regime(
                    quotes[pos.symbol].percent_change_24h,
                    quotes[pos.symbol].volume_24h,
                )
                if regime == "risk_off" and pos.side == "long":
                    note_close = "regime risk_off (giảm rủi ro)"
                elif regime == "high_momentum" and pos.side == "short":
                    note_close = "regime high_momentum (short không thuận)"
            if note_close:
                try:
                    executor.close_position(db, pos, price_now, note=f"Đóng chủ động: {note_close}")
                    actions.append({"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": note_close})
                except Exception:
                    try:
                        paper.close_position(db, pos, price_now, note=f"Đóng chủ động: {note_close}")
                        actions.append({"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": note_close})
                    except Exception:
                        actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"lỗi khi đóng ({note_close})"})
                continue

            # 1a) Decision layer v6: HOLD / REDUCE / CLOSE / HEDGE_PARTIAL — hedge chỉ khi hợp lệ
            pnl_usd = (price_now - pos.entry_price) * direction * float(pos.quantity or 0) if pos.quantity else 0
            risk_usd = None
            if pos.stop_loss is not None and pos.quantity:
                risk_usd = abs(float(pos.entry_price) - float(pos.stop_loss)) * float(pos.quantity)
            try:
                from core.hedge.hedge_config import load_hedge_config
                from core.hedge import hedge_allowed_for_position, hedge_size_usd
                from core.hedge.hedge_executor import open_hedge_position
                from core.hedge.hedge_unwind_engine import get_hedge_positions_for_main
                hedge_cfg = load_hedge_config()
                pos_profile = pos_profile_review
                if hedge_cfg.get("enabled") and (pos_profile is None or pos_profile.hedge_policy != "disabled"):
                    allowed, hedge_reason = hedge_allowed_for_position(pos, pnl_usd, risk_usd, hedge_cfg)
                    if allowed:
                        main_size_usd = float(pos.entry_price or 0) * float(pos.quantity or 0)
                        size_hedge = hedge_size_usd(
                            main_size_usd, float(pos.quantity or 0), float(pos.entry_price or 0),
                            pnl_usd, risk_usd, hedge_cfg,
                        )
                        hedges = get_hedge_positions_for_main(db, pos.id)
                        if size_hedge >= 25 and not hedges:
                            try:
                                hedge_pos = open_hedge_position(
                                    db, portfolio.id, pos, size_hedge, price_now, hedge_reason=hedge_reason,
                                )
                                if hedge_pos:
                                    ratio = size_hedge / main_size_usd if main_size_usd > 0 else 0
                                    open_hedge_trade = db.scalar(
                                        select(Trade).where(
                                            Trade.position_id == hedge_pos.id,
                                            Trade.action == "open",
                                        )
                                    )
                                    hedge_signal = StrategySignal(
                                        symbol=pos.symbol,
                                        strategy_name="hedge",
                                        side="short" if pos.side == "long" else "long",
                                        confidence=0.5,
                                        entry_price=price_now,
                                        stop_loss=hedge_pos.stop_loss,
                                        take_profit=hedge_pos.take_profit,
                                        rationale=f"Hedge: {hedge_reason}",
                                        regime="",
                                    )
                                    self.journal.create_entry(
                                        db, hedge_signal, hedge_reason,
                                        setup_score=50.0,
                                        trade_id=open_hedge_trade.id if open_hedge_trade else None,
                                        side=hedge_signal.side,
                                        hedge_reason=hedge_reason,
                                        hedge_ratio=ratio,
                                    )
                                    actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HEDGE_PARTIAL", "reason": hedge_reason})
                                    logging.getLogger(__name__).info("HEDGE_PLACED symbol=%s main_side=%s size_usd=%s ratio=%s", pos.symbol, pos.side, size_hedge, round(ratio, 2))
                            except Exception as e:
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"hedge lỗi: {e}"})
                        else:
                            actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HEDGE_PARTIAL", "reason": hedge_reason})
                    else:
                        logging.getLogger(__name__).info("HEDGE_REJECTED symbol=%s reason=%s", pos.symbol, hedge_reason)
            except Exception:
                pass

            # 1b) Proactive exit engine (profit protection + reversal + TP1 partial)
            try:
                klines = get_klines_1h(pos.symbol, limit=20)
            except Exception:
                klines = []
            quote = quotes.get(pos.symbol)
            has_partial = any(
                t.action == "partial_close"
                for t in list(db.scalars(select(Trade).where(Trade.position_id == pos.id)))
            )
            try:
                from core.profit.proactive_exit_engine import (
                    evaluate_position,
                    load_proactive_exit_config,
                )
                pe_cfg = load_proactive_exit_config()
                if brain_v4_ctx:
                    try:
                        from core.brain.policy_apply import merge_proactive_exit_overlay

                        pe_cfg = merge_proactive_exit_overlay(
                            pe_cfg, brain_v4_ctx.policy.active_policy_mode
                        )
                    except Exception:
                        pass
                else:
                    try:
                        from core.brain.policy_apply import merge_proactive_exit_overlay
                        from core.brain.runtime_state import load_runtime_state

                        rt_pe = load_runtime_state()
                        pe_cfg = merge_proactive_exit_overlay(pe_cfg, rt_pe.policy_mode)  # type: ignore[arg-type]
                    except Exception:
                        pass
                if pe_cfg.get("enabled", True) and klines and quote:
                    pe_result = evaluate_position(
                        pos, price_now, klines, quote, pe_cfg, has_partial_closed=has_partial
                    )
                    if pe_result.action == "PROACTIVE_CLOSE":
                        try:
                            executor.close_position(
                                db, pos, price_now,
                                note=f"Proactive exit: {pe_result.reason_code} (score {pe_result.reversal_exit_score or 0:.2f})",
                            )
                            actions.append({"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": pe_result.reason})
                        except Exception:
                            try:
                                paper.close_position(db, pos, price_now, note=f"Proactive exit: {pe_result.reason_code}")
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": pe_result.reason})
                            except Exception:
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"proactive exit lỗi: {pe_result.reason}"})
                        continue
                    if pe_result.action == "PARTIAL_TP" and pe_result.partial_tp_pct > 0:
                        reduce_qty = round(float(pos.quantity) * pe_result.partial_tp_pct, 8)
                        if reduce_qty > 0 and reduce_qty < pos.quantity:
                            try:
                                if hasattr(executor, "reduce_position"):
                                    executor.reduce_position(db, pos, reduce_qty, price_now, note=f"Partial TP: {pe_result.reason_code}")
                                else:
                                    paper.reduce_position(db, pos, reduce_qty, price_now, note=f"Partial TP: {pe_result.reason_code}")
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "PARTIAL_TP", "reason": pe_result.reason})
                            except Exception:
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"partial TP lỗi: {pe_result.reason}"})
                        continue
                    if pe_result.action == "MOVE_SL" and pe_result.suggested_sl is not None:
                        key_ss = (pos.symbol, pos.side)
                        if key_ss not in updated_symbol_side:
                            try:
                                executor.update_position_sl_tp(db, pos, pe_result.suggested_sl, pos.take_profit, note=pe_result.reason_code or pe_result.reason)
                                updated_symbol_side.add(key_ss)
                                for other in open_positions:
                                    if (other.symbol, other.side) == key_ss and other.stop_loss is not None:
                                        other.stop_loss = pe_result.suggested_sl
                                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "UPDATE_TP_SL", "reason": pe_result.reason})
                            except Exception:
                                pass
                        continue
            except Exception:
                pass

            # 2) Có cần cập nhật TP/SL không? (pattern + ATR + cấu trúc + học từ lệnh)
            if not klines:
                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"giữ (PnL ~{pnl_pct:+.1f}%, {age_hours:.0f}h), không có nến"})
                continue
            patterns = detect_patterns(klines)
            learned_tp_pct = get_learned_max_tp_pct(db, portfolio.id, symbol=pos.symbol, side=pos.side) if portfolio else None
            qty = float(pos.quantity) if pos.quantity else None
            position_age_minutes = age_hours * 60.0
            position_age_sec = age_hours * 3600.0
            # Guard: không cho AI can thiệp TP/SL khi position quá mới và PnL chưa đáng kể. Coin biến động mạnh (high/extreme) dùng ngưỡng ngắn hơn (3m, 0.5%).
            volatility_tier = _get_volatility_tier_for_position(pos.symbol, quote, klines, _time.monotonic())
            if volatility_tier in ("high", "extreme"):
                min_age_minutes = float(getattr(settings, "ai_sl_tp_min_age_minutes_high_vol", 3) or 3)
                min_pnl_pct_for_ai = float(getattr(settings, "ai_sl_tp_min_pnl_pct_high_vol", 0.5) or 0.5)
            else:
                min_age_minutes = float(getattr(settings, "ai_sl_tp_min_age_minutes", 5) or 5)
                min_pnl_pct_for_ai = float(getattr(settings, "ai_sl_tp_min_pnl_pct", 0.8) or 0.8)
            use_ai_sl_tp = True
            if position_age_minutes < min_age_minutes and pnl_pct < min_pnl_pct_for_ai:
                use_ai_sl_tp = False
                logging.getLogger(__name__).debug(
                    "skip_ai_sl_tp_update symbol=%s side=%s reason=position_too_new age_min=%.1f pnl_pct=%.2f",
                    pos.symbol, pos.side, position_age_minutes, pnl_pct,
                )
            min_age_sec_review = 180.0 if volatility_tier in ("high", "extreme") else 300.0
            suggestion = suggest_sl_tp_update(
                position_side=pos.side,
                entry_price=pos.entry_price,
                current_sl=pos.stop_loss,
                current_tp=pos.take_profit,
                candles=klines,
                patterns=patterns,
                current_price=price_now,
                use_ai=use_ai_sl_tp,
                learned_max_tp_pct=learned_tp_pct,
                quantity=qty,
                symbol_key=(pos.symbol, pos.side),
                position_age_sec=position_age_sec,
                min_age_sec_initial_review=min_age_sec_review,
            )
            if suggestion:
                new_sl, new_tp, reason = suggestion
                if (new_sl is not None and new_sl != pos.stop_loss) or (new_tp is not None and new_tp != pos.take_profit):
                    valid = True
                    if new_sl is not None:
                        if pos.side == "long" and new_sl >= price_now:
                            valid = False
                        if pos.side == "short" and new_sl <= price_now:
                            valid = False
                    if new_tp is not None and valid:
                        if pos.side == "long" and new_tp <= price_now:
                            valid = False
                        if pos.side == "short" and new_tp >= price_now:
                            valid = False
                    if valid:
                        key_ss = (pos.symbol, pos.side)
                        if key_ss in updated_symbol_side:
                            actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"đã cập nhật TP/SL cho {pos.symbol} {pos.side} trong cycle này (1 bộ/symbol trên sàn)"})
                            continue
                        sl_final = new_sl if new_sl is not None else pos.stop_loss
                        tp_final = new_tp if new_tp is not None else pos.take_profit
                        try:
                            executor.update_position_sl_tp(db, pos, sl_final, tp_final, note=reason)
                            updated_symbol_side.add(key_ss)
                            # Đồng bộ SL/TP vào mọi Position cùng (symbol, side) — trên sàn chỉ có 1 bộ
                            for other in open_positions:
                                if (other.symbol, other.side) == key_ss:
                                    if sl_final is not None:
                                        other.stop_loss = sl_final
                                    if tp_final is not None:
                                        other.take_profit = tp_final
                            actions.append({"symbol": pos.symbol, "side": pos.side, "action": "UPDATE_TP_SL", "reason": reason})
                        except Exception:
                            actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"gợi ý TP/SL nhưng lỗi áp dụng: {reason}"})
                    else:
                        actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"giữ (PnL ~{pnl_pct:+.1f}%), gợi ý không hợp lệ"})
                else:
                    actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"giữ (PnL ~{pnl_pct:+.1f}%), pattern không đổi TP/SL"})
            else:
                pat_str = ", ".join(patterns) if patterns else "không"
                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": f"giữ (PnL ~{pnl_pct:+.1f}%, {age_hours:.0f}h), pattern [{pat_str}] không gợi ý đổi"})

        # Unwind hedges: close when timeout or pullback done
        try:
            from core.hedge.hedge_config import load_hedge_config
            from core.hedge.hedge_unwind_engine import should_unwind_hedge, get_hedge_positions_for_main
            hedge_cfg = load_hedge_config()
            for pos in list(db.scalars(select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id))):
                main_id = getattr(pos, "hedge_of_position_id", None)
                if main_id is None:
                    continue
                main_pos = db.get(Position, main_id)
                if not main_pos or not main_pos.is_open:
                    continue
                try:
                    klines_u = get_klines_1h(pos.symbol, limit=20)
                except Exception:
                    klines_u = []
                price_u = quotes.get(pos.symbol)
                price_u = price_u.price if price_u else 0
                do_unwind, reason = should_unwind_hedge(pos, main_pos, price_u, klines_u, hedge_cfg)
                if do_unwind and reason:
                    try:
                        executor.close_position(db, pos, price_u, note=f"Unwind: {reason}")
                    except Exception:
                        try:
                            paper.close_position(db, pos, price_u, note=f"Unwind: {reason}")
                        except Exception:
                            pass
                    actions.append({"symbol": pos.symbol, "side": pos.side, "action": "CLOSE", "reason": reason})
                    logging.getLogger(__name__).info("HEDGE_UNWIND symbol=%s reason=%s", pos.symbol, reason)
        except Exception:
            pass
        db.flush()
        return actions

    def check_proactive_close(self, db: Session, portfolio_name: str) -> dict:
        """
        Kiểm tra lệnh đang mở và đóng chủ động trước thời hạn nếu cấu hình bật:
        - max_hold_hours > 0: đóng mọi vị thế đã giữ quá N giờ.
        - proactive_close_if_risk_off: đóng long khi regime = risk_off (giảm rủi ro khi thị trường xấu).
        """
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return {"closed": 0, "reason": "no_portfolio"}
        open_positions = list(db.scalars(
            select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id)
        ))
        if not open_positions:
            return {"closed": 0, "reason": "no_open_positions"}
        max_hours = max(0.0, float(getattr(settings, "max_hold_hours", 0) or 0))
        close_if_risk_off = bool(getattr(settings, "proactive_close_if_risk_off", False))
        cs_pc = load_capital_split_config()
        max_min_fast_pc = int(cs_pc.get("max_hold_minutes_fast", 0) or 0) if cs_pc.get("enabled") else 0
        if max_hours <= 0 and not close_if_risk_off and max_min_fast_pc <= 0:
            return {"closed": 0, "reason": "proactive_close_disabled"}
        symbols = list({p.symbol for p in open_positions})
        quotes = get_quotes_with_fallback(symbols)
        executor = get_execution_backend()
        paper = PaperExecutionSimulator()
        now = datetime.utcnow()
        closed = 0
        for pos in open_positions:
            if pos.symbol not in quotes:
                continue
            price_now = quotes[pos.symbol].price
            exit_price = price_now
            note = ""
            if max_min_fast_pc > 0 and normalize_bucket(getattr(pos, "capital_bucket", None)) == "fast":
                if getattr(pos, "opened_at", None):
                    age_min = (now - pos.opened_at).total_seconds() / 60.0
                    if age_min >= max_min_fast_pc:
                        note = f"Đóng chủ động: fast time-stop ({age_min:.0f}m >= {max_min_fast_pc}m)"
            if not note and max_hours > 0 and getattr(pos, "opened_at", None):
                age_hours = (now - pos.opened_at).total_seconds() / 3600.0
                if age_hours >= max_hours:
                    note = f"Đóng chủ động: đã giữ {age_hours:.1f}h (tối đa {max_hours}h)"
            if not note and close_if_risk_off:
                regime = derive_regime(
                    quotes[pos.symbol].percent_change_24h,
                    quotes[pos.symbol].volume_24h,
                )
                if regime == "risk_off" and pos.side == "long":
                    note = "Đóng chủ động: regime risk_off (giảm rủi ro)"
                elif regime == "high_momentum" and pos.side == "short":
                    note = "Đóng chủ động: regime high_momentum (short không thuận)"
            if note:
                try:
                    executor.close_position(db, pos, exit_price, note=note)
                    closed += 1
                except Exception:
                    try:
                        paper.close_position(db, pos, exit_price, note=note)
                        closed += 1
                    except Exception:
                        pass
        db.flush()
        return {"closed": closed}

    def check_patterns_and_update_sl_tp(self, db: Session, portfolio_name: str) -> dict:
        """
        Với mỗi vị thế đang mở có TP/SL: lấy nến 1h, phát hiện hình nến (hammer, engulfing, …),
        gợi ý cập nhật TP/SL (rule + AI nếu có key) và áp dụng nếu hợp lý.
        """
        from core.patterns.candlestick import detect_patterns
        from core.reflection.sl_tp_update import suggest_sl_tp_update, get_learned_max_tp_pct

        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return {"updated": 0}
        open_positions = list(db.scalars(
            select(Position).where(Position.is_open == True, Position.portfolio_id == portfolio.id)
        ))
        if not open_positions:
            return {"updated": 0}
        quotes = get_quotes_with_fallback([p.symbol for p in open_positions])
        executor = get_execution_backend()
        updated = 0
        for pos in open_positions:
            if pos.symbol not in quotes:
                continue
            price_now = quotes[pos.symbol].price
            try:
                klines = get_klines_1h(pos.symbol, limit=20)
            except Exception:
                continue
            if not klines:
                continue
            patterns = detect_patterns(klines)
            if not patterns:
                continue
            learned_tp_pct = None
            if portfolio:
                learned_tp_pct = get_learned_max_tp_pct(db, portfolio.id, symbol=pos.symbol, side=pos.side)
            qty = float(pos.quantity) if pos.quantity else None
            now_utc = datetime.utcnow()
            age_hours_st = (now_utc - pos.opened_at).total_seconds() / 3600.0 if getattr(pos, "opened_at", None) else 0
            position_age_sec_st = age_hours_st * 3600.0
            direction_st = 1 if (pos.side or "").lower() == "long" else -1
            pnl_pct_st = (price_now - pos.entry_price) / pos.entry_price * direction_st * 100 if pos.entry_price else 0
            vol_tier_st = _get_volatility_tier_for_position(pos.symbol, quotes.get(pos.symbol), klines, _time.monotonic())
            if vol_tier_st in ("high", "extreme"):
                min_age_min = float(getattr(settings, "ai_sl_tp_min_age_minutes_high_vol", 3) or 3)
                min_pnl_for_ai = float(getattr(settings, "ai_sl_tp_min_pnl_pct_high_vol", 0.5) or 0.5)
            else:
                min_age_min = float(getattr(settings, "ai_sl_tp_min_age_minutes", 5) or 5)
                min_pnl_for_ai = float(getattr(settings, "ai_sl_tp_min_pnl_pct", 0.8) or 0.8)
            use_ai_st = not (age_hours_st * 60 < min_age_min and pnl_pct_st < min_pnl_for_ai)
            min_age_sec_review_st = 180.0 if vol_tier_st in ("high", "extreme") else 300.0
            suggestion = suggest_sl_tp_update(
                position_side=pos.side,
                entry_price=pos.entry_price,
                current_sl=pos.stop_loss,
                current_tp=pos.take_profit,
                candles=klines,
                patterns=patterns,
                current_price=price_now,
                use_ai=use_ai_st,
                learned_max_tp_pct=learned_tp_pct,
                quantity=qty,
                symbol_key=(pos.symbol, pos.side),
                position_age_sec=position_age_sec_st,
                min_age_sec_initial_review=min_age_sec_review_st,
            )
            if suggestion is None:
                continue
            new_sl, new_tp, reason = suggestion
            if new_sl is None and new_tp is None:
                continue
            # Chỉ cập nhật khi thực sự đổi (và hợp lý: long SL < price, short SL > price)
            if new_sl is not None and new_sl != pos.stop_loss:
                if pos.side == "long" and new_sl >= price_now:
                    continue
                if pos.side == "short" and new_sl <= price_now:
                    continue
            if new_tp is not None and new_tp != pos.take_profit:
                if pos.side == "long" and new_tp <= price_now:
                    continue
                if pos.side == "short" and new_tp >= price_now:
                    continue
            try:
                executor.update_position_sl_tp(db, pos, new_sl, new_tp, note=reason)
                updated += 1
            except Exception:
                pass
        db.flush()
        return {"updated": updated}

    def snapshot(self, db: Session, portfolio_name: str):
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if not portfolio:
            return None
        trades = list(
            db.scalars(select(Trade).where(Trade.portfolio_id == portfolio.id))
        )
        realized = round(sum(t.pnl_usd for t in trades if t.action == "close"), 2)
        snapshot = DailySnapshot(
            portfolio_id=portfolio.id,
            snapshot_date=date.today(),
            equity_usd=portfolio.cash_usd,
            realized_pnl_usd=realized,
            unrealized_pnl_usd=0.0,
            notes="Auto-generated daily snapshot.",
        )
        db.add(snapshot)
        db.flush()
        return snapshot







