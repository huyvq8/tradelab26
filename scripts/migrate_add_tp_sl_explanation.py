"""
Migration: add tp_sl_explanation to journal_entries (why TP/SL was chosen, for dashboard).
Chạy một lần: python scripts/migrate_add_tp_sl_explanation.py
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy import text
from core.db import engine


def run():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE journal_entries ADD COLUMN tp_sl_explanation TEXT"))
            conn.commit()
            print("Added journal_entries.tp_sl_explanation")
        except Exception as e:
            conn.rollback()
            err = str(e).lower()
            if "duplicate column" in err or "already exists" in err:
                print("Column journal_entries.tp_sl_explanation already exists, skip.")
            else:
                raise
    print("Migration done.")


if __name__ == "__main__":
    run()
