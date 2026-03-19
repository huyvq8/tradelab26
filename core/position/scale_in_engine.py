"""
Smart Scale-In Engine (spec document/budget).
Quyet dinh ADD_TO_POSITION / HOLD_EXISTING / REJECT_SCALE_IN theo risk, exposure, chat luong tin hieu,
tuoi position, PnL, regime, gia — thay the gioi han theo count.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.position.scale_in_config import load_scale_in_config
from core.position.scale_in_models import ScaleInAction, ScaleInDecision
from core.portfolio.models import Position, Portfolio
from core.strategies.base import StrategySignal

logger = logging.getLogger(__name__)


def _effective_quality_score(signal: StrategySignal) -> float:
    return (signal.quality_score if signal.quality_score is not None else signal.confidence)


def _signal_freshness_seconds(signal: StrategySignal) -> float:
    if signal.created_at is None:
        return 0.0
    delta = datetime.now(timezone.utc) - signal.created_at
    return delta.total_seconds()


def _position_age_seconds(position: Position) -> float:
    if not position.opened_at:
        return 0.0
    now = datetime.now(timezone.utc)
    if position.opened_at.tzinfo:
        delta = now - position.opened_at
    else:
        from datetime import timezone as tz
        opened = position.opened_at.replace(tzinfo=tz.utc) if position.opened_at else now
        delta = now - opened
    return delta.total_seconds()


def _position_age_hours(position: Position) -> float:
    return _position_age_seconds(position) / 3600.0


def _seconds_since_scale_in(last_scale_in_at: datetime | None) -> float | None:
    """Khoảng thời gian (giây) từ lần scale-in cuối; None nếu chưa từng add."""
    if last_scale_in_at is None:
        return None
    now = datetime.now(timezone.utc)
    ts = last_scale_in_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def _unrealized_pnl(position: Position, mark_price: float) -> float:
    """Tinh unrealized PnL: (mark - entry) * qty (long), (entry - mark) * qty (short)."""
    qty = float(position.quantity or 0)
    entry = float(position.entry_price or 0)
    if entry <= 0 or qty <= 0:
        return 0.0
    if (position.side or "").lower() == "long":
        return (mark_price - entry) * qty
    return (entry - mark_price) * qty


def _symbol_notional(position: Position, current_price: float) -> float:
    return float(position.quantity or 0) * current_price


def _position_risk_usd(position: Position) -> float:
    """Risk USD = qty * abs(entry - stop_loss)."""
    qty = float(position.quantity or 0)
    entry = float(position.entry_price or 0)
    sl = position.stop_loss
    if sl is None:
        return 0.0
    return qty * abs(entry - float(sl))


def _portfolio_risk_usd(open_positions: list[Position]) -> float:
    return sum(_position_risk_usd(p) for p in open_positions)


class ScaleInEngine:
    """Danh gia scale-in: risk/exposure -> trang thai position -> tin hieu -> dieu kien add -> tinh size."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_scale_in_config()

    def evaluate(
        self,
        signal: StrategySignal,
        position: Position,
        current_price: float,
        portfolio: Portfolio,
        open_positions: list[Position],
        close_signal_active: bool = False,
        reduce_only_pending: bool = False,
        last_scale_in_at: datetime | None = None,
    ) -> ScaleInDecision:
        """
        Thu tu: kiem tra bat -> trang thai position -> tin hieu -> gia -> risk/exposure -> tinh add size.
        Tra ve ADD_TO_POSITION (kem add_qty, add_notional) hoac HOLD_EXISTING / REJECT_SCALE_IN (kem reason).
        """
        si = (self.config.get("scale_in") or {})
        si_risk = (self.config.get("scale_in_risk") or {})
        si_price = (self.config.get("scale_in_price_rules") or {})
        si_pos = (self.config.get("scale_in_position_rules") or {})
        si_strat = (self.config.get("scale_in_strategy_rules") or {})
        si_sizing = (self.config.get("scale_in_sizing") or {})

        if not si.get("enabled", False):
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "scale_in_disabled")
        if not si.get("allow_same_symbol_scale_in", True):
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "allow_same_symbol_scale_in=false")

        pos_side = (position.side or "").lower()
        sig_side = (signal.side or "").lower()
        if pos_side != sig_side:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "opposite_side_signal")
        if si.get("allow_only_same_side", True) and pos_side != sig_side:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "allow_only_same_side")

        # max_scale_in_times <= 0: không giới hạn số lần add theo count — chỉ risk/exposure,
        # cooldown_between_scale_ins, vùng giá, điểm tin hiệu, v.v.
        max_scale_in = int(si.get("max_scale_in_times", 1) or 0)
        scale_in_count = int(getattr(position, "scale_in_count", 0) or 0)
        if max_scale_in > 0 and scale_in_count >= max_scale_in:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "max_scale_in_reached")

        cooldown_sec = float(si.get("cooldown_between_scale_ins_seconds", 0) or 0)
        if cooldown_sec > 0:
            since_si = _seconds_since_scale_in(last_scale_in_at)
            if since_si is not None and since_si < cooldown_sec:
                return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "scale_in_cooldown_active")

        min_age = float(si.get("min_position_age_seconds", 120) or 120)
        if _position_age_seconds(position) < min_age:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "position_too_new")

        max_age_h = float(si.get("max_position_age_hours", 48) or 48)
        if _position_age_hours(position) > max_age_h:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "position_too_old")

        if si_pos.get("reject_if_reduce_only_pending", True) and reduce_only_pending:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "reduce_only_pending")
        if si_pos.get("reject_if_close_signal_active", True) and close_signal_active:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "close_signal_active")

        freshness = float(si.get("require_signal_freshness_seconds", 180) or 180)
        if freshness > 0 and _signal_freshness_seconds(signal) > freshness:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "stale_signal")

        min_score = float(si.get("min_signal_score_to_scale_in", 0.72) or 0.72)
        quality = _effective_quality_score(signal)
        if quality < min_score:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "signal_score_too_low")
        min_conf = float(si.get("min_confidence_to_scale_in", 0.70) or 0.70)
        if signal.confidence < min_conf:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "signal_confidence_too_low")

        allowed_regimes = si_strat.get("allowed_regimes") or []
        blocked_regimes = si_strat.get("blocked_regimes") or []
        regime = (signal.regime or "").strip().lower()
        if blocked_regimes and regime in [r.lower() for r in blocked_regimes]:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "regime_blocked")
        if allowed_regimes and regime not in [r.lower() for r in allowed_regimes]:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "regime_not_allowed")

        allow_neg_pnl = si.get("allow_scale_in_when_pnl_negative", False)
        unrealized = _unrealized_pnl(position, current_price)
        if not allow_neg_pnl and unrealized < 0:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "negative_pnl_scale_in_blocked")

        # Price rules: entry zone, min add distance
        entry_zone_pct = float(si_price.get("entry_zone_pct", 0.004) or 0.004)
        zone = signal.entry_price * entry_zone_pct
        zone_low = signal.entry_price - zone
        zone_high = signal.entry_price + zone
        if not (zone_low <= current_price <= zone_high):
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "price_outside_entry_zone")

        min_dist_pct = float(si_price.get("min_add_distance_pct", 0.003) or 0.003)
        entry_pos = float(position.entry_price or 0)
        if entry_pos > 0 and min_dist_pct > 0:
            dist_pct = abs(current_price - entry_pos) / entry_pos
            if dist_pct < min_dist_pct:
                return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "price_too_close_to_position_entry")

        # Strategy: same/different
        add_only_diff = si_strat.get("add_only_different_strategy", False)
        pos_strategy = (position.strategy_name or "").strip()
        sig_strategy = (signal.strategy_name or "").strip()
        if add_only_diff and pos_strategy and sig_strategy == pos_strategy:
            improve_by = float(si_strat.get("allow_same_strategy_if_score_improves_by", 0) or 0)
            if improve_by <= 0:
                return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "same_strategy_not_allowed")
            pos_conf = float(getattr(position, "confidence", 0) or 0)
            if quality <= pos_conf + improve_by:
                return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "same_strategy_score_not_improved")

        # SL too close
        if si_pos.get("reject_if_stoploss_too_close", True) and position.stop_loss is not None:
            min_sl_dist_pct = float(si_pos.get("min_remaining_distance_to_sl_pct", 0.004) or 0.004)
            sl = float(position.stop_loss)
            dist_to_sl = abs(current_price - sl) / max(current_price, 1e-9)
            if dist_to_sl < min_sl_dist_pct:
                return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "stoploss_too_close")

        equity = float(portfolio.capital_usd or 1000.0)
        symbol_notional = _symbol_notional(position, current_price)
        symbol_risk = _position_risk_usd(position)
        portfolio_risk = _portfolio_risk_usd(open_positions)

        max_sym_exp_pct = float(si_risk.get("max_symbol_exposure_pct_of_equity", 0.15) or 0.15)
        max_sym_risk_pct = float(si_risk.get("max_symbol_risk_pct_of_equity", 0.01) or 0.01)
        max_port_risk_pct = float(si_risk.get("max_portfolio_risk_pct_of_equity", 0.04) or 0.04)
        max_total_notional = float(si_risk.get("max_total_position_notional_usd", 1000) or 1000)
        min_add_usd = float(si_risk.get("min_add_notional_usd", 25) or 25)
        max_add_usd = float(si_risk.get("max_add_notional_usd", 300) or 300)
        max_add_pct_existing = float(si_risk.get("max_add_notional_pct_of_existing", 1.0) or 1.0)

        # Compute add size (risk-based)
        stop_distance_pct = abs(current_price - signal.stop_loss) / max(current_price, 1e-9)
        if stop_distance_pct <= 0:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "invalid_stop_distance")

        room_sym_risk_usd = max(0, equity * max_sym_risk_pct - symbol_risk)
        room_port_risk_usd = max(0, equity * max_port_risk_pct - portfolio_risk)
        usable_risk = min(room_sym_risk_usd, room_port_risk_usd)
        raw_add_notional = usable_risk / stop_distance_pct if stop_distance_pct > 0 else 0

        room_notional = max(0, max_total_notional - symbol_notional)
        raw_add_notional = min(raw_add_notional, room_notional)
        raw_add_notional = min(raw_add_notional, max_add_usd)
        raw_add_notional = min(raw_add_notional, symbol_notional * max_add_pct_existing)
        raw_add_notional = max(raw_add_notional, min_add_usd)

        ladder = si_sizing.get("ladder_multipliers") or [1.0, 0.75, 0.5]
        idx = min(scale_in_count, len(ladder) - 1)
        mult = float(ladder[idx]) if idx >= 0 else 1.0
        if si_sizing.get("score_weighted", True):
            mult *= (0.7 + 0.3 * quality)
        add_notional = round(raw_add_notional * mult, 2)
        if add_notional < min_add_usd:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "add_size_below_min")

        # Check after-add limits
        new_notional = symbol_notional + add_notional
        if new_notional > max_total_notional:
            add_notional = max(min_add_usd, max_total_notional - symbol_notional)
        if add_notional < min_add_usd:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "after_add_exceeds_total_notional_limit")

        if new_notional > equity * max_sym_exp_pct:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "after_add_exceeds_symbol_exposure")

        new_risk_sym = symbol_risk + add_notional * stop_distance_pct
        if new_risk_sym > equity * max_sym_risk_pct:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "after_add_exceeds_symbol_risk")

        new_port_risk = portfolio_risk - symbol_risk + new_risk_sym
        if new_port_risk > equity * max_port_risk_pct:
            return ScaleInDecision(ScaleInAction.REJECT_SCALE_IN, "after_add_exceeds_portfolio_risk")

        add_qty = add_notional / current_price if current_price > 0 else 0
        new_total_qty = float(position.quantity or 0) + add_qty
        new_total_notional = new_total_qty * current_price
        expected_avg_entry = (float(position.entry_price or 0) * float(position.quantity or 0) + current_price * add_qty) / new_total_qty if new_total_qty > 0 else current_price

        risk_snapshot = {
            "symbol_notional_before": round(symbol_notional, 2),
            "symbol_risk_before": round(symbol_risk, 2),
            "portfolio_risk_before": round(portfolio_risk, 2),
            "add_notional": round(add_notional, 2),
            "new_total_notional": round(new_total_notional, 2),
        }

        return ScaleInDecision(
            action=ScaleInAction.ADD_TO_POSITION,
            reason="smart_scale_in_passed",
            add_qty=add_qty,
            add_notional=add_notional,
            expected_avg_entry=expected_avg_entry,
            new_total_qty=new_total_qty,
            new_total_notional=new_total_notional,
            risk_snapshot=risk_snapshot,
        )
