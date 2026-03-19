"""
AI-powered reflection: daily review, post-mortem, next-day plan.
Uses OpenAI API when OPENAI_API_KEY is set; otherwise returns None (fallback to rule-based).
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _call_openai(system_prompt: str, user_content: str, max_tokens: int = 1500, reason: str | None = None) -> str | None:
    if not getattr(settings, "openai_api_key", None) or not settings.openai_api_key.strip():
        return None
    if reason:
        logger.info("AI_CALL reason=%s", reason)
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
        if r.choices and r.choices[0].message and r.choices[0].message.content:
            return r.choices[0].message.content.strip()
    except Exception:
        pass
    return None


def daily_review_from_context(
    target_date: str,
    realized_pnl: float,
    total_trades: int,
    win_rate: float,
    profit_factor: float,
    expectancy_usd: float,
    trades_text: str,
    journal_text: str,
    repeated_mistakes: list[dict],
) -> str | None:
    """Generate daily reflection text using OpenAI. Returns None if no key or on error."""
    prompt = _load_prompt("daily_review")
    if not prompt:
        return None
    user = f"""Date: {target_date}
Realized PnL (USD): {realized_pnl}
Total closed trades: {total_trades}
Win rate: {win_rate:.2%}
Profit factor: {profit_factor:.2f}
Expectancy (USD): {expectancy_usd}

Trades today:
{trades_text}

Journal excerpts (entry reason, lessons, mistakes):
{journal_text}

Repeated mistakes (from history): {repeated_mistakes}
"""
    return _call_openai(prompt, user, reason="daily_review")


def next_day_plan_from_context(
    reflection_summary: str,
    realized_pnl: float,
    win_rate: float,
    strategy_accuracy: dict,
    repeated_mistakes: list[dict],
    open_positions: int,
) -> str | None:
    """Generate next-day checklist using OpenAI. Returns None if no key or on error."""
    prompt = _load_prompt("next_day_plan")
    if not prompt:
        return None
    user = f"""Yesterday's reflection summary:
{reflection_summary}

Today's metrics:
- Realized PnL: {realized_pnl} USD
- Win rate: {win_rate:.2%}
- Strategy accuracy: {strategy_accuracy}

Repeated mistakes to avoid: {repeated_mistakes}
Current open positions: {open_positions}
"""
    return _call_openai(prompt, user, max_tokens=800, reason="next_day_plan")


def trade_postmortem_from_trade(
    symbol: str,
    strategy: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    risk_usd: float | None,
    entry_reason: str,
    lessons: str,
    mistakes: str,
    note: str = "",
) -> str | None:
    """Generate a single-trade post-mortem. Returns None if no key or on error."""
    prompt = _load_prompt("trade_postmortem")
    if not prompt:
        return None
    r_mult = (pnl_usd / risk_usd) if risk_usd and risk_usd > 0 else None
    user = f"""Trade: {symbol} {side} | {strategy}
Entry: {entry_price} | Exit: {exit_price}
PnL: {pnl_usd} USD | Risk: {risk_usd or 'N/A'} USD | R: {r_mult or 'N/A'}
Entry reason: {entry_reason}
Lessons: {lessons}
Mistakes: {mistakes}
Note: {note}
"""
    return _call_openai(prompt, user, max_tokens=500, reason="trade_postmortem")


def tp_reach_diagnosis_from_context(tp_reach: dict) -> str | None:
    """
    Gọi OpenAI phân tích: hầu hết long đóng có lãi nhưng không đạt TP — nguyên nhân chính xác là TP quá cao
    vô lý hay không đủ thời gian chờ? Trả về đoạn phân tích ngắn + gợi ý hành động (chuyên gia).
    """
    if not tp_reach or tp_reach.get("count", 0) == 0:
        return None
    system = """You are a trading expert. In 1 short paragraph, diagnose why many long trades close in profit
but never hit Take Profit (TP): is the main cause (1) TP set too high / unrealistic for the actual price range,
or (2) not enough time (positions closed too early by trailing SL / proactive close before price could reach TP)?
Then in 1-2 bullet points suggest concrete actions (e.g. lower TP %, use ATR-based TP, delay when to move SL up, or increase hold time).
Reply in Vietnamese, concise."""
    user = f"""Số liệu 30 ngày:
- Số lệnh long đóng có lãi nhưng KHÔNG đóng do chạm TP: {tp_reach.get('count', 0)}
- Thời gian giữ trung bình: {tp_reach.get('avg_hold_min')} phút
- TP tại entry trung bình: {tp_reach.get('avg_tp_pct')}% từ entry
- Lời thực tế trung bình khi đóng: {tp_reach.get('avg_actual_pct')}%

Diagnosis đã có (rule-based): {tp_reach.get('diagnosis', '')}
Gợi ý (rule-based): {tp_reach.get('suggestion', '')}

Hãy đưa ra phân tích chuyên gia ngắn (nguyên nhân chính xác) và gợi ý hành động cụ thể."""
    return _call_openai(system, user, max_tokens=600, reason="tp_reach_diagnosis")
