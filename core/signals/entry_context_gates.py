"""
Post-pump / context-aware entry gates (Phases 1–3).

Single orchestrator: combo runs in cycle first; then this module; then entry_timing.
Observability: on reject, payload includes phase metrics; optional log_pass_snapshot in config.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _ROOT / "config" / "entry_context_gates.v1.json"


def load_entry_context_gates_config() -> dict:
    cfg: dict = {}
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    try:
        from core.experiments.merge_config import deep_merge
        from core.experiments.paths import resolved_entry_context_gates_path

        op = resolved_entry_context_gates_path()
        if op is not None and op.exists():
            over = json.loads(op.read_text(encoding="utf-8"))
            cfg = deep_merge(cfg, over)
    except Exception:
        pass
    return cfg


def _ohlcv(c: Any) -> tuple[float, float, float, float, float]:
    o = float(getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else 0) or 0)
    h = float(getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else 0) or 0)
    low = float(getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else 0) or 0)
    cl = float(getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else 0) or 0)
    v = float(getattr(c, "volume", None) or (c.get("volume") if isinstance(c, dict) else 0) or 0)
    return o, h, low, cl, v


def _mean_atr_proxy(klines: list, period: int = 14) -> float | None:
    if not klines or len(klines) < 2:
        return None
    trs: list[float] = []
    prev_c = None
    for c in klines:
        _, h, low, cl, _ = _ohlcv(c)
        if prev_c is None:
            trs.append(max(h - low, 0.0))
        else:
            trs.append(max(h - low, abs(h - prev_c), abs(low - prev_c)))
        prev_c = cl
    if not trs:
        return None
    use = trs[-period:] if len(trs) >= period else trs
    return sum(use) / len(use) if use else None


def _ema_last(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
    return ema


@dataclass
class EntryContextGateResult:
    ok: bool
    reason_code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _strategy_applies(strategy_name: str, side: str, apply_to: list[str], long_only: bool) -> bool:
    if apply_to and strategy_name not in apply_to:
        return False
    if long_only and (side or "").lower() != "long":
        return False
    return True


def _run_phase1_native(
    signal: Any,
    *,
    strategy_name: str,
    side: str,
    cfg: dict,
) -> tuple[bool, str, str, dict]:
    out: dict = {}
    if not cfg.get("enabled", True):
        return True, "", "", out

    if not _strategy_applies(strategy_name, side, list(cfg.get("apply_to_strategies") or []), bool(cfg.get("long_only", True))):
        return True, "", "", out

    ext_max = cfg.get("extension_score_max")
    sq_min = cfg.get("setup_quality_min")
    allow = cfg.get("entry_style_allowlist")
    block = [str(x).strip() for x in (cfg.get("entry_style_blocklist") or []) if str(x).strip()]
    missing_mode = (cfg.get("when_native_fields_missing") or "pass").strip().lower()

    ext = getattr(signal, "extension_score", None)
    sq = getattr(signal, "setup_quality", None)
    estyle = getattr(signal, "entry_style", None)
    estyle_s = (estyle or "").strip() if estyle else ""

    native_missing = ext is None and sq is None and not estyle_s
    out["extension_score"] = ext
    out["setup_quality"] = sq
    out["entry_style"] = estyle_s or None

    if native_missing and missing_mode == "reject":
        return False, "CONTEXT_GATE_NATIVE_FIELDS_MISSING", "Structural native fields missing; gate requires setup_quality/extension/entry_style.", out

    if ext is not None and ext_max is not None:
        try:
            if float(ext) > float(ext_max):
                return (
                    False,
                    "CONTEXT_GATE_EXTENSION_SCORE",
                    f"extension_score={ext:.4f} > max={float(ext_max):.4f} (too extended in range; post-pump chase risk).",
                    {**out, "extension_score_max": float(ext_max)},
                )
        except (TypeError, ValueError):
            pass

    if sq is not None and sq_min is not None:
        try:
            if float(sq) < float(sq_min):
                return (
                    False,
                    "CONTEXT_GATE_SETUP_QUALITY",
                    f"setup_quality={sq:.4f} < min={float(sq_min):.4f}.",
                    {**out, "setup_quality_min": float(sq_min)},
                )
        except (TypeError, ValueError):
            pass

    if block and estyle_s and estyle_s in block:
        return False, "CONTEXT_GATE_ENTRY_STYLE", f"entry_style={estyle_s!r} is blocklisted.", {**out, "entry_style_blocklist": block}

    if allow is not None and isinstance(allow, list) and len(allow) > 0:
        allowed_set = {str(x).strip() for x in allow if str(x).strip()}
        if estyle_s and estyle_s not in allowed_set:
            return (
                False,
                "CONTEXT_GATE_ENTRY_STYLE",
                f"entry_style={estyle_s!r} not in allowlist {sorted(allowed_set)}.",
                {**out, "entry_style_allowlist": sorted(allowed_set)},
            )

    return True, "", "", out


def _compute_phase2_metrics(klines: list, price_now: float, cfg: dict) -> dict:
    L = int(cfg.get("lookback_bars", 72) or 72)
    if not klines or L < 5:
        return {"insufficient_klines": True, "lookback_bars": L}

    use = klines[-L:] if len(klines) >= L else klines
    highs = [_ohlcv(c)[1] for c in use]
    lows = [_ohlcv(c)[2] for c in use]
    closes = [_ohlcv(c)[3] for c in use]

    recent_high = max(highs)
    imax = highs.index(recent_high)
    bars_since_local_high = (len(use) - 1) - imax
    dist_pct = ((recent_high - price_now) / max(recent_high, 1e-12)) * 100.0 if recent_high > 0 else 0.0

    rej_lb = min(int(cfg.get("rejection_lookback_bars", 8) or 8), len(use))
    wick_min = float(cfg.get("upper_wick_ratio_min", 0.55) or 0.55)
    recent_rejection = False
    tail = use[-rej_lb:]
    for c in tail:
        o, h, low, cl, _ = _ohlcv(c)
        rng = max(h - low, 1e-12)
        body_top = max(o, cl)
        upper = h - body_top
        if upper / rng >= wick_min and cl <= low + 0.45 * rng:
            recent_rejection = True
            break

    failed_break = False
    frac = float(cfg.get("failed_breakout_pullback_frac", 0.008) or 0.008)
    breakout_eps = float(cfg.get("failed_breakout_prior_eps", 0.003) or 0.003)
    if len(use) >= 8 and imax > 0:
        peak = recent_high
        prior_max = max(highs[:imax])
        broke_above = prior_max > 0 and peak >= prior_max * (1.0 + breakout_eps)
        after_peak_closes = closes[imax + 1 :]
        min_close_after = min(after_peak_closes) if after_peak_closes else peak
        gave_back = min_close_after < peak * (1.0 - frac) or price_now < peak * (1.0 - frac)
        failed_break = bool(broke_above and gave_back and imax < len(use) - 1)

    return {
        "insufficient_klines": False,
        "lookback_bars": len(use),
        "recent_high": round(recent_high, 8),
        "distance_from_recent_high_pct": round(dist_pct, 6),
        "bars_since_local_high": bars_since_local_high,
        "recent_rejection_from_high": recent_rejection,
        "failed_breakout_flag": failed_break,
    }


def _run_phase2(
    klines: list,
    price_now: float,
    *,
    strategy_name: str,
    side: str,
    cfg: dict,
) -> tuple[bool, str, str, dict]:
    metrics = _compute_phase2_metrics(klines, price_now, cfg)
    if not cfg.get("enabled", True):
        return True, "", "", metrics

    if not _strategy_applies(strategy_name, side, list(cfg.get("apply_to_strategies") or []), bool(cfg.get("long_only", True))):
        return True, "", "", metrics

    if metrics.get("insufficient_klines"):
        return True, "", "", metrics

    min_dist = cfg.get("min_distance_from_high_pct")
    if min_dist is not None:
        try:
            if float(metrics["distance_from_recent_high_pct"]) < float(min_dist):
                return (
                    False,
                    "CONTEXT_GATE_DISTANCE_FROM_HIGH",
                    f"distance_from_recent_high_pct={metrics['distance_from_recent_high_pct']}% < min={float(min_dist)}%.",
                    metrics,
                )
        except (TypeError, ValueError):
            pass

    if cfg.get("reject_if_recent_rejection_from_high") and metrics.get("recent_rejection_from_high"):
        return False, "CONTEXT_GATE_RECENT_REJECTION_HIGH", "Upper-wick rejection near highs in recent bars.", metrics

    min_bars = cfg.get("min_bars_since_local_high")
    if min_bars is not None:
        try:
            if int(metrics["bars_since_local_high"]) < int(min_bars):
                return (
                    False,
                    "CONTEXT_GATE_BARS_SINCE_HIGH",
                    f"bars_since_local_high={metrics['bars_since_local_high']} < min={int(min_bars)}.",
                    metrics,
                )
        except (TypeError, ValueError):
            pass

    if cfg.get("reject_on_failed_breakout") and metrics.get("failed_breakout_flag"):
        return False, "CONTEXT_GATE_FAILED_BREAKOUT", "Failed breakout / giveback from recent swing high.", metrics

    return True, "", "", metrics


def _compute_phase3_metrics(klines: list, price_now: float, volume_24h: float | None, cfg: dict) -> dict:
    out: dict = {"insufficient_klines": True}
    plb = int(cfg.get("pullback_speed_lookback_bars", 24) or 24)
    if not klines or len(klines) < plb + 2:
        return out

    use = klines[-plb:]
    highs = [_ohlcv(c)[1] for c in use]
    lows = [_ohlcv(c)[2] for c in use]
    closes = [_ohlcv(c)[3] for c in use]
    vols = [_ohlcv(c)[4] for c in use]

    peak_i = highs.index(max(highs))
    bars_from_peak = max(1, (len(use) - 1) - peak_i)
    atr = _mean_atr_proxy(klines, 14) or 1e-9
    drop = max(highs) - price_now
    pullback_speed = (drop / atr) / bars_from_peak if atr > 0 else 0.0

    n = int(cfg.get("volume_compare_bars", 4) or 4)
    vol_ratio = None
    if len(vols) >= 2 * n:
        a = sum(vols[-n:])
        b = sum(vols[-2 * n : -n]) or 1e-12
        vol_ratio = a / b

    ema_p = cfg.get("reclaim_ema_period")
    ema_val = None
    if ema_p is not None:
        try:
            ep = int(ema_p)
            ema_val = _ema_last(closes, ep) if len(closes) >= ep else None
        except (TypeError, ValueError):
            ema_val = None

    sl_look = min(int(cfg.get("swing_low_lookback", 8) or 8), len(lows))
    recent_swing_low = min(lows[-sl_look:]) if sl_look else None
    broke_swing = bool(recent_swing_low is not None and price_now < recent_swing_low)

    return {
        "insufficient_klines": False,
        "pullback_speed_atr_per_bar": round(pullback_speed, 6),
        "pullback_volume_ratio": round(vol_ratio, 6) if vol_ratio is not None else None,
        "reclaim_ema": round(ema_val, 8) if ema_val is not None else None,
        "price_now": round(price_now, 8),
        "recent_swing_low": round(recent_swing_low, 8) if recent_swing_low is not None else None,
        "broke_recent_swing_low": broke_swing,
    }


def _run_phase3(
    klines: list,
    price_now: float,
    volume_24h: float | None,
    *,
    strategy_name: str,
    side: str,
    cfg: dict,
) -> tuple[bool, str, str, dict]:
    metrics = _compute_phase3_metrics(klines, price_now, volume_24h, cfg)
    if not cfg.get("enabled", False):
        return True, "", "", metrics

    if not _strategy_applies(strategy_name, side, list(cfg.get("apply_to_strategies") or []), bool(cfg.get("long_only", True))):
        return True, "", "", metrics

    if metrics.get("insufficient_klines"):
        return True, "", "", metrics

    max_spd = cfg.get("max_pullback_speed_atr_per_bar")
    if max_spd is not None:
        try:
            if float(metrics["pullback_speed_atr_per_bar"]) > float(max_spd):
                return (
                    False,
                    "CONTEXT_GATE_PULLBACK_SPEED",
                    f"pullback_speed_atr_per_bar={metrics['pullback_speed_atr_per_bar']} > max={float(max_spd)}.",
                    metrics,
                )
        except (TypeError, ValueError):
            pass

    min_vol_r = cfg.get("min_pullback_volume_ratio")
    if min_vol_r is not None and metrics.get("pullback_volume_ratio") is not None:
        try:
            if float(metrics["pullback_volume_ratio"]) < float(min_vol_r):
                return (
                    False,
                    "CONTEXT_GATE_PULLBACK_VOLUME",
                    f"pullback_volume_ratio={metrics['pullback_volume_ratio']} < min={float(min_vol_r)}.",
                    metrics,
                )
        except (TypeError, ValueError):
            pass

    if cfg.get("reject_if_below_reclaim_ema") and metrics.get("reclaim_ema") is not None:
        try:
            if price_now < float(metrics["reclaim_ema"]):
                return False, "CONTEXT_GATE_RECLAIM_QUALITY", f"price {price_now} below reclaim EMA {metrics['reclaim_ema']}.", metrics
        except (TypeError, ValueError):
            pass

    if cfg.get("reject_if_broke_recent_swing_low") and metrics.get("broke_recent_swing_low"):
        return False, "CONTEXT_GATE_STRUCTURE_BREAK", "Price below recent swing low (short-term structure break).", metrics

    return True, "", "", metrics


def _merge_section(root: dict, key: str, defaults: dict) -> dict:
    raw = root.get(key)
    out = dict(defaults)
    if isinstance(raw, dict):
        out.update(raw)
    return out


def evaluate_entry_context_gates(
    signal: Any,
    *,
    symbol: str,
    strategy_name: str,
    side: str,
    price_now: float,
    klines: list,
    volume_24h: float | None = None,
    cfg: dict | None = None,
) -> EntryContextGateResult:
    """
    Run phase1 → phase2 → phase3. Returns first failure or ok with merged metrics in details.
    """
    cfg = cfg if cfg is not None else load_entry_context_gates_config()
    root = cfg or {}

    d_all: dict = {"symbol": symbol, "strategy_name": strategy_name, "side": side}

    p1 = _merge_section(root, "phase1_native_signal", {"enabled": True})
    ok1, rc1, msg1, m1 = _run_phase1_native(signal, strategy_name=strategy_name, side=side, cfg=p1)
    d_all["phase1_native_signal"] = m1
    if not ok1:
        return EntryContextGateResult(False, rc1, msg1, d_all)

    p2 = _merge_section(root, "phase2_recent_context", {"enabled": False})
    ok2, rc2, msg2, m2 = _run_phase2(klines, price_now, strategy_name=strategy_name, side=side, cfg=p2)
    d_all["phase2_recent_context"] = m2
    if not ok2:
        return EntryContextGateResult(False, rc2, msg2, d_all)

    p3 = _merge_section(root, "phase3_pullback_quality", {"enabled": False})
    ok3, rc3, msg3, m3 = _run_phase3(klines, price_now, volume_24h, strategy_name=strategy_name, side=side, cfg=p3)
    d_all["phase3_pullback_quality"] = m3
    if not ok3:
        return EntryContextGateResult(False, rc3, msg3, d_all)

    return EntryContextGateResult(True, "", "", d_all)


def should_log_context_pass(symbol: str, strategy_name: str, cfg: dict) -> bool:
    """
    Log every pass if log_pass_snapshot is true; otherwise optional focused debug:
    pass_snapshot_debug.enabled + match symbols (empty = wildcard) + strategies (empty = wildcard).
    """
    root = cfg or {}
    if root.get("log_pass_snapshot"):
        return True
    dbg = root.get("pass_snapshot_debug")
    if not isinstance(dbg, dict) or not dbg.get("enabled"):
        return False
    syms = [(s or "").strip().upper() for s in (dbg.get("symbols") or []) if (s or "").strip()]
    sts = [(s or "").strip() for s in (dbg.get("strategies") or []) if (s or "").strip()]
    su = (symbol or "").strip().upper()
    st = (strategy_name or "").strip()
    if syms and su not in syms:
        return False
    if sts and st not in sts:
        return False
    return True


def maybe_log_context_pass(log_decision_fn: Any, *, symbol: str, strategy_name: str, details: dict, cfg: dict) -> None:
    if not should_log_context_pass(symbol, strategy_name, cfg):
        return
    try:
        log_decision_fn(
            "entry_context_gates_pass",
            details,
            symbol=symbol,
            strategy_name=strategy_name,
            reason_code="CONTEXT_GATES_PASS",
        )
    except Exception:
        pass
