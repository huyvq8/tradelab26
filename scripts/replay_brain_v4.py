"""CLI: python scripts/replay_brain_v4.py <cycle_uuid>"""
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.brain.replay import replay_cycle_change_points


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/replay_brain_v4.py <cycle_id>")
        sys.exit(1)
    cid = sys.argv[1].strip()
    with SessionLocal() as db:
        db: Session
        out = replay_cycle_change_points(db, cid)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
