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
from core.brain.replay import replay_cycle_change_points, replay_proposal_vs_baseline


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python scripts/replay_brain_v4.py <cycle_id>\n"
            "  python scripts/replay_brain_v4.py cycle <cycle_id>\n"
            "  python scripts/replay_brain_v4.py proposal-compare <proposal_public_id>"
        )
        sys.exit(1)
    a1 = sys.argv[1].strip()
    if a1.lower() == "proposal-compare" and len(sys.argv) >= 3:
        pid = sys.argv[2].strip()
        out = replay_proposal_vs_baseline(
            {"pnl_usd": 0, "max_drawdown_pct": 0.01},
            proposal_public_id=pid,
            proposed_metrics={"pnl_usd": 0, "max_drawdown_pct": 0.008},
            fee_slippage_bps=5,
            drawdown_penalty_weight=100.0,
        )
        print(json.dumps(out, indent=2, default=str))
        return
    if a1.lower() == "cycle" and len(sys.argv) >= 3:
        cid = sys.argv[2].strip()
    else:
        cid = a1
    if not cid:
        print("Missing cycle_id")
        sys.exit(1)
    with SessionLocal() as db:
        db: Session
        out = replay_cycle_change_points(db, cid)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
