"""
Phân tích tình huống thị trường (capitulation, structure, bounce, vùng vào lệnh).
Gọi AI khi: dashboard "Phân tích tình huống", hoặc worker khi nến 1h đặc biệt.
Đầu vào: symbol, nến 1h (OHLCV), giá hiện tại, vị thế đang mở (nếu có).
Đầu ra: tình huống, xác suất, kế hoạch rẽ nhánh (có/không có lệnh), mức giá cần theo dõi.
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


def _normalize_openai_key(raw: str) -> str:
    """Chỉ giữ key đầu tiên nếu bị dán hai lần (hai chuỗi sk-proj-)."""
    k = (raw or "").strip()
    if not k or k.count("sk-proj-") < 2:
        return k
    parts = k.split("sk-proj-", 2)
    return (parts[0] or "") + "sk-proj-" + (parts[1] or "")


def _call_openai(system_prompt: str, user_content: str, max_tokens: int = 2000) -> str | None:
    """Trả về nội dung AI, hoặc None nếu không có key, hoặc chuỗi 'ERROR: ...' nếu có key nhưng gọi API lỗi."""
    raw = getattr(settings, "openai_api_key", None) or ""
    key = _normalize_openai_key(raw)
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
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
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {str(e)}"
    return None


def _kline_repr(k) -> tuple:
    """Lấy O,H,L,C,volume,body_pct từ Kline1h (hoặc dict). Tránh gọi .get() trên Kline1h."""
    o = getattr(k, "open", None) if not isinstance(k, dict) else k.get("open")
    h = getattr(k, "high", None) if not isinstance(k, dict) else k.get("high")
    l_ = getattr(k, "low", None) if not isinstance(k, dict) else k.get("low")
    c = getattr(k, "close", None) if not isinstance(k, dict) else k.get("close")
    vol = getattr(k, "volume", 0) if not isinstance(k, dict) else k.get("volume", 0)
    body_pct = abs(c - o) / o * 100 if o and o > 0 else 0
    return o, h, l_, c, vol, body_pct


def build_situation_context(
    symbol: str,
    klines_1h: list,
    quote_price: float,
    quote_pct_24h: float,
    quote_volume_24h: float,
    positions_for_symbol: list[dict],
    extra_notes: str = "",
    klines_5m: list | None = None,
) -> str:
    """Tạo đoạn mô tả đầu vào cho AI (nến 1h, nến 5m nếu có, giá, volume, vị thế, ghi chú)."""
    parts = [f"**Symbol:** {symbol}", f"**Giá hiện tại:** {quote_price}", f"**% thay đổi 24h:** {quote_pct_24h:.2f}%", f"**Volume 24h (quote):** {quote_volume_24h:,.0f}"]
    if klines_1h:
        last = klines_1h[-1]
        o, h, l_, c, vol, body_pct = _kline_repr(last)
        parts.append(f"**Nến 1h gần nhất:** O={o} H={h} L={l_} C={c} | Volume={vol:,.0f} | Body%={body_pct:.1f}%")
        if len(klines_1h) >= 2:
            prev = klines_1h[-2]
            prev_vol = getattr(prev, "volume", 0) if not isinstance(prev, dict) else prev.get("volume", 0)
            if prev_vol and prev_vol > 0:
                vol_ratio = vol / prev_vol
                parts.append(f"**Volume nến 1h này so với nến trước:** {vol_ratio:.1f}x")
    if klines_5m:
        last5 = klines_5m[-1]
        o5, h5, l5, c5, vol5, body5 = _kline_repr(last5)
        parts.append(f"**Nến 5m gần nhất:** O={o5} H={h5} L={l5} C={c5} | Volume={vol5:,.0f} | Body%={body5:.1f}%")
        if len(klines_5m) >= 2:
            prev5 = klines_5m[-2]
            _, _, _, _, prev_vol5, _ = _kline_repr(prev5)
            if prev_vol5 and prev_vol5 > 0:
                parts.append(f"**Volume nến 5m so với nến trước:** {vol5 / prev_vol5:.1f}x")
    parts.append("**Vị thế đang mở:**")
    if not positions_for_symbol:
        parts.append("- Không có vị thế.")
    else:
        for p in positions_for_symbol:
            parts.append(f"- {p.get('side', 'long')} @ entry {p.get('entry_price')} | SL {p.get('stop_loss')} | TP {p.get('take_profit')} | strategy {p.get('strategy_name', '')}")
    if extra_notes:
        parts.append("**Ghi chú / cấu trúc / MA (người dùng):**")
        parts.append(extra_notes)
    return "\n".join(parts)


def analyze_market_situation(
    symbol: str,
    klines_1h: list | None = None,
    quote_price: float | None = None,
    quote_pct_24h: float = 0.0,
    quote_volume_24h: float = 0.0,
    positions_for_symbol: list[dict] | None = None,
    extra_notes: str = "",
    klines_5m: list | None = None,
    reason: str = "manual_force",
) -> str | None:
    """
    Gọi AI phân tích tình huống. Trả về markdown (tình huống, xác suất, kế hoạch rẽ nhánh) hoặc None nếu không có key/lỗi.
    reason: hourly_situation | auto_situation | manual_dashboard | manual_force (để log AI_CALL).
    """
    if positions_for_symbol is None:
        positions_for_symbol = []
    if quote_price is None:
        quote_price = 0.0
    if klines_1h is None:
        klines_1h = []
    if klines_5m is None:
        klines_5m = []
    prompt = _load_prompt("market_structure_situation")
    if not prompt:
        return None
    context = build_situation_context(
        symbol=symbol,
        klines_1h=klines_1h,
        quote_price=quote_price,
        quote_pct_24h=quote_pct_24h,
        quote_volume_24h=quote_volume_24h,
        positions_for_symbol=positions_for_symbol,
        extra_notes=extra_notes,
        klines_5m=klines_5m or None,
    )
    logger.info("AI_CALL reason=%s symbol=%s", reason, symbol)
    return _call_openai(prompt, context, max_tokens=2000)
