"""Generate end-of-day report for a given date."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import argparse
from datetime import date, datetime

from core.db import Base, engine, SessionLocal
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport
from core.reporting.service import DailyReportService

Base.metadata.create_all(bind=engine)

parser = argparse.ArgumentParser()
parser.add_argument("--date", default=None)
args = parser.parse_args()

target_date = (
    datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
)

with SessionLocal() as db:
    report = DailyReportService().generate(db, target_date)
    db.commit()
    print(report.headline)
