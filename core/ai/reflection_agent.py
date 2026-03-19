"""
Reflection Agent (v4): reads journal + closed trades, returns structured JSON.
Output: summary (best/worst strategy, worst_regime), mistakes_found, suggested_actions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.config import settings

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _call_openai_json(system_prompt: str, user_content: str, max_tokens: int = 1200) -> dict | None:
    if not getattr(settings, "openai_api_key", None) or not settings.openai_api_key.strip():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key.strip())
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        if not r.choices or not r.choices[0].message or not r.choices[0].message.content:
            return None
        raw = r.choices[0].message.content.strip()
        # Extract JSON (allow wrapped in ```json ... ```)
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception:
        pass
    return None


def run_structured_reflection(
    target_date: str,
    trades_text: str,
    journal_text: str,
    metrics: dict,
    learned_warnings: list[dict],
    repeated_mistakes: list[dict],
) -> dict | None:
    """
    Run AI reflection and return structured output:
    - summary: { best_strategy, worst_strategy, worst_regime }
    - mistakes_found: [str, ...]
    - suggested_actions: [{ type, strategy?, regime?, value? }, ...]
    Returns None if no API key or parse error.
    """
    prompt = _load_prompt("daily_reflection_structured")
    if not prompt:
        return None
    user = f"""Date: {target_date}

Metrics: win_rate={metrics.get('win_rate', 0):.2%}, profit_factor={metrics.get('profit_factor', 0):.2f}, total_trades={metrics.get('total_trades', 0)}, strategy_accuracy={metrics.get('strategy_accuracy', {})}

Closed trades:
{trades_text}

Journal (entry reason, lessons, mistakes, exit_reason):
{journal_text}

Repeated mistakes: {repeated_mistakes}

Warnings from history (last 30 days): {learned_warnings}

Return only the JSON object with summary, mistakes_found, and suggested_actions.
"""
    out = _call_openai_json(prompt, user)
    if not out or not isinstance(out, dict):
        return None
    # Normalize
    if "summary" not in out:
        out["summary"] = {}
    if "mistakes_found" not in out or not isinstance(out["mistakes_found"], list):
        out["mistakes_found"] = []
    if "suggested_actions" not in out or not isinstance(out["suggested_actions"], list):
        out["suggested_actions"] = []
    return out
