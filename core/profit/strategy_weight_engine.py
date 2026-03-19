"""
Phase 3 v6: Strategy weight engine — weight 0.25–1.5 từ rolling PF, win rate.
Combo multipliers: (strategy, symbol) và tùy chọn (strategy, symbol, entry_regime).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Position, Trade


def compute_strategy_weights(
    db: Session,
    portfolio_id: int | None = None,
    lookback_days: int = 30,
    min_sample: int = 5,
    weight_min: float = 0.25,
    weight_max: float = 1.5,
) -> dict[str, float]:
    """
    Với mỗi strategy: lấy rolling lệnh đóng (lookback_days), tính win_rate và profit_factor.
    weight = weight_min + (win_rate * 0.4 + min(PF, 2) / 2 * 0.6) * (weight_max - weight_min), clamp.
    Nếu sample < min_sample -> weight = 1.0 (trung tính).
    """
    since = datetime.utcnow() - timedelta(days=lookback_days)
    q = select(Trade).where(
        Trade.action == "close",
        Trade.created_at >= since,
    )
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    trades = list(db.scalars(q))
    if not trades:
        return {}

    by_strategy: dict[str, list[float]] = {}
    for t in trades:
        s = (t.strategy_name or "unknown").strip() or "unknown"
        pnl = float(t.pnl_usd or 0)
        by_strategy.setdefault(s, []).append(pnl)

    out = {}
    for strategy, pnls in by_strategy.items():
        if len(pnls) < min_sample:
            out[strategy] = 1.0
            continue
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
        pf_capped = min(pf, 2.0)
        raw = win_rate * 0.4 + (pf_capped / 2.0) * 0.6
        weight = weight_min + raw * (weight_max - weight_min)
        weight = max(weight_min, min(weight_max, weight))
        out[strategy] = round(weight, 2)
    return out


def get_strategy_weight(weights: dict[str, float], strategy_name: str) -> float:
    """Lấy weight cho strategy; không có thì 1.0."""
    return float(weights.get((strategy_name or "").strip() or "unknown", 1.0))


def _combo_key_pair(strategy_name: str, symbol: str) -> str:
    s = (strategy_name or "").strip() or "unknown"
    sym = (symbol or "").strip().upper() or "?"
    return f"{s}|{sym}"


def _combo_key_triple(strategy_name: str, symbol: str, regime: str) -> str:
    s = (strategy_name or "").strip() or "unknown"
    sym = (symbol or "").strip().upper() or "?"
    r = (regime or "").strip() or "unknown"
    return f"{s}|{sym}|{r}"


def _combo_key_quad(strategy_name: str, symbol: str, regime: str, side: str) -> str:
    s = (strategy_name or "").strip() or "unknown"
    sym = (symbol or "").strip().upper() or "?"
    r = (regime or "").strip() or "unknown"
    sd = (side or "").strip().lower() or "unknown"
    return f"{s}|{sym}|{r}|{sd}"


def _combo_mult_from_pnls(
    pnls: list[float],
    *,
    block_pf_below: float,
    block_wr_below: float,
    soft_pf_below: float,
    soft_mult: float,
) -> float:
    n = len(pnls)
    if n == 0:
        return 1.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)
    if pf < block_pf_below and win_rate < block_wr_below:
        return 0.0
    if pf < soft_pf_below:
        return float(soft_mult)
    return 1.0


def compute_combo_multipliers(
    db: Session,
    portfolio_id: int | None = None,
    lookback_days: int = 60,
    min_sample: int = 15,
    min_sample_regime: int | None = None,
    include_regime_in_key: bool = True,
    include_side_in_key: bool = False,
    min_sample_quad: int | None = None,
    block_pf_below: float = 0.92,
    block_wr_below: float = 0.36,
    soft_pf_below: float = 1.0,
    soft_mult: float = 0.5,
) -> dict[str, float]:
    """
    Rolling closed trades → multiplier per `strategy|symbol` và (nếu bật) `strategy|symbol|entry_regime`
    và (nếu bật) `strategy|symbol|entry_regime|side`. Side/regime keys chỉ áp dụng khi đủ min_sample_*.
    entry_regime lấy từ Position tại lúc mở (cột entry_regime); side từ Position.side.
    """
    since = datetime.utcnow() - timedelta(days=lookback_days)
    q = select(Trade).where(Trade.action == "close", Trade.created_at >= since)
    if portfolio_id is not None:
        q = q.where(Trade.portfolio_id == portfolio_id)
    trades = list(db.scalars(q))
    if not trades:
        return {}

    min_reg = min_sample_regime if min_sample_regime is not None else max(8, min_sample - 3)
    min_q = min_sample_quad if min_sample_quad is not None else max(20, min_sample + 5)

    pids = [t.position_id for t in trades if t.position_id]
    pos_map: dict[int, Position] = {}
    if pids:
        uniq = list({int(x) for x in pids})
        for pos in db.scalars(select(Position).where(Position.id.in_(uniq))):
            pos_map[pos.id] = pos

    by_pair: dict[str, list[float]] = {}
    by_trip: dict[str, list[float]] = {}
    by_quad: dict[str, list[float]] = {}
    for t in trades:
        sym = (t.symbol or "").strip().upper() or "?"
        s = (t.strategy_name or "unknown").strip() or "unknown"
        pnl = float(t.pnl_usd or 0)
        kp = _combo_key_pair(s, sym)
        by_pair.setdefault(kp, []).append(pnl)
        if include_regime_in_key and t.position_id and t.position_id in pos_map:
            pos = pos_map[t.position_id]
            reg = (getattr(pos, "entry_regime", None) or "unknown").strip() or "unknown"
            kt = _combo_key_triple(s, sym, reg)
            by_trip.setdefault(kt, []).append(pnl)
            if include_side_in_key:
                side = (getattr(pos, "side", None) or getattr(t, "side", None) or "unknown").strip().lower() or "unknown"
                kq = _combo_key_quad(s, sym, reg, side)
                by_quad.setdefault(kq, []).append(pnl)

    out: dict[str, float] = {}
    kw = dict(
        block_pf_below=block_pf_below,
        block_wr_below=block_wr_below,
        soft_pf_below=soft_pf_below,
        soft_mult=soft_mult,
    )

    for key, pnls in by_pair.items():
        if len(pnls) < min_sample:
            out[key] = 1.0
        else:
            out[key] = _combo_mult_from_pnls(pnls, **kw)

    if include_regime_in_key:
        for key, pnls in by_trip.items():
            if len(pnls) < min_reg:
                continue
            out[key] = _combo_mult_from_pnls(pnls, **kw)

    if include_regime_in_key and include_side_in_key:
        for key, pnls in by_quad.items():
            if len(pnls) < min_q:
                continue
            out[key] = _combo_mult_from_pnls(pnls, **kw)

    return out


def get_combo_multiplier(
    multipliers: dict[str, float],
    strategy_name: str,
    symbol: str,
    current_regime: str | None = None,
    side: str | None = None,
) -> float:
    """
    Ưu tiên khóa 4 phần (strategy|symbol|regime|side) nếu có; sau đó 3 phần; unknown regime; cặp 2 phần.
    `current_regime` = regime hiện tại của symbol (khớp entry_regime đã lưu khi mở lệnh).
    """
    s = (strategy_name or "").strip() or "unknown"
    sym = (symbol or "").strip().upper() or "?"
    reg = (current_regime or "").strip() or "unknown"
    sd = (side or "").strip().lower() or "unknown"

    k4 = _combo_key_quad(s, sym, reg, sd)
    if k4 in multipliers:
        return float(multipliers[k4])
    k4u = _combo_key_quad(s, sym, reg, "unknown")
    if k4u in multipliers:
        return float(multipliers[k4u])

    k3 = _combo_key_triple(s, sym, reg)
    if k3 in multipliers:
        return float(multipliers[k3])
    k3u = _combo_key_triple(s, sym, "unknown")
    if k3u in multipliers:
        return float(multipliers[k3u])
    k2 = _combo_key_pair(s, sym)
    if k2 in multipliers:
        return float(multipliers[k2])
    return 1.0


# Back-compat alias
def _combo_key(strategy_name: str, symbol: str) -> str:
    return _combo_key_pair(strategy_name, symbol)
