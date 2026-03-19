"""
Run daily reflection (v4): build reflection for today and optionally apply suggested_actions to candidate config.
Usage: python scripts/run_reflection.py [--apply-candidate]
  --apply-candidate: write suggested_actions to strategy.candidate.json (if config exists)
"""
import sys
from pathlib import Path
from datetime import date

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy import select
from core.db import SessionLocal
from core.reflection.engine import ReflectionEngine
from core.portfolio.models import Portfolio


def main():
    apply_candidate = "--apply-candidate" in sys.argv
    today = date.today()
    with SessionLocal() as db:
        portfolio = db.scalar(select(Portfolio).where(Portfolio.name == "Paper Portfolio"))
        portfolio_id = portfolio.id if portfolio else None
        engine = ReflectionEngine()
        reflection = engine.build_daily_reflection(db, today, portfolio_id=portfolio_id)
    print("Reflection for", today)
    print("Realized PnL:", reflection.get("realized_pnl"))
    print("Journal count:", reflection.get("journal_count"))
    print("Suggested actions:", reflection.get("suggested_actions"))
    print("Reflection summary:", reflection.get("reflection_summary"))
    if apply_candidate and reflection.get("suggested_actions"):
        try:
            from core.ai.optimizer_agent import apply_suggested_actions_to_candidate
            n = apply_suggested_actions_to_candidate(reflection["suggested_actions"])
            print(f"Applied {n} action(s) to strategy.candidate.json")
        except Exception as e:
            print("Apply candidate failed:", e)


if __name__ == "__main__":
    main()
