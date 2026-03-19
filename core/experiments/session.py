"""
Append daily experiment summaries for A/B comparison (JSON lines per day).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.experiments import paths

_RESULTS_DIR = paths._ROOT / "storage" / "experiments"


def record_experiment_snapshot(metrics: dict[str, Any], *, label: str | None = None) -> Path:
    """
    Append one JSON object to storage/experiments/results_YYYY-MM-DD.jsonl
    """
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    path = _RESULTS_DIR / f"results_{day}.jsonl"
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": label or "validation_run",
        **paths.experiment_labels(),
        "metrics": metrics,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta_path = _RESULTS_DIR / "last_run.json"
    meta_path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
