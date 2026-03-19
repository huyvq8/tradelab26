"""
One-off migration: add columns for learning/reflection (result_r, mfe_pct, mae_pct on journal_entries; risk_usd on trades).
Chạy một lần sau khi nâng cấp: python scripts/migrate_add_learning_columns.py
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
        for table, column, col_type in [
            ("journal_entries", "result_r", "FLOAT"),
            ("journal_entries", "mfe_pct", "FLOAT"),
            ("journal_entries", "mae_pct", "FLOAT"),
            ("trades", "risk_usd", "FLOAT"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"Added {table}.{column}")
            except Exception as e:
                conn.rollback()
                err = str(e).lower()
                if "duplicate column" in err or "already exists" in err:
                    print(f"Column {table}.{column} already exists, skip.")
                else:
                    print(f"Error adding {table}.{column}: {e}")
    print("Migration done.")

if __name__ == "__main__":
    run()
