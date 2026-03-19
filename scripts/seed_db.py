"""Create tables (if missing) and seed default portfolio."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from core.db import Base, engine, SessionLocal
from core.portfolio.models import Portfolio
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport

Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    portfolio = db.query(Portfolio).filter_by(name="Paper Portfolio").first()
    if portfolio is None:
        portfolio = Portfolio(name="Paper Portfolio", capital_usd=1000, cash_usd=1000)
        db.add(portfolio)
        db.commit()
        print("Seeded Paper Portfolio")
    else:
        print("Portfolio already exists")
