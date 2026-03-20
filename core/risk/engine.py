from __future__ import annotations

from dataclasses import dataclass

from core.config import (
    settings,
    get_effective_kill_switch_enabled,
    get_effective_kill_switch_r_threshold,
    get_effective_max_consecutive_loss_stop,
)
from core.strategies.base import StrategySignal


def effective_risk_capital_usd(portfolio_capital_usd: float | None) -> float:
    """
    Vốn dùng cho % risk / giới hạn lỗ USD trong ngày — đồng nhất với scale-in (portfolio.capital_usd).
    Nếu DB chưa set capital_usd (0/None) → fallback settings.default_capital_usd.
    """
    c = float(portfolio_capital_usd or 0)
    if c > 0:
        return c
    return float(getattr(settings, "default_capital_usd", 1000.0) or 1000.0)


@dataclass
class RiskDecision:
    approved: bool
    size_usd: float
    reason: str


class RiskEngine:
    def assess(
        self,
        signal: StrategySignal,
        available_cash: float,
        open_positions: int,
        daily_realized_pnl: float,
        daily_realized_r: float | None = None,
        consecutive_loss_count: int = 0,
        override_risk_pct: float | None = None,
        capital_usd_for_risk: float | None = None,
        max_concurrent_trades_override: int | None = None,
        *,
        capital_scope: str | None = None,
        open_positions_in_scope: int | None = None,
        daily_realized_pnl_in_scope: float | None = None,
        risk_capital_for_scope: float | None = None,
        max_concurrent_in_scope: int | None = None,
        max_daily_loss_pct_in_scope: float | None = None,
        consecutive_loss_in_scope: int | None = None,
        max_consecutive_loss_for_scope: int | None = None,
        max_position_usd_cap: float | None = None,
    ) -> RiskDecision:
        """
        capital_scope None = hành vi cũ (một pool).
        capital_scope 'core' | 'fast' = giới hạn concurrent/daily/consec theo bucket + risk trên slice vốn.
        """
        cap_legacy = effective_risk_capital_usd(capital_usd_for_risk)

        if get_effective_kill_switch_enabled() and daily_realized_r is not None:
            threshold = get_effective_kill_switch_r_threshold()
            if daily_realized_r <= -threshold:
                return RiskDecision(
                    False,
                    0.0,
                    f"Kill switch v5: daily R = {daily_realized_r:.1f} <= -{threshold} (dung trade den het ngay).",
                )

        max_c_global = (
            int(max_concurrent_trades_override)
            if max_concurrent_trades_override is not None
            else settings.max_concurrent_trades
        )
        if capital_scope in ("core", "fast"):
            oscope = int(open_positions_in_scope if open_positions_in_scope is not None else open_positions)
            base_mc = max_concurrent_in_scope if max_concurrent_in_scope is not None else max_c_global
            max_c = int(base_mc)
            if oscope >= max_c:
                return RiskDecision(False, 0.0, "Maximum concurrent trades reached (bucket scope).")

            cap = effective_risk_capital_usd(
                risk_capital_for_scope if risk_capital_for_scope is not None else capital_usd_for_risk
            )
            daily_scoped = (
                daily_realized_pnl_in_scope
                if daily_realized_pnl_in_scope is not None
                else daily_realized_pnl
            )
            mdl = float(
                max_daily_loss_pct_in_scope
                if max_daily_loss_pct_in_scope is not None
                else settings.max_daily_loss_pct
            )
            if daily_scoped <= -(cap * mdl):
                return RiskDecision(False, 0.0, "Daily loss limit reached (bucket scope).")

            cl = int(consecutive_loss_in_scope if consecutive_loss_in_scope is not None else consecutive_loss_count)
            mcl = max_consecutive_loss_for_scope
            if mcl is None:
                mcl = get_effective_max_consecutive_loss_stop()
            if int(mcl) >= 1 and cl >= int(mcl):
                return RiskDecision(
                    False,
                    0.0,
                    f"v5: {cl} lenh thua lien tiep >= {mcl} (bucket scope).",
                )
        else:
            if open_positions >= max_c_global:
                return RiskDecision(False, 0.0, "Maximum concurrent trades reached.")
            if daily_realized_pnl <= -(cap_legacy * settings.max_daily_loss_pct):
                return RiskDecision(False, 0.0, "Daily loss limit reached.")
            max_consec = get_effective_max_consecutive_loss_stop()
            if max_consec >= 1 and consecutive_loss_count >= max_consec:
                return RiskDecision(
                    False,
                    0.0,
                    f"v5: {consecutive_loss_count} lenh thua lien tiep >= {max_consec} (dung mo lenh moi).",
                )
            cap = cap_legacy

        stop_distance = abs(signal.entry_price - signal.stop_loss) / max(
            signal.entry_price, 1e-9
        )
        if stop_distance <= 0:
            return RiskDecision(False, 0.0, "Invalid stop distance.")
        risk_pct = (
            override_risk_pct
            if override_risk_pct is not None and 0 < override_risk_pct < 1
            else settings.default_risk_pct
        )
        risk_dollars = cap * risk_pct
        size_usd = min(available_cash, risk_dollars / stop_distance)
        if max_position_usd_cap is not None and float(max_position_usd_cap) > 0:
            size_usd = min(size_usd, float(max_position_usd_cap))
        if size_usd < 25:
            return RiskDecision(
                False, 0.0, "Position size too small after risk adjustment."
            )
        return RiskDecision(True, round(size_usd, 2), "Approved by risk policy.")
