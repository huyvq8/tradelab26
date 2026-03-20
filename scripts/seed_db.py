"""Create tables (if missing) and seed default portfolio."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from core.db import (
    Base,
    engine,
    SessionLocal,
    ensure_learning_artifact_governance_columns,
    ensure_positions_thesis_columns,
)
from core.portfolio.models import Portfolio
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport

try:
    import core.brain.models  # noqa: F401
    import core.brain.p2_models  # noqa: F401
except ImportError:
    pass
Base.metadata.create_all(bind=engine)
try:
    ensure_positions_thesis_columns()
    ensure_learning_artifact_governance_columns()
except Exception:
    pass

with SessionLocal() as db:
    portfolio = db.query(Portfolio).filter_by(name="Paper Portfolio").first()
    if portfolio is None:
        portfolio = Portfolio(name="Paper Portfolio", capital_usd=1000, cash_usd=1000)
        db.add(portfolio)
        db.commit()
        print("Seeded Paper Portfolio")
    else:
        print("Portfolio already exists")
