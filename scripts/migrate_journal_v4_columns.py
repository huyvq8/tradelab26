"""
Migration: add v4 journal columns (side, reasons, market_context, risk_score, timeframe, exit_reason, mistake_tags).
Chạy một lần: python scripts/migrate_journal_v4_columns.py
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy import text
from core.db import engine


def run():
    columns = [
        ("side", "VARCHAR(10)"),
        ("timeframe", "VARCHAR(10)"),
        ("reasons", "TEXT"),
        ("market_context", "TEXT"),
        ("risk_score", "FLOAT"),
        ("exit_reason", "VARCHAR(30)"),
        ("mistake_tags", "TEXT"),
    ]
    with engine.connect() as conn:
        for col_name, col_type in columns:
            try:
                conn.execute(text(f"ALTER TABLE journal_entries ADD COLUMN {col_name} {col_type}"))
                conn.commit()
                print(f"Added journal_entries.{col_name}")
            except Exception as e:
                conn.rollback()
                err = str(e).lower()
                if "duplicate column" in err or "already exists" in err:
                    print(f"Column journal_entries.{col_name} already exists, skip.")
                else:
                    print(f"Error adding journal_entries.{col_name}: {e}")
    print("Migration done.")


if __name__ == "__main__":
    run()
