"""
One-off migration: add scale_in_count, initial_entry_price to positions table.
Chay mot lan sau khi cap nhat code Smart Scale-In. SQLite khong co IF NOT EXISTS cho ADD COLUMN,
nen kiem tra column ton tai truoc khi ADD.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# project root
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "trading_lab.db"
if not DB_PATH.exists():
    # co the dat trong apps/worker hoac env
    for p in [ROOT / "apps" / "worker" / "trading_lab.db", Path.cwd() / "trading_lab.db"]:
        if p.exists():
            DB_PATH = p
            break


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(positions)")
    cols = [row[1] for row in cur.fetchall()]
    if "scale_in_count" not in cols:
        cur.execute("ALTER TABLE positions ADD COLUMN scale_in_count INTEGER DEFAULT 0")
        print("Added scale_in_count")
    if "initial_entry_price" not in cols:
        cur.execute("ALTER TABLE positions ADD COLUMN initial_entry_price REAL")
        print("Added initial_entry_price")
    conn.commit()
    conn.close()
    print("Migration done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
