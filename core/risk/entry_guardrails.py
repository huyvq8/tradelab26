from __future__ import annotations

from typing import Any

from core.strategies.base import StrategySignal
from core.risk.trade_r_metrics import planned_r_multiple


def _lookup_pct(cfg: dict[str, Any], base_key: str, strategy_name: str, regime: str, default: float) -> float:
    v = cfg.get(f"{base_key}_default")
    if isinstance(v, (int, float)) and float(v) > 0:
        default = float(v)
    by_sr = cfg.get(f"{base_key}_by_strategy_regime") or {}
    if isinstance(by_sr, dict):
        s = by_sr.get(strategy_name) or {}
        if isinstance(s, dict) and isinstance(s.get(regime), (int, float)) and float(s[regime]) > 0:
            return float(s[regime])
    by_s = cfg.get(f"{base_key}_by_strategy") or {}
    if isinstance(by_s, dict) and isinstance(by_s.get(strategy_name), (int, float)) and float(by_s[strategy_name]) > 0:
        return float(by_s[strategy_name])
    by_r = cfg.get(f"{base_key}_by_regime") or {}
    if isinstance(by_r, dict) and isinstance(by_r.get(regime), (int, float)) and float(by_r[regime]) > 0:
        return float(by_r[regime])
    return float(default)


def apply_min_stop_distance_floor(
    signal: StrategySignal,
    *,
    guard_cfg: dict[str, Any],
    regime: str,
) -> dict[str, Any]:
    min_pct = _lookup_pct(
        guard_cfg,
        "min_stop_distance_pct",
        str(signal.strategy_name or ""),
        str(regime or ""),
        0.01,
    )
    entry = float(signal.entry_price or 0)
    if entry <= 0:
        return {"applied": False, "min_pct": min_pct}
    old_sl = float(signal.stop_loss or 0)
    if old_sl <= 0:
        return {"applied": False, "min_pct": min_pct}
    if (signal.side or "").lower() == "long":
        old_pct = max(0.0, (entry - old_sl) / max(entry, 1e-9))
        if old_pct + 1e-12 >= min_pct:
            return {"applied": False, "min_pct": min_pct, "old_pct": old_pct, "new_pct": old_pct}
        new_sl = entry * (1.0 - min_pct)
        signal.stop_loss = float(new_sl)
        return {
            "applied": True,
            "old_stop_loss": old_sl,
            "new_stop_loss": float(new_sl),
            "old_pct": old_pct,
            "new_pct": min_pct,
            "min_pct": min_pct,
        }
    old_pct = max(0.0, (old_sl - entry) / max(entry, 1e-9))
    if old_pct + 1e-12 >= min_pct:
        return {"applied": False, "min_pct": min_pct, "old_pct": old_pct, "new_pct": old_pct}
    new_sl = entry * (1.0 + min_pct)
    signal.stop_loss = float(new_sl)
    return {
        "applied": True,
        "old_stop_loss": old_sl,
        "new_stop_loss": float(new_sl),
        "old_pct": old_pct,
        "new_pct": min_pct,
        "min_pct": min_pct,
    }


def resolve_notional_cap_usd(
    *,
    guard_cfg: dict[str, Any],
    strategy_name: str,
    regime: str,
    equity_usd: float,
) -> float | None:
    pct = _lookup_pct(guard_cfg, "max_notional_pct_of_equity", strategy_name, regime, 0.0)
    if pct <= 0:
        return None
    return max(0.0, float(equity_usd) * float(pct))


def apply_notional_cap(final_size_usd: float, cap_usd: float | None) -> dict[str, Any]:
    if cap_usd is None or cap_usd <= 0:
        return {"applied": False, "final_size_usd": float(final_size_usd)}
    x = float(final_size_usd)
    if x <= cap_usd + 1e-9:
        return {"applied": False, "final_size_usd": x, "cap_usd": float(cap_usd)}
    return {
        "applied": True,
        "final_size_usd": float(cap_usd),
        "cap_usd": float(cap_usd),
        "old_size_usd": x,
    }


def _ohlcv_from_bar(c: Any) -> tuple[float, float, float, float, float] | None:
    """Support Binance kline rows [_, o,h,l,c,v, ...] and Kline1h / objects with open/high/low/close/volume."""
    if c is None:
        return None
    try:
        if hasattr(c, "open") and hasattr(c, "high"):
            return (
                float(c.open),
                float(c.high),
                float(c.low),
                float(c.close),
                float(getattr(c, "volume", 0) or 0),
            )
        return (
            float(c[1]),
            float(c[2]),
            float(c[3]),
            float(c[4]),
            float(c[5]),
        )
    except Exception:
        return None


def mr_long_has_reversal_confirmation(klines_1h: list, *, cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if not klines_1h:
        return False, {"reason": "no_klines"}
    c = klines_1h[-1]
    ohlcv = _ohlcv_from_bar(c)
    if not ohlcv:
        return False, {"reason": "invalid_kline"}
    o, h, l, cl, v = ohlcv
    body = max(abs(cl - o), 1e-9)
    rng = max(h - l, 1e-9)
    lower_wick = max(min(o, cl) - l, 0.0)
    wick_ratio = lower_wick / body
    rec = (cl - l) / rng
    avg_v = 0.0
    prev = klines_1h[-6:-1] if len(klines_1h) >= 6 else klines_1h[:-1]
    if prev:
        vols = []
        for r in prev:
            t = _ohlcv_from_bar(r)
            if t:
                vols.append(t[4])
        avg_v = sum(vols) / len(vols) if vols else 0.0
    vol_spike = (v / avg_v) if avg_v > 0 else 0.0
    decel = False
    if len(klines_1h) >= 3:
        try:
            p1 = _ohlcv_from_bar(klines_1h[-2])
            p2 = _ohlcv_from_bar(klines_1h[-3])
            if p1 and p2:
                r1 = abs(float(p1[3]) - float(p1[0])) / max(float(p1[0]), 1e-9)
                r2 = abs(float(p2[3]) - float(p2[0])) / max(float(p2[0]), 1e-9)
                decel = r1 <= r2 * float(cfg.get("deceleration_ratio_max", 0.8) or 0.8)
        except Exception:
            decel = False
    c1 = wick_ratio >= float(cfg.get("lower_wick_to_body_min", 1.1) or 1.1)
    c2 = rec >= float(cfg.get("recovery_close_pct_of_range_min", 0.45) or 0.45)
    c3 = vol_spike >= float(cfg.get("volume_spike_ratio_min", 1.4) or 1.4) and rec >= 0.35
    ok = c1 or c2 or c3 or decel
    return ok, {
        "lower_wick_to_body": round(wick_ratio, 4),
        "recovery_close_pct_of_range": round(rec, 4),
        "volume_spike_ratio": round(vol_spike, 4),
        "deceleration_ok": bool(decel),
        "flags": {
            "lower_wick": c1,
            "recovery_close": c2,
            "volume_spike_plus_recovery": c3,
            "deceleration": bool(decel),
        },
    }


def evaluate_stop_floor_r_guard(
    signal: StrategySignal,
    *,
    guard_cfg: dict[str, Any],
    regime: str,
    min_candidate_r: float,
) -> dict[str, Any]:
    old_r = planned_r_multiple(signal)
    floor = apply_min_stop_distance_floor(signal, guard_cfg=guard_cfg, regime=regime)
    new_r = planned_r_multiple(signal)
    reject = bool(floor.get("applied")) and (new_r is not None and float(new_r) < float(min_candidate_r))
    return {
        "stop_floor_applied": bool(floor.get("applied")),
        "stop_floor": floor,
        "planned_r_old": old_r,
        "planned_r_new": new_r,
        "reject_low_r_after_floor": reject,
    }
