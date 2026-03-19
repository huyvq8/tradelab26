"""Run one simulation cycle."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import argparse

from core.db import Base, engine, SessionLocal
from core.portfolio.models import Portfolio, Position, Trade, DailySnapshot
from core.journal.models import JournalEntry
from core.reporting.models import DailyReport
from core.orchestration.cycle import SimulationCycle
from core.watchlist import get_watchlist

Base.metadata.create_all(bind=engine)

parser = argparse.ArgumentParser()
parser.add_argument("--symbols", default=None, help="VD: BTC,ETH,SOL. Bo qua thi dung watchlist.")
args = parser.parse_args()

symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else get_watchlist()

with SessionLocal() as db:
    result = SimulationCycle().run(db, "Paper Portfolio", symbols)
    db.commit()
    print(result)
