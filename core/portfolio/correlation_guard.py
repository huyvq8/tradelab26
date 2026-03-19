"""Gioi han so vi the fast cung sector. Sector = map base -> nhom hoac fallback alt:BASE."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _default_sectors() -> dict[str, str]:
    """Base asset (upper) → sector key."""
    return {
        "BTC": "maj",
        "ETH": "maj",
        "BNB": "maj",
        "SOL": "maj",
    }


def load_correlation_sector_map() -> dict[str, str]:
    """Đọc config/correlation_sectors.v1.json nếu có."""
    path = _CONFIG_DIR / "correlation_sectors.v1.json"
    if not path.exists():
        return _default_sectors()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "base_to_sector" in raw:
            m = raw["base_to_sector"]
            if isinstance(m, dict):
                out = {**_default_sectors()}
                out.update({str(k).upper(): str(v) for k, v in m.items()})
                return out
    except Exception as e:
        _LOG.warning("correlation_sectors: %s", e)
    return _default_sectors()


def symbol_base_asset(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    for suf in ("USDT", "USDC", "BUSD", "FDUSD", "PERP"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s or "?"


def sector_for_symbol(symbol: str, base_map: dict[str, str] | None = None) -> str:
    b = symbol_base_asset(symbol)
    m = base_map if base_map is not None else load_correlation_sector_map()
    return m.get(b) or f"alt:{b}"


def count_fast_same_sector(
    open_positions: list[Any],
    new_symbol: str,
    *,
    base_map: dict[str, str] | None = None,
) -> int:
    target = sector_for_symbol(new_symbol, base_map)
    n = 0
    for p in open_positions:
        if not getattr(p, "is_open", True):
            continue
        if (getattr(p, "capital_bucket", None) or "core") != "fast":
            continue
        if sector_for_symbol(getattr(p, "symbol", ""), base_map) == target:
            n += 1
    return n


def correlation_guard_rejects_fast_entry(
    open_positions: list[Any],
    new_symbol: str,
    cs_cfg: dict[str, Any],
    *,
    base_map: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """
    True nếu từ chối mở thêm fast (đã đủ slot cùng sector).
    correlation_guard_max_same_sector_fast: 0 = tắt.
    """
    try:
        lim = int(cs_cfg.get("correlation_guard_max_same_sector_fast", 0) or 0)
    except (TypeError, ValueError):
        lim = 0
    if lim <= 0 or not cs_cfg.get("enabled"):
        return False, ""
    cnt = count_fast_same_sector(open_positions, new_symbol, base_map=base_map)
    if cnt >= lim:
        sec = sector_for_symbol(new_symbol, base_map)
        return True, f"Correlation guard fast: sector={sec} already has {cnt} >= limit {lim}"
    return False, ""
