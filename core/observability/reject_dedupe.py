"""Dedupe entry_rejected lines in decision_log by (symbol, strategy, side, reason_code)."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from time import monotonic
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_DEDUPE_CFG = _ROOT / "config" / "reject_log_dedupe.v1.json"
_STATS_PATH = _ROOT / "data" / "entry_reject_dedupe_stats.json"

_lock = threading.Lock()
_state: dict[str, tuple[float, str]] = {}
"""key -> (monotonic_ts_last_emit, fingerprint)."""


def _load_dedupe_cfg() -> dict[str, Any]:
    if not _DEDUPE_CFG.exists():
        return {"enabled": False}
    try:
        return json.loads(_DEDUPE_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}


def _extract_side(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    s = payload.get("side")
    if s:
        return str(s).strip().lower()
    ns = payload.get("native_signal")
    if isinstance(ns, dict) and ns.get("side"):
        return str(ns["side"]).strip().lower()
    return ""


def _fingerprint_for_payload(
    reason_code: str,
    payload: dict[str, Any] | None,
    cfg: dict[str, Any],
) -> str:
    paths_map = cfg.get("state_change_key_paths") or {}
    paths = paths_map.get(reason_code) or cfg.get("default_state_key_paths") or []
    blob: dict[str, Any] = {}
    p = payload or {}
    for path in paths:
        if "." in path:
            continue
        if path in p:
            try:
                v = p[path]
                if isinstance(v, float):
                    blob[path] = round(v, 4)
                else:
                    blob[path] = v
            except Exception:
                blob[path] = p.get(path)
        elif path == "extension_score" and isinstance(p.get("native_signal"), dict):
            ns = p["native_signal"]
            if "extension_score" in ns:
                try:
                    blob["extension_score"] = round(float(ns["extension_score"]), 4)
                except Exception:
                    blob["extension_score"] = ns.get("extension_score")
    raw = json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _dedupe_key(
    symbol: str,
    strategy_name: str | None,
    side: str,
    reason_code: str | None,
    candle_id: str | None = None,
    *,
    include_candle: bool = False,
) -> str:
    parts = [
        (symbol or "").strip().upper(),
        (strategy_name or "").strip(),
        side.strip().lower(),
        (reason_code or "").strip(),
    ]
    if include_candle and candle_id:
        parts.append(str(candle_id).strip())
    return "|".join(parts)


def cooldown_seconds_for(reason_code: str | None, cfg: dict[str, Any]) -> float:
    rc = (reason_code or "").strip()
    per = cfg.get("per_code_cooldown_seconds") or {}
    if rc in per:
        return float(per[rc])
    return float(cfg.get("default_cooldown_seconds", 45))


def _bump_suppressed_stats(reason_code: str) -> None:
    try:
        _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {"suppressed_by_reason": {}, "version": 1}
        if _STATS_PATH.exists():
            try:
                data = json.loads(_STATS_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        br = data.get("suppressed_by_reason") or {}
        br[reason_code] = int(br.get(reason_code, 0)) + 1
        data["suppressed_by_reason"] = br
        _STATS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def should_emit_entry_reject(
    symbol: str | None,
    strategy_name: str | None,
    reason_code: str | None,
    payload: dict[str, Any] | None,
) -> bool:
    """
    Returns True if this entry_rejected should be written to decision_log.
    On suppressed duplicate, bumps stats file for dashboard noise counts.
    """
    cfg = _load_dedupe_cfg()
    if not cfg.get("enabled", True):
        return True
    rc = (reason_code or "").strip()
    if rc in (cfg.get("never_dedupe_codes") or []):
        return True
    sym = (symbol or "").strip()
    side = _extract_side(payload)
    cid = None
    if isinstance(payload, dict):
        cid = payload.get("candle_id")
        if cid is not None:
            cid = str(cid).strip()
    use_candle = bool(cfg.get("dedupe_key_includes_candle_id", True))
    key = _dedupe_key(sym, strategy_name, side, rc, cid, include_candle=use_candle)
    fp = _fingerprint_for_payload(rc, payload, cfg)
    now = monotonic()
    cd = cooldown_seconds_for(rc, cfg)
    with _lock:
        prev = _state.get(key)
        if prev is None:
            _state[key] = (now, fp)
            return True
        last_t, last_fp = prev
        if fp != last_fp:
            _state[key] = (now, fp)
            return True
        if (now - last_t) >= cd:
            _state[key] = (now, fp)
            return True
    _bump_suppressed_stats(rc)
    return False


def read_dedupe_suppressed_stats() -> dict[str, Any]:
    if not _STATS_PATH.exists():
        return {"suppressed_by_reason": {}}
    try:
        return json.loads(_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"suppressed_by_reason": {}}


def reset_dedupe_memory_state() -> None:
    """Test helper: clear in-memory dedupe windows."""
    with _lock:
        _state.clear()
