"""Portfolio-wide state snapshot (P2); lightweight heuristic."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.brain.p2_models import PortfolioStateEvent
from core.market_data.client import MarketQuote
from core.portfolio.models import Position

_ROOT = Path(__file__).resolve().parents[2]


def load_portfolio_brain_config() -> dict[str, Any]:
    p = _ROOT / "config" / "portfolio_brain.v1.json"
    if not p.exists():
        return {"enabled": False}
    return json.loads(p.read_text(encoding="utf-8"))


def _exposure_usd(positions: list[Position], quotes: dict[str, MarketQuote]) -> float:
    s = 0.0
    for p in positions:
        q = quotes.get(p.symbol)
        if not q:
            continue
        s += abs(float(q.price) * float(p.quantity or 0))
    return s


def infer_portfolio_state_label(
    *,
    equity_usd: float,
    exposure_usd: float,
    open_positions: list[Position],
    daily_realized_pnl_usd: float,
    cfg: dict[str, Any],
) -> tuple[str, float]:
    stress_cfg = (cfg.get("stress") or {}) if cfg.get("enabled") else {}
    if equity_usd <= 0:
        return "HEALTHY", 0.0
    exp_pct = exposure_usd / equity_usd
    stress = min(1.0, exp_pct / max(float(stress_cfg.get("overexposed_exposure_pct_equity", 0.65) or 0.65), 1e-9))
    label = "HEALTHY"
    if exp_pct >= float(stress_cfg.get("overexposed_exposure_pct_equity", 0.65) or 0.65):
        label = "OVEREXPOSED"
    elif exp_pct >= float(stress_cfg.get("stretched_exposure_pct_equity", 0.45) or 0.45):
        label = "STRETCHED"

    wr_ratio = cfg.get("warning_danger_ratio_for_stretch", 0.35)
    if isinstance(wr_ratio, (int, float)) and open_positions:
        n = len(open_positions)
        w = sum(1 for p in open_positions if (p.thesis_state or "") == "WARNING")
        d = sum(1 for p in open_positions if (p.thesis_state or "") == "DANGER")
        if n and (w + d) / n >= float(wr_ratio) and label == "HEALTHY":
            label = "STRETCHED"

    dd = abs(min(0.0, daily_realized_pnl_usd)) / equity_usd
    if dd >= float(stress_cfg.get("shock_drawdown_pct_day", 0.04) or 0.04):
        label = "SHOCK_RESPONSE"
        stress = max(stress, 0.85)

    return label, round(stress, 4)


def maybe_persist_portfolio_state_tick(
    db: Session,
    *,
    portfolio_id: int,
    equity_usd: float,
    open_positions: list[Position],
    quotes: dict[str, MarketQuote],
    brain_cycle_id: str | None,
    decision_trace_id: str | None,
    daily_realized_pnl_usd: float = 0.0,
) -> None:
    cfg = load_portfolio_brain_config()
    if not cfg.get("enabled") or not cfg.get("persist_state_each_review_tick"):
        return
    exp = _exposure_usd(open_positions, quotes)
    label, stress = infer_portfolio_state_label(
        equity_usd=equity_usd,
        exposure_usd=exp,
        open_positions=open_positions,
        daily_realized_pnl_usd=daily_realized_pnl_usd,
        cfg=cfg,
    )
    payload = {
        "exposure_usd": exp,
        "open_count": len(open_positions),
        "equity_usd": equity_usd,
    }
    db.add(
        PortfolioStateEvent(
            portfolio_id=portfolio_id,
            brain_cycle_id=brain_cycle_id,
            decision_trace_id=decision_trace_id,
            state_label=label,
            portfolio_stress_score=stress,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )
