import json
from datetime import date

from fastapi import Depends, FastAPI
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from core.db import (
    Base,
    engine,
    get_db,
    ensure_brain_v4_p1_trace_columns,
    ensure_learning_artifact_governance_columns,
    ensure_positions_thesis_columns,
    ensure_trades_brain_cycle_id_column,
    ensure_trades_decision_trace_id_column,
    ensure_trades_risk_metadata_columns,
)

try:
    import core.brain.models  # noqa: F401
    import core.brain.p2_models  # noqa: F401
except ImportError:
    pass
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport
from core.orchestration.cycle import SimulationCycle
from core.reporting.service import DailyReportService
from core.analytics.metrics import compute_metrics

Base.metadata.create_all(bind=engine)
try:
    ensure_trades_brain_cycle_id_column()
    ensure_trades_decision_trace_id_column()
    ensure_brain_v4_p1_trace_columns()
    ensure_positions_thesis_columns()
    ensure_learning_artifact_governance_columns()
    ensure_trades_risk_metadata_columns()
except Exception:
    pass

app = FastAPI(title="Trading Lab Pro API")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cycle/run")
def run_cycle(
    symbols: str = "BTC,ETH,SOL",
    portfolio_name: str = "Paper Portfolio",
    db: Session = Depends(get_db),
):
    result = SimulationCycle().run(
        db, portfolio_name, [s.strip().upper() for s in symbols.split(",") if s.strip()]
    )
    db.commit()
    return result


@app.post("/reports/daily")
def generate_daily_report(
    report_date: date = date.today(), db: Session = Depends(get_db)
):
    report = DailyReportService().generate(db, report_date)
    db.commit()
    return {"headline": report.headline, "date": str(report.report_date)}


@app.get("/brain/v4/latest")
def brain_v4_latest(db: Session = Depends(get_db)):
    from core.brain.persistence import fetch_latest_cycle_summary

    b = fetch_latest_cycle_summary(db)
    return b or {"empty": True}


@app.get("/brain/v4/cycle/{cycle_id}")
def brain_v4_cycle(cycle_id: str, db: Session = Depends(get_db)):
    from core.brain.persistence import fetch_cycle_bundle

    return fetch_cycle_bundle(db, cycle_id)


@app.get("/brain/v4/symbol/{symbol}")
def brain_v4_symbol(symbol: str, limit: int = 20, db: Session = Depends(get_db)):
    from core.brain.persistence import fetch_symbol_recent

    return fetch_symbol_recent(db, symbol, limit=limit)


@app.get("/brain/v4/position/{position_id}")
def brain_v4_position(position_id: int, limit: int = 50, db: Session = Depends(get_db)):
    from core.brain.persistence import fetch_position_reflex

    return {"position_id": position_id, "reflex_events": fetch_position_reflex(db, position_id, limit=limit)}


@app.get("/brain/v4/trace/{decision_trace_id}")
def brain_v4_trace(decision_trace_id: str, db: Session = Depends(get_db)):
    from core.brain.persistence import fetch_by_decision_trace_id

    return fetch_by_decision_trace_id(db, decision_trace_id)


@app.get("/brain/v4/evaluations/latest")
def brain_v4_evaluations_latest(limit: int = 50, db: Session = Depends(get_db)):
    from core.brain.p2_models import DecisionEvaluation

    rows = list(
        db.scalars(
            select(DecisionEvaluation).order_by(desc(DecisionEvaluation.created_at)).limit(max(1, min(limit, 200)))
        )
    )
    return {
        "items": [
            {
                "id": r.id,
                "symbol": r.symbol,
                "evaluation_phase": r.evaluation_phase,
                "trade_id": r.trade_id,
                "position_id": r.position_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@app.get("/brain/v4/thesis/open")
def brain_v4_thesis_open(portfolio_name: str = "Paper Portfolio", db: Session = Depends(get_db)):
    from core.profit.thesis_metrics import count_thesis_states_open, open_positions_thesis_snapshot

    p = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
    if not p:
        return {"empty": True}
    return {
        "counts": count_thesis_states_open(db, p.id),
        "positions": open_positions_thesis_snapshot(db, p.id),
    }


@app.get("/brain/v4/portfolio/latest")
def brain_v4_portfolio_latest(limit: int = 30, db: Session = Depends(get_db)):
    from core.brain.p2_models import PortfolioStateEvent

    rows = list(
        db.scalars(
            select(PortfolioStateEvent).order_by(desc(PortfolioStateEvent.created_at)).limit(max(1, min(limit, 100)))
        )
    )
    return {
        "items": [
            {
                "id": r.id,
                "portfolio_id": r.portfolio_id,
                "state_label": r.state_label,
                "portfolio_stress_score": r.portfolio_stress_score,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@app.get("/observability/rejects/summary")
def observability_rejects_summary(
    limit: int = 400,
    symbols: str | None = None,
):
    """entry_rejected aggregates: good_reject / policy_reject / sizing_reject + dedupe noise counts."""
    from core.observability.reject_summary import build_entry_reject_summary

    sym_set = {s.strip().upper() for s in symbols.split(",") if s.strip()} if symbols else None
    return build_entry_reject_summary(limit=limit, symbols=sym_set)


@app.get("/brain/v4/proposals")
def brain_v4_proposals(
    status: str | None = None,
    limit: int = 80,
    db: Session = Depends(get_db),
):
    from core.brain.proposal_service import list_proposals, proposal_to_dict

    st_list = [s.strip() for s in status.split(",") if s.strip()] if status else None
    rows = list_proposals(db, statuses=st_list, limit=limit)
    return {"items": [proposal_to_dict(p) for p in rows]}


@app.get("/brain/v4/proposals/{public_id}")
def brain_v4_proposal_detail(public_id: str, db: Session = Depends(get_db)):
    from core.brain.proposal_service import get_proposal_by_public_id, proposal_to_dict

    p = get_proposal_by_public_id(db, public_id)
    if not p:
        return {"error": "not_found"}
    try:
        before = json.loads(p.before_values_json or "{}")
        after = json.loads(p.after_values_json or "{}")
        ev = json.loads(p.evidence_snapshot_json or "{}")
        rollback = json.loads(p.rollback_conditions_json or "{}")
    except Exception:
        before, after, ev, rollback = {}, {}, {}, {}
    return {
        "proposal": proposal_to_dict(p),
        "before_values": before,
        "after_values": after,
        "evidence_snapshot": ev,
        "rollback_conditions": rollback,
    }


@app.post("/brain/v4/proposals/from-artifact/{artifact_id}")
def brain_v4_proposal_from_artifact(
    artifact_id: int,
    force: bool = False,
    risk_class: str = "B",
    db: Session = Depends(get_db),
):
    from core.brain.proposal_service import create_proposal_from_learning_artifact, proposal_to_dict

    prop = create_proposal_from_learning_artifact(
        db, artifact_id, risk_class=risk_class, force=force
    )
    if not prop:
        db.rollback()
        return {"error": "could_not_create", "hint": "min sample / missing artifact — try force=true"}
    db.commit()
    return {"proposal": proposal_to_dict(prop)}


@app.post("/brain/v4/proposals/{public_id}/approve")
def brain_v4_proposal_approve(
    public_id: str,
    reviewer_label: str = "",
    notes: str = "",
    db: Session = Depends(get_db),
):
    from core.brain.proposal_service import approve_proposal, proposal_to_dict

    p = approve_proposal(db, public_id, reviewer_label=reviewer_label, notes=notes)
    if not p:
        db.rollback()
        return {"error": "not_approved", "hint": "needs reviewer_label for class B/C"}
    db.commit()
    return {"proposal": proposal_to_dict(p)}


@app.post("/brain/v4/proposals/{public_id}/activate")
def brain_v4_proposal_activate(
    public_id: str,
    rollout_mode: str = "full",
    reviewer_label: str = "",
    db: Session = Depends(get_db),
):
    from core.brain.proposal_service import activate_proposal_runtime_override

    ov = activate_proposal_runtime_override(
        db, public_id, rollout_mode=rollout_mode, reviewer_label=reviewer_label
    )
    if not ov:
        db.rollback()
        return {"error": "not_activated", "hint": "must be approved and non-empty patch"}
    db.commit()
    return {"runtime_override_id": ov.id, "expires_at": ov.expires_at.isoformat() if ov.expires_at else None}


@app.post("/brain/v4/proposals/{public_id}/rollback")
def brain_v4_proposal_rollback(public_id: str, reason: str = "", db: Session = Depends(get_db)):
    from core.brain.proposal_service import rollback_proposal

    ok = rollback_proposal(db, public_id, reason or "api_rollback")
    if not ok:
        db.rollback()
        return {"error": "not_found"}
    db.commit()
    return {"ok": True}


@app.get("/brain/v4/config/versions")
def brain_v4_config_versions(limit: int = 50, db: Session = Depends(get_db)):
    from core.brain.versioning import list_config_versions

    return {"items": list_config_versions(db, limit=limit)}


@app.get("/brain/v4/replay/proposal-compare")
def brain_v4_replay_proposal_compare(
    proposal_public_id: str = "",
    fee_slippage_bps: float = 0,
    drawdown_penalty_weight: float = 0,
    regime: str | None = None,
    cluster: str | None = None,
):
    from core.brain.replay import replay_proposal_vs_baseline

    return replay_proposal_vs_baseline(
        {"pnl_usd": 0, "max_drawdown_pct": 0.01, "trades": 0},
        proposal_public_id=proposal_public_id or None,
        proposed_metrics={"pnl_usd": 0, "max_drawdown_pct": 0.01, "trades": 0},
        regime=regime,
        cluster=cluster,
        fee_slippage_bps=fee_slippage_bps,
        drawdown_penalty_weight=drawdown_penalty_weight,
    )


@app.get("/brain/v4/rollback/history")
def brain_v4_rollback_history(limit: int = 40, db: Session = Depends(get_db)):
    from core.brain.p2_models import RollbackEvent

    rows = list(
        db.scalars(select(RollbackEvent).order_by(desc(RollbackEvent.created_at)).limit(max(1, min(limit, 100))))
    )
    return {
        "items": [
            {
                "id": r.id,
                "proposal_id": r.proposal_id,
                "override_id": r.override_id,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@app.get("/metrics")
def get_metrics(portfolio_id: int | None = None, db: Session = Depends(get_db)):
    """Performance metrics: win rate, profit factor, expectancy, max drawdown."""
    return compute_metrics(db, portfolio_id)


@app.get("/portfolio")
@app.get("/portfolio/summary")
def portfolio_summary(db: Session = Depends(get_db)):
    portfolios = list(db.scalars(select(Portfolio)))
    positions = list(db.scalars(select(Position)))
    trades = list(db.scalars(select(Trade)))
    reports = list(
        db.scalars(select(DailyReport).order_by(DailyReport.report_date.desc()))
    )
    return {
        "portfolios": [
            {"name": p.name, "capital_usd": p.capital_usd, "cash_usd": p.cash_usd}
            for p in portfolios
        ],
        "open_positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "strategy_name": p.strategy_name,
                "entry_price": p.entry_price,
                "quantity": p.quantity,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
            }
            for p in positions if p.is_open
        ],
        "recent_trades": [
            {
                "symbol": t.symbol,
                "action": t.action,
                "side": t.side,
                "strategy_name": t.strategy_name,
                "price": t.price,
                "pnl_usd": t.pnl_usd,
                "created_at": t.created_at.isoformat(),
            }
            for t in trades[-20:]
        ],
        "reports": [
            {"date": str(r.report_date), "headline": r.headline}
            for r in reports[:10]
        ],
    }
