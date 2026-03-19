"""Watchlist: symbols to watch. Persisted to storage/watchlist.txt."""
from pathlib import Path

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
