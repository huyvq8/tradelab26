"""Map reason_code → dashboard/API reject bucket (good / policy / sizing / noise)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CFG_PATH = _ROOT / "config" / "reject_classification.v1.json"


def _load_cfg() -> dict[str, Any]:
    if not _CFG_PATH.exists():
        return {}
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def classify_entry_reject(
    reason_code: str | None,
    *,
    dedupe_suppressed: bool = False,
) -> str:
    """
    Buckets:
    - noise_reject_repeated: logged only for suppressed repeats / dedupe side-channel
    - good_reject: entry quality / context gates (intentional)
    - policy_reject: brain / combo / scale-in policy
    - sizing_reject: post-sizing minimum
    """
    rc = (reason_code or "").strip()
    if dedupe_suppressed:
        return "noise_reject_repeated"
    cfg = _load_cfg()
    if rc in (cfg.get("sizing_reject_codes") or []):
        return "sizing_reject"
    for prefix in cfg.get("policy_reject_prefixes") or []:
        if rc.startswith(prefix):
            return "policy_reject"
    if rc in (cfg.get("policy_reject_codes") or []):
        return "policy_reject"
    for prefix in cfg.get("good_reject_prefixes") or []:
        if rc.startswith(prefix):
            return "good_reject"
    if rc in (cfg.get("good_reject_codes") or []):
        return "good_reject"
    return str(cfg.get("default_bucket") or "good_reject")


def attach_reject_bucket(row: dict[str, Any]) -> dict[str, Any]:
    """Add reject_bucket to a decision_log row dict (mutates copy)."""
    out = dict(row)
    if (out.get("event") or "") != "entry_rejected":
        return out
    out["reject_bucket"] = classify_entry_reject(out.get("reason_code"))
    return out
