from datetime import date

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import Base, engine, get_db
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport
from core.orchestration.cycle import SimulationCycle
from core.reporting.service import DailyReportService
from core.analytics.metrics import compute_metrics

Base.metadata.create_all(bind=engine)

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
