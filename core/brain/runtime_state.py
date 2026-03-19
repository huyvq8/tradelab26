"""Persistent hysteresis + last BTC regime for leader-break detector."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_PATH = _ROOT / "storage" / "brain_v4_runtime.json"


@dataclass
class RuntimeStateV4:
    version: int = 1
    last_btc_regime: str = ""
    last_btc_regime_ts: float = 0.0
    market_state: str = "BALANCED"
    market_state_since_ts: float = 0.0
    market_state_bars: int = 0
    policy_mode: str = "NORMAL"
    policy_since_ts: float = 0.0
    token_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    reflex_cooldown_until: dict[str, float] = field(default_factory=dict)
    last_cp_by_symbol: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_json(cls, d: dict) -> RuntimeStateV4:
        return cls(
            version=int(d.get("version", 1)),
            last_btc_regime=str(d.get("last_btc_regime", "")),
            last_btc_regime_ts=float(d.get("last_btc_regime_ts", 0)),
            market_state=str(d.get("market_state", "BALANCED")),
            market_state_since_ts=float(d.get("market_state_since_ts", 0)),
            market_state_bars=int(d.get("market_state_bars", 0)),
            policy_mode=str(d.get("policy_mode", "NORMAL")),
            policy_since_ts=float(d.get("policy_since_ts", 0)),
            token_states=dict(d.get("token_states") or {}),
            reflex_cooldown_until=dict(d.get("reflex_cooldown_until") or {}),
            last_cp_by_symbol=dict(d.get("last_cp_by_symbol") or {}),
        )


def load_runtime_state() -> RuntimeStateV4:
    try:
        if _STATE_PATH.exists():
            return RuntimeStateV4.from_json(json.loads(_STATE_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return RuntimeStateV4()


def save_runtime_state(state: RuntimeStateV4) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state.to_json(), indent=2), encoding="utf-8")
    except Exception:
        pass


def new_trace_id() -> str:
    return str(uuid.uuid4())[:12]


def hysteresis_pick(
    new_state: str,
    new_confidence: float,
    prev_state: str,
    prev_confidence: float,
    *,
    switch_margin: float,
    emergency_states: frozenset[str],
) -> tuple[str, float]:
    if new_state in emergency_states:
        return new_state, new_confidence
    if new_state == prev_state:
        return new_state, new_confidence
    if new_confidence + switch_margin <= prev_confidence:
        return prev_state, prev_confidence
    return new_state, new_confidence


def policy_cooldown_ok(
    state: RuntimeStateV4,
    proposed: str,
    *,
    min_ttl_sec: float,
    emergency: bool,
) -> bool:
    if emergency:
        return True
    now = time.time()
    if state.policy_mode == proposed:
        return True
    return now - state.policy_since_ts >= min_ttl_sec


def update_after_cycle(
    rt: RuntimeStateV4,
    *,
    btc_regime: str,
    market_state: str,
    policy_mode: str,
    symbol_token_updates: dict[str, dict[str, Any]] | None = None,
) -> None:
    now = time.time()
    if btc_regime and btc_regime != rt.last_btc_regime:
        rt.last_btc_regime = btc_regime
        rt.last_btc_regime_ts = now
    if market_state != rt.market_state:
        rt.market_state = market_state
        rt.market_state_since_ts = now
        rt.market_state_bars = 0
    else:
        rt.market_state_bars += 1
    if policy_mode != rt.policy_mode:
        rt.policy_mode = policy_mode
        rt.policy_since_ts = now
    if symbol_token_updates:
        for sym, data in symbol_token_updates.items():
            rt.token_states[sym] = {**rt.token_states.get(sym, {}), **data, "ts": now}


def set_reflex_cooldown(rt: RuntimeStateV4, key: str, seconds: float) -> None:
    rt.reflex_cooldown_until[key] = time.time() + max(0.0, seconds)


def reflex_cooldown_active(rt: RuntimeStateV4, key: str) -> bool:
    return time.time() < rt.reflex_cooldown_until.get(key, 0.0)
