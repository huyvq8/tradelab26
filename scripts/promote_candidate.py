"""
Promote candidate config to active if promotion rules pass (v4).
Runs backtest comparison, then copies strategy.candidate.json -> strategy.active.json (with backup).
Usage: python scripts/promote_candidate.py [SYMBOL]
"""
import sys
from pathlib import Path
from datetime import datetime

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import json
from core.market_data.client import get_klines_1h
from core.validate.compare_configs import klines_to_dataframe, compare_configs
from core.ai.optimizer_agent import STRATEGY_ACTIVE, STRATEGY_CANDIDATE, get_candidate_strategy_config


def main():
    if not STRATEGY_CANDIDATE.exists():
        print("No strategy.candidate.json found. Run reflection and apply-candidate first.")
        return
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    klines = get_klines_1h(symbol, limit=500)
    if not klines or len(klines) < 50:
        print("Not enough klines to run backtest.")
        return
    df = klines_to_dataframe(klines)
    result = compare_configs(df, symbol=symbol)
    if not result["promotion_pass"]:
        print("Promotion rules not passed:")
        for r in result["promotion_reasons"]:
            print("  -", r)
        return
    # Backup active
    backup_path = Path(str(STRATEGY_ACTIVE) + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if STRATEGY_ACTIVE.exists():
        backup_path.write_text(STRATEGY_ACTIVE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backed up active to {backup_path.name}")
    data = get_candidate_strategy_config()
    if not data:
        print("Could not load candidate.")
        return
    STRATEGY_ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_ACTIVE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Promoted candidate to strategy.active.json.")


if __name__ == "__main__":
    main()
