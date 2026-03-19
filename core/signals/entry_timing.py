"""
Entry timing filters: extended candle, pullback/chase-top, per-symbol cooldown.
Machine-readable reason codes for observability + dashboard.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent


def load_entry_timing_config() -> dict:
    try:
        from core.experiments.paths import resolved_entry_timing_config_path

        p = resolved_entry_timing_config_path()
    except Exception:
        p = _ROOT / "config" / "entry_timing.v1.json"
    if not p.exists():
        return {"enabled": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}


def _cooldown_path(cfg: dict) -> Path:
    rel = (cfg.get("cooldown") or {}).get("storage_relative") or "storage/entry_cooldown.json"
    return _ROOT / rel


def _parse_ts(s: str) -> datetime | None:
    try:
        s = s.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def get_last_entry_utc(symbol: str, cfg: dict | None = None) -> datetime | None:
    cfg = cfg or load_entry_timing_config()
    cd = cfg.get("cooldown") or {}
    if not cd.get("enabled", True):
        return None
    path = _cooldown_path(cfg)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    raw = data.get((symbol or "").strip().upper())
    if not raw:
        return None
    dt = _parse_ts(raw) if isinstance(raw, str) else None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def record_entry_opened(symbol: str, cfg: dict | None = None) -> None:
    cfg = cfg or load_entry_timing_config()
    cd = cfg.get("cooldown") or {}
    if not cd.get("enabled", True):
        return
    path = _cooldown_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    sym = (symbol or "").strip().upper()
    data[sym] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _ohlc(c: Any) -> tuple[float, float, float, float]:
    o = float(getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else 0) or 0)
    h = float(getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else 0) or 0)
    low = float(getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else 0) or 0)
    cl = float(getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else 0) or 0)
    return o, h, low, cl


def _volume(c: Any) -> float:
    v = getattr(c, "volume", None)
    if v is None and isinstance(c, dict):
        v = c.get("volume")
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class EntryTimingResult:
    ok: bool
    reason_code: str
    message: str
    details: dict


def _last_closed_candle(klines: list) -> Any | None:
    if not klines:
        return None
    return klines[-1]


def evaluate_entry_timing(
    *,
    strategy_name: str,
    symbol: str,
    side: str,
    price_now: float,
    klines_1h: list,
    cfg: dict | None = None,
) -> EntryTimingResult:
    """
    Return ok=True if entry is allowed. reason_code empty when ok.
    """
    cfg = cfg or load_entry_timing_config()
    details: dict = {"strategy": strategy_name, "symbol": symbol, "side": side}
    if not cfg.get("enabled", True):
        return EntryTimingResult(True, "", "", details)

    sym_u = (symbol or "").strip().upper()
    cd = cfg.get("cooldown") or {}
    if cd.get("enabled", True):
        sec = float(cd.get("seconds_between_entries_per_symbol", 900) or 900)
        last = get_last_entry_utc(sym_u, cfg)
        if last is not None:
            now = datetime.now(timezone.utc)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            delta = (now - last).total_seconds()
            details["cooldown_seconds_remaining"] = max(0.0, sec - delta)
            if delta < sec:
                return EntryTimingResult(
                    False,
                    "ENTRY_COOLDOWN",
                    f"Symbol cooldown active ({int(sec)}s); {int(sec - delta)}s remaining.",
                    details,
                )

    apply_to = set(cfg.get("apply_to_strategies") or [])
    if apply_to and strategy_name not in apply_to:
        return EntryTimingResult(True, "", "", details)

    if side.lower() != "long":
        # Filters below target long chase / extended bull candles on alt longs
        return EntryTimingResult(True, "", "", details)

    last = _last_closed_candle(klines_1h)
    if last is None:
        return EntryTimingResult(
            False,
            "ENTRY_NO_KLINES",
            "No 1h klines for entry timing filter.",
            details,
        )

    o, h, low, c = _ohlc(last)
    rng = max(h - low, 1e-12)
    body = abs(c - o)
    body_ratio = body / rng
    mid = (h + low) / 2.0

    conf = cfg.get("confirmation") or {}
    if conf.get("enabled", False):
        body_min = float(conf.get("candle_body_range_ratio_min", 0.25) or 0.25)
        details["confirmation_body_range_ratio"] = round(body_ratio, 4)
        if body_ratio < body_min:
            return EntryTimingResult(
                False,
                "ENTRY_WEAK_BODY",
                conf.get("reject_message_weak_body")
                or f"Last 1h candle body/range {body_ratio:.2f} < min {body_min:.2f} (no confirmation).",
                details,
            )

        vol_lb = max(3, int(conf.get("volume_lookback_bars", 20) or 20))
        vol_min_ratio = float(conf.get("volume_spike_min_ratio", 0) or 0)
        if vol_min_ratio > 0 and len(klines_1h) >= vol_lb + 2:
            priors = klines_1h[-(vol_lb + 1) : -1]
            vols = [_volume(k) for k in priors]
            vols_pos = [v for v in vols if v > 0]
            v_last = _volume(last)
            details["confirmation_volume_spike_ratio"] = None
            if vols_pos and v_last > 0:
                avg_prev = sum(vols_pos) / len(vols_pos)
                if avg_prev > 0:
                    spike_r = v_last / avg_prev
                    details["confirmation_volume_spike_ratio"] = round(spike_r, 4)
                    if spike_r < vol_min_ratio:
                        return EntryTimingResult(
                            False,
                            "ENTRY_LOW_VOLUME_SPIKE",
                            conf.get("reject_message_volume")
                            or (
                                f"Volume vs {vol_lb}h avg {spike_r:.2f} < {vol_min_ratio:.2f}; "
                                "defer until participation confirms."
                            ),
                            details,
                        )

        vwap_lb = max(2, int(conf.get("vwap_lookback_bars", 24) or 24))
        max_d_vwap = float(conf.get("max_distance_from_vwap_pct", 0) or 0)
        if max_d_vwap > 0 and len(klines_1h) >= 2:
            use = klines_1h[-vwap_lb:]
            tw = 0.0
            vw = 0.0
            for k in use:
                oh, hh, lh, ch = _ohlc(k)
                typ = (hh + lh + ch) / 3.0
                vv = _volume(k)
                if vv > 0 and typ > 0:
                    tw += typ * vv
                    vw += vv
            if vw > 0:
                vwap_px = tw / vw
                dist_pct = abs(float(price_now) - vwap_px) / max(vwap_px, 1e-12) * 100.0
                details["confirmation_vwap"] = round(vwap_px, 8)
                details["confirmation_dist_from_vwap_pct"] = round(dist_pct, 4)
                if dist_pct > max_d_vwap:
                    return EntryTimingResult(
                        False,
                        "ENTRY_FAR_FROM_VWAP",
                        conf.get("reject_message_vwap")
                        or (
                            f"Price {dist_pct:.2f}% from session VWAP (max {max_d_vwap:.2f}%); "
                            "avoid late chase."
                        ),
                        details,
                    )

    ext = cfg.get("extended_candle") or {}
    if ext.get("enabled", True):
        br_min = float(ext.get("body_range_ratio_min", 0.72) or 0.72)
        rp_min = float(ext.get("range_pct_of_price_min", 0.018) or 0.018)
        ref_px = max(price_now, c, 1e-12)
        range_pct = rng / ref_px
        details.update(
            {
                "last_body_range_ratio": round(body_ratio, 4),
                "last_range_pct": round(range_pct, 5),
            }
        )
        if body_ratio >= br_min and range_pct >= rp_min:
            return EntryTimingResult(
                False,
                "ENTRY_EXTENDED_CANDLE",
                ext.get("reject_message") or "Over-extended candle; skip immediate breakout entry.",
                details,
            )

    pb = cfg.get("pullback") or {}
    if pb.get("enabled", True):
        chase = float(pb.get("chase_top_pct_of_range", 0.12) or 0.12)
        pos_in_range = (price_now - low) / rng if rng > 0 else 0.5
        details["position_in_candle_range"] = round(pos_in_range, 4)
        # In top X% of range = chasing the high
        if pos_in_range > (1.0 - chase):
            return EntryTimingResult(
                False,
                "ENTRY_CHASE_TOP",
                pb.get("reject_message") or "Price in top of last candle range; wait for pullback.",
                details,
            )
        if pb.get("require_close_below_prior_high", True) and len(klines_1h) >= 2:
            _, h2, _, c2 = _ohlc(klines_1h[-2])
            # Strong green continuation bar at highs: current price still above prior high
            bullish_bar = c > o
            broke_up = h > h2 * 1.0001
            if bullish_bar and broke_up and price_now >= h2 and c >= h * (1.0 - chase):
                return EntryTimingResult(
                    False,
                    "ENTRY_NO_RETEST",
                    "Breakout extension without retest; deferred entry.",
                    details,
                )

    # Mild: prefer some mean reversion toward mid if last bar very bullish
    if c > o and body_ratio > 0.55 and price_now > mid * 1.015:
        return EntryTimingResult(
            False,
            "ENTRY_EXTENDED_ABOVE_MID",
            "Price extended above 1h candle midpoint after strong bullish bar.",
            {**details, "mid": mid},
        )

    return EntryTimingResult(True, "", "", details)
