from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta

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
from core.risk.daily_r import sum_daily_realized_r_from_trades
from core.profit.volatility_guard import check_volatility_guard, load_profit_config
from core.profit.position_sizer import apply_dynamic_sizing
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
from core.risk.quick_sizing import is_likely_below_min_position_usd
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

# Cache token features + profile + routing 120s — log chỉ khi refresh (document/request: giảm TOKEN_FEATURES_BUILT mỗi 10s).
_TOKEN_INTEL_CACHE: dict[str, tuple[float, dict, "TokenProfile", object]] = {}
_TOKEN_INTEL_TTL = 120.0


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
    if combo_cfg.get("enabled", True):
        cm = get_combo_multiplier(
            combo_mults,
            signal.strategy_name,
            symbol,
            current_regime or getattr(signal, "regime", None),
            side=getattr(signal, "side", None),
        )
        if cm <= 0:
            rec = {
                "symbol": signal.symbol,
                "strategy_name": signal.strategy_name,
                "reason": "Combo strategy+symbol(+regime+side) underperforming (rolling PF/WR); entry blocked.",
                "reason_code": "COMBO_BLOCKED_EDGE",
                "meta": {
                    "combo_multiplier": 0.0,
                    "regime": current_regime or getattr(signal, "regime", None),
                    "side": getattr(signal, "side", None),
                    "native_signal": _native_signal_log_slice(signal),
                },
            }
            log_decision(
                "entry_rejected",
                rec["meta"],
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
            )
        if bucket == "fast":
            cap_nf = cs_mgr.max_notional_usd_fast()
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
                max_concurrent_in_scope=cs_mgr.max_concurrent_fast(),
                max_daily_loss_pct_in_scope=cs_mgr.max_daily_loss_fast_pct(),
                consecutive_loss_in_scope=consecutive_loss_fast,
                max_consecutive_loss_for_scope=cs_mgr.max_consecutive_loss_fast(),
                max_position_usd_cap=cap_nf if cap_nf > 0 else None,
            )
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
            max_concurrent_in_scope=settings.max_concurrent_trades,
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
    ) -> dict:
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
        if portfolio is None:
            portfolio = Portfolio(name=portfolio_name)
            db.add(portfolio)
            db.flush()
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
        single_strategy = get_effective_single_strategy_mode()
        strategies_to_use = (
            [s for s in self.strategies if s.name == single_strategy]
            if single_strategy
            else self.strategies
        )
        all_strategy_names = [s.name for s in self.strategies]
        class_cfg = load_classification_config()
        # Phase 3 v6: strategy weights và allocation (một lần mỗi cycle)
        profit_cfg_cycle = load_profit_config()
        sw_cfg = profit_cfg_cycle.get("strategy_weight") or {}
        strategy_weights = compute_strategy_weights(
            db,
            portfolio_id=portfolio.id,
            lookback_days=int(sw_cfg.get("lookback_days", 30)),
            min_sample=int(sw_cfg.get("min_sample", 5)),
            weight_min=float(sw_cfg.get("weight_min", 0.25)),
            weight_max=float(sw_cfg.get("weight_max", 1.5)),
        )
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

        for symbol, quote in quotes.items():
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
                logging.getLogger(__name__).debug(
                    "SIGNAL_CANDIDATE symbol=%s strategy=%s side=%s entry=%s price_now=%s",
                    symbol, signal.strategy_name, signal.side, signal.entry_price, quote.price,
                )
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
                # Số lệnh đang mở cho symbol này
                opens_for_symbol = [p for p in open_positions if p.symbol == symbol]
                count_open_for_symbol = len(opens_for_symbol)
                existing_same_side = [p for p in opens_for_symbol if (p.side or "").lower() == (signal.side or "").lower()]
                is_binance = getattr(self.execution, "__class__", None) and getattr(self.execution.__class__, "__name__", "") == "BinanceFuturesExecutor"
                scale_in_cfg = load_scale_in_config()
                scale_in_enabled = (scale_in_cfg.get("scale_in") or {}).get("enabled", False)
                # Smart Scale-In (document/budget): 1 position cung chieu -> danh gia scale-in thay vi skip theo count
                if len(existing_same_side) == 1 and is_binance and scale_in_enabled:
                    position = existing_same_side[0]
                    si_flat = scale_in_cfg.get("scale_in") or {}
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
                signals_fired.append({
                    "symbol": signal.symbol,
                    "strategy_name": signal.strategy_name,
                    "side": signal.side,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
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
                ):
                    logging.getLogger(__name__).debug(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=pre_size_below_min cap_quick=%s",
                        symbol, signal.strategy_name, round(cap_quick, 2),
                    )
                    rejected_signals.append({
                        "symbol": symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": "Pre-check: estimated position size below minimum.",
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
                )
                if not decision.approved:
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=%s available_cash=%s open_positions=%s",
                        signal.symbol, signal.strategy_name, decision.reason, available_cash, len(open_positions),
                    )
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": decision.reason,
                    })
                    continue
                # Phase 1 v6: áp dụng giảm size theo volatility guard
                size_after_vol = decision.size_usd
                if vol_result.reduce_size_pct > 0:
                    size_after_vol = round(decision.size_usd * (1.0 - vol_result.reduce_size_pct), 2)
                    if size_after_vol < 25:
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
                if sizing_cfg.get("enabled", True):
                    final_size_usd = apply_dynamic_sizing(
                        size_after_vol,
                        signal.confidence,
                        regime,
                        profit_cfg,
                        strategy_weight=strategy_weight,
                        portfolio_heat_mult=allocation_result.portfolio_heat_mult,
                    )
                else:
                    final_size_usd = size_after_vol
                if entry_combo_mult < 1.0:
                    final_size_usd = round(float(final_size_usd) * float(entry_combo_mult), 2)
                if final_size_usd < 25:
                    logging.getLogger(__name__).info(
                        "REJECTED_SIGNAL symbol=%s strategy=%s reason=Position size too small after dynamic sizing. final_size_usd=%s",
                        signal.symbol, signal.strategy_name, round(final_size_usd, 2),
                    )
                    rejected_signals.append({
                        "symbol": signal.symbol,
                        "strategy_name": signal.strategy_name,
                        "reason": "Position size too small after dynamic sizing.",
                        "reason_code": "SIZE_TOO_SMALL_POST_SIZING",
                        "meta": {"combo_mult": entry_combo_mult},
                    })
                    continue
                final_size_usd = min(final_size_usd, available_cash)
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
                log_decision(
                    "entry_opened",
                    {
                        "size_usd": round(final_size_usd, 2),
                        "combo_mult": entry_combo_mult,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                    },
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
                                    opens_for_symbol = [p for p in open_positions if p.symbol == symbol]
                                    existing_same_side_short = [p for p in opens_for_symbol if (p.side or "").lower() == "short"]
                                    if len(existing_same_side_short) == 1 and is_binance and scale_in_enabled:
                                        position = existing_same_side_short[0]
                                        si_flat_short = scale_in_cfg.get("scale_in") or {}
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
                                        signals_fired.append({
                                            "symbol": signal.symbol,
                                            "strategy_name": signal.strategy_name,
                                            "side": signal.side,
                                            "entry_price": signal.entry_price,
                                            "stop_loss": signal.stop_loss,
                                            "take_profit": signal.take_profit,
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
                                            ):
                                                logging.getLogger(__name__).debug(
                                                    "REJECTED_SIGNAL symbol=%s strategy=%s reason=pre_size_below_min (short)",
                                                    symbol, signal.strategy_name,
                                                )
                                                rejected_signals.append({
                                                    "symbol": symbol,
                                                    "strategy_name": signal.strategy_name,
                                                    "reason": "Pre-check: estimated position size below minimum.",
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
                                            )
                                            if decision.approved:
                                                size_after_vol = decision.size_usd
                                                if vol_result.reduce_size_pct > 0:
                                                    size_after_vol = round(decision.size_usd * (1.0 - vol_result.reduce_size_pct), 2)
                                                if size_after_vol >= 25:
                                                    allocation_result = compute_allocation_mult(
                                                        open_positions_for_allocation,
                                                        max_portfolio_heat_r=float(alloc_cfg.get("max_portfolio_heat_r", 4.0)),
                                                        same_regime_reduce=float(alloc_cfg.get("same_regime_reduce", 0.8)),
                                                        same_strategy_reduce=float(alloc_cfg.get("same_strategy_reduce", 0.75)),
                                                        current_regime=regime,
                                                        current_strategy=signal.strategy_name,
                                                    )
                                                    strategy_weight = get_strategy_weight(strategy_weights, signal.strategy_name)
                                                    sizing_cfg = (profit_cfg.get("sizing") or {})
                                                    if sizing_cfg.get("enabled", True):
                                                        final_size_usd = apply_dynamic_sizing(
                                                            size_after_vol, signal.confidence, regime, profit_cfg,
                                                            strategy_weight=strategy_weight,
                                                            portfolio_heat_mult=allocation_result.portfolio_heat_mult,
                                                        )
                                                    else:
                                                        final_size_usd = size_after_vol
                                                    if entry_combo_mult_s < 1.0:
                                                        final_size_usd = round(float(final_size_usd) * float(entry_combo_mult_s), 2)
                                                    if final_size_usd >= 25:
                                                        final_size_usd = min(final_size_usd, available_cash)
                                                        position = self.execution.open_position(db, portfolio.id, signal, final_size_usd)
                                                        if not hasattr(self.execution, "get_available_balance_usd"):
                                                            portfolio.cash_usd -= final_size_usd
                                                        open_trade = db.scalar(
                                                            select(Trade).where(
                                                                Trade.position_id == position.id,
                                                                Trade.action == "open",
                                                            )
                                                        )
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
                                                        log_decision(
                                                            "entry_opened",
                                                            {
                                                                "size_usd": round(final_size_usd, 2),
                                                                "combo_mult": entry_combo_mult_s,
                                                                "setup": short_sig.setup_type,
                                                                "capital_bucket": normalize_bucket(getattr(signal, "capital_bucket", None)),
                                                            },
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
                                                        })
                                                        if normalize_bucket(getattr(signal, "capital_bucket", None)) == "fast":
                                                            open_fast += 1
                                                        else:
                                                            open_core += 1
                                                        open_positions.append(position)
                                                        open_positions_for_allocation.append({"strategy_name": signal.strategy_name})
                                        else:
                                            rejected_signals.append({
                                                "symbol": signal.symbol,
                                                "strategy_name": signal.strategy_name,
                                                "reason": vol_result.block_reason,
                                            })
        db.flush()
        return {
            "evaluated": evaluated,
            "opened": opened,
            "symbols": len(symbols),
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
            risk_usd = None
            if pos.stop_loss is not None and pos.quantity:
                risk_usd = abs(float(pos.entry_price) - float(pos.stop_loss)) * float(pos.quantity)
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

    def review_positions_and_act(self, db: Session, portfolio_name: str) -> list[dict]:
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
        max_hours = max(0.0, float(getattr(settings, "max_hold_hours", 0) or 0))
        close_if_risk_off = bool(getattr(settings, "proactive_close_if_risk_off", False))
        now = datetime.utcnow()
        actions = []
        # Binance chỉ có 1 bộ TP/SL cho mỗi (symbol, side); tránh gọi update_position_sl_tp nhiều lần cùng cycle → race/-4130
        updated_symbol_side: set[tuple[str, str]] = set()

        _class_cfg_review = load_classification_config()
        cs_rev = load_capital_split_config()
        max_min_fast = int(cs_rev.get("max_hold_minutes_fast", 0) or 0) if cs_rev.get("enabled") else 0
        for pos in open_positions:
            if pos.symbol not in quotes:
                actions.append({"symbol": pos.symbol, "side": pos.side, "action": "HOLD", "reason": "không có giá"})
                continue
            price_now = quotes[pos.symbol].price
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
                and max_min_fast > 0
                and normalize_bucket(getattr(pos, "capital_bucket", None)) == "fast"
            ):
                age_min = (now - pos.opened_at).total_seconds() / 60.0 if getattr(pos, "opened_at", None) else 0
                if age_min >= max_min_fast:
                    note_close = f"fast capital time-stop ({age_min:.0f}m >= {max_min_fast}m)"
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
