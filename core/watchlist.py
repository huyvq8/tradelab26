"""Watchlist: symbols to watch. Persisted to storage/watchlist.txt."""
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = ROOT / "storage" / "watchlist.txt"
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]


def get_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST_FILE.write_text(",".join(DEFAULT_SYMBOLS), encoding="utf-8")
        return DEFAULT_SYMBOLS.copy()
    raw = WATCHLIST_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return DEFAULT_SYMBOLS.copy()
    return [s.strip().upper() for s in raw.replace(",", " ").split() if s.strip()]


def set_watchlist(symbols: list[str]) -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FILE.write_text(",".join(s.strip().upper() for s in symbols if s.strip()), encoding="utf-8")


def get_effective_execution_watchlist() -> dict[str, Any]:
    """
    Build the execution watchlist shared by worker + dashboard.

    Returns:
      - manual_watchlist: list[str]
      - dynamic_shortlist: list[str]
      - effective_watchlist: list[str]
      - manual_watchlist_count: int
      - dynamic_shortlist_count: int
      - effective_execution_watchlist_count: int
    """
    manual_watchlist = get_watchlist()
    dynamic_shortlist: list[str] = []
    effective_watchlist = list(manual_watchlist)

    try:
        from core.config import settings, get_effective_single_strategy_mode
        from core.discovery.mr_scout import load_mr_opportunity_config, scan_mr_candidates

        single_mode = (get_effective_single_strategy_mode() or "").strip()
        if single_mode == "mean_reversion":
            mr_cfg = load_mr_opportunity_config()
            if bool(mr_cfg.get("dynamic_watchlist_when_mr_only", True)):
                rows = scan_mr_candidates(
                    cfg=mr_cfg,
                    available_cash=float(getattr(settings, "default_capital_usd", 1000.0) or 1000.0),
                    capital_usd_for_risk=float(getattr(settings, "default_capital_usd", 1000.0) or 1000.0),
                    risk_pct=float(getattr(settings, "default_risk_pct", 0.01) or 0.01),
                )
                dynamic_shortlist = [
                    str(r.get("symbol", "")).strip().upper()
                    for r in rows
                    if r.get("symbol")
                ]
                lim = int(mr_cfg.get("dynamic_watchlist_top_n", 6) or 6)
                dynamic_shortlist = dynamic_shortlist[: max(1, lim)]
                effective_watchlist = list(dict.fromkeys(manual_watchlist + dynamic_shortlist))
    except Exception:
        dynamic_shortlist = []
        effective_watchlist = list(manual_watchlist)

    return {
        "manual_watchlist": manual_watchlist,
        "dynamic_shortlist": dynamic_shortlist,
        "effective_watchlist": effective_watchlist,
        "manual_watchlist_count": len(manual_watchlist),
        "dynamic_shortlist_count": len(dynamic_shortlist),
        "effective_execution_watchlist_count": len(effective_watchlist),
    }
