"""
Quyết định cập nhật TP/SL khi có vị thế và xuất hiện hình nến đã học (classic hoặc từ AI).
Logic thông minh: giới hạn TP theo ATR (biến động thực tế) và cấu trúc giá (đỉnh gần), có thể học từ lịch sử lệnh.
Trả về (new_sl, new_tp, reason) hoặc None (không đổi).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings

logger = logging.getLogger(__name__)

# document/request: AI không gọi mỗi cycle — cooldown 10 phút per (symbol, side).
_LAST_AI_SL_TP_CALL: dict[tuple[str, str], float] = {}
AI_SL_TP_COOLDOWN_SECONDS = 600  # 10 phút

# v6: chỉ gọi AI khi thật sự gần SL/TP, lỗ sâu, hoặc reversal cao — không dùng "inside_risk_buffer" mặc định.
AI_TRIGGER_NEAR_SL_PCT = 0.8
AI_TRIGGER_NEAR_TP_PCT = 1.5
AI_TRIGGER_DEEP_LOSS_PCT = -2.0  # % PnL unrealized
AI_TRIGGER_REVERSAL_MIN = 0.65

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_candle(c: object) -> tuple[float, float, float, float]:
    o = getattr(c, "open", None) or (c.get("open") if isinstance(c, dict) else None)
    h = getattr(c, "high", None) or (c.get("high") if isinstance(c, dict) else None)
    l = getattr(c, "low", None) or (c.get("low") if isinstance(c, dict) else None)
    cl = getattr(c, "close", None) or (c.get("close") if isinstance(c, dict) else None)
    return (float(o), float(h), float(l), float(cl))


def _atr(candles: list, period: int = 14) -> float | None:
    """ATR(period) từ danh sách nến. Dùng biến động thực tế để giới hạn TP/SL hợp lý."""
    if not candles or len(candles) < 2 or period < 1:
        return None
    tr_list = []
    prev_close = None
    for i, c in enumerate(candles):
        o, h, low, cl = _to_candle(c)
        if prev_close is None:
            tr = h - low if low < h else 0.0
        else:
            tr = max(h - low, abs(h - prev_close), abs(low - prev_close))
        tr_list.append(tr)
        prev_close = cl
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else None
    return sum(tr_list[-period:]) / period


def _recent_high(candles: list, lookback: int = 10) -> float | None:
    """Đỉnh cao nhất trong lookback nến gần nhất — cấu trúc giá."""
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    highs = [_to_candle(c)[1] for c in subset]
    return max(highs) if highs else None


def _recent_low(candles: list, lookback: int = 10) -> float | None:
    """Đáy thấp nhất trong lookback nến gần nhất."""
    if not candles or lookback < 1:
        return None
    subset = candles[-lookback:]
    lows = [_to_candle(c)[2] for c in subset]
    return min(lows) if lows else None


def _suggest_lock_profit_sl(
    position_side: str,
    entry_price: float,
    quantity: float,
    current_sl: float | None,
    current_price: float,
    min_profit_usd: float,
    buffer_pct: float,
) -> tuple[float, str] | None:
    """
    Chiến thuật **chốt lãi an toàn (Lock profit / Securing profit)**:
    Khi lãi chưa chốt >= min_profit_usd, gợi ý kéo SL lên (long) hoặc xuống (short)
    để nếu giá đảo chiều thì vẫn đóng với lãi tối thiểu đó — không bị âm vốn.
    Trả về (new_sl, reason) hoặc None nếu không áp dụng.
    """
    if quantity is None or quantity <= 0 or min_profit_usd <= 0:
        return None
    if position_side == "long":
        pnl_usd = (current_price - entry_price) * quantity
        if pnl_usd < min_profit_usd:
            return None
        # SL mới = entry + (min_profit_usd / quantity) để nếu chạm SL thì lãi ≈ min_profit_usd
        sl_lock = entry_price + min_profit_usd / quantity
        sl_min = entry_price * (1.0 + buffer_pct)
        new_sl = max(sl_lock, sl_min)
        if new_sl >= current_price:
            return None
        if current_sl is not None and new_sl <= current_sl:
            return None
        return (round(new_sl, 6), f"chốt lãi an toàn (lãi ~{pnl_usd:.0f} USD, SL bảo vệ ~{min_profit_usd:.0f} USD)")
    else:
        pnl_usd = (entry_price - current_price) * quantity
        if pnl_usd < min_profit_usd:
            return None
        sl_lock = entry_price - min_profit_usd / quantity
        sl_max = entry_price * (1.0 - buffer_pct)
        new_sl = min(sl_lock, sl_max)
        if new_sl <= current_price:
            return None
        if current_sl is not None and new_sl >= current_sl:
            return None
        return (round(new_sl, 6), f"chốt lãi an toàn (lãi ~{pnl_usd:.0f} USD, SL bảo vệ ~{min_profit_usd:.0f} USD)")


def get_learned_max_tp_pct(
    db: Session,
    portfolio_id: int,
    symbol: str | None = None,
    side: str | None = None,
    percentile: float = 75.0,
    min_trades: int = 5,
) -> float | None:
    """
    Học từ lịch sử lệnh đã đóng: phần trăm lợi nhuận (TP đã chạm) thực tế.
    Trả về percentile (vd. 75%) của (exit-entry)/entry cho long — dùng làm giới hạn TP hợp lý (xác suất).
    Nếu ít hơn min_trades lệnh đóng thì trả về None (chưa đủ dữ liệu).
    """
    try:
        from sqlalchemy import select
        from core.portfolio.models import Trade, Position
    except Exception:
        return None
    q = (
        select(Trade, Position)
        .where(
            Trade.action == "close",
            Trade.position_id == Position.id,
            Trade.portfolio_id == portfolio_id,
        )
    )
    if symbol:
        q = q.where(Trade.symbol == symbol)
    if side:
        q = q.where(Trade.side == side)
    rows = list(db.execute(q).all())
    pcts = []
    for (t, pos) in rows:
        entry = getattr(pos, "entry_price", None) or getattr(pos, "entry", None)
        if entry is None or entry <= 0:
            continue
        exit_p = t.price
        if t.side == "long":
            pct = (exit_p - entry) / entry
        else:
            pct = (entry - exit_p) / entry
        pcts.append(pct)
    if len(pcts) < min_trades:
        return None
    pcts.sort()
    idx = min(int(len(pcts) * percentile / 100.0), len(pcts) - 1)
    return pcts[idx]


def rule_based_suggest(
    position_side: str,
    entry_price: float,
    current_sl: float | None,
    current_tp: float | None,
    candles: list,
    patterns: list[str],
    current_price: float,
    learned_max_tp_pct: float | None = None,
) -> tuple[float | None, float | None, str] | None:
    """
    Rule-based: từ pattern đã học đưa ra new_sl, new_tp (có thể chỉ một trong hai).
    Trả về (new_sl, new_tp, reason) hoặc None nếu không đổi.
    """
    if not candles:
        return None
    last = candles[-1]
    o, h, low, c = _to_candle(last)
    new_sl, new_tp = current_sl, current_tp
    reason = ""

    # Long: hammer hoặc rejection_low -> chuyển SL gần breakeven nhưng chừa buffer (0.1%) tránh bị kích hoạt bởi spread/nhiễu
    if position_side == "long":
        if "hammer" in patterns or "rejection_low" in patterns:
            if current_sl is not None and current_price > entry_price:
                buffer_pct = 0.001  # 0.1% dưới entry
                new_sl = round(entry_price * (1.0 - buffer_pct), 6)
                if new_sl < current_price:
                    reason = "hammer/rejection_low, SL gần breakeven (buffer 0.1%)"
                    return (new_sl, new_tp, reason)
        if "engulfing_bear" in patterns and current_sl is not None:
            # Thắt chặt SL: đặt dưới đáy nến; không đặt quá sát giá hiện tại (tối thiểu 0.2% dưới price)
            candidate_sl = low * 0.998 if low > 0 else current_sl
            min_distance = current_price * 0.002  # 0.2%
            if candidate_sl > current_sl and candidate_sl < current_price - min_distance:
                new_sl = round(candidate_sl, 6)
                reason = "engulfing_bear, thắt chặt SL dưới đáy nến"
                return (new_sl, new_tp, reason)
        if "big_body_bull" in patterns and current_tp is not None and current_price > entry_price:
            if c > current_tp * 0.98:
                extra = (c - entry_price) * 0.5
                candidate_tp = round(entry_price + extra + (current_tp - entry_price) * 0.3, 6)
                # Logic chuyên gia: giới hạn TP theo ATR (biến động thực tế) và cấu trúc (đỉnh gần)
                # — không phải config tùy ý, mà dữ liệu thị trường.
                atr_val = _atr(candles, 14)
                recent_h = _recent_high(candles, 10)
                cap_tp = None
                if atr_val is not None and atr_val > 0:
                    # TP không xa hơn 2.5 ATR từ entry (kinh nghiệm: xác suất chạm hợp lý)
                    cap_atr = entry_price + 2.5 * atr_val
                    cap_tp = cap_atr if cap_tp is None else min(cap_tp, cap_atr)
                if recent_h is not None and recent_h > entry_price:
                    # TP không vượt quá đỉnh gần + 1% (cấu trúc kháng cự)
                    cap_structure = recent_h * 1.01
                    cap_tp = cap_structure if cap_tp is None else min(cap_tp, cap_structure)
                if learned_max_tp_pct is not None and learned_max_tp_pct > 0 and position_side == "long":
                    cap_learned = entry_price * (1.0 + learned_max_tp_pct)
                    cap_tp = cap_learned if cap_tp is None else min(cap_tp, cap_learned)
                if cap_tp is not None and candidate_tp > cap_tp:
                    candidate_tp = round(cap_tp, 6)
                    reason = "big_body_bull, nới TP theo đà (giới hạn ATR + cấu trúc + bài học lệnh)"
                else:
                    reason = "big_body_bull, nới TP theo đà"
                if candidate_tp > current_tp:
                    new_tp = candidate_tp
                    return (new_sl, new_tp, reason)

    # Short: shooting_star/rejection_high -> SL gần breakeven, chừa buffer 0.1% tránh spread/nhiễu
    if position_side == "short":
        if "shooting_star" in patterns or "rejection_high" in patterns:
            if current_sl is not None and current_price < entry_price:
                buffer_pct = 0.001
                new_sl = round(entry_price * (1.0 + buffer_pct), 6)
                if new_sl > current_price:
                    reason = "shooting_star/rejection_high, SL gần breakeven (buffer 0.1%)"
                    return (new_sl, new_tp, reason)
        if "engulfing_bull" in patterns and current_sl is not None:
            candidate_sl = h * 1.002 if h > 0 else current_sl
            min_distance = current_price * 0.002  # 0.2%
            if candidate_sl < current_sl and candidate_sl > current_price + min_distance:
                new_sl = round(candidate_sl, 6)
                reason = "engulfing_bull, thắt chặt SL trên đỉnh nến"
                return (new_sl, new_tp, reason)

    return None


def _call_ai_sl_tp(
    position_side: str,
    entry_price: float,
    current_sl: float | None,
    current_tp: float | None,
    patterns: list[str],
    last_ohlc: tuple[float, float, float, float],
    current_price: float,
    symbol_key: tuple[str, str] | None = None,
    position_age_sec: float | None = None,
    min_age_sec_initial_review: float = 300.0,
    pnl_pct_unrealized: float | None = None,
    reversal_exit_score: float | None = None,
) -> tuple[float | None, float | None, str] | None:
    """Gọi OpenAI để gợi ý cập nhật TP/SL; parse response thành (new_sl, new_tp, reason). min_age_sec_initial_review: dưới ngưỡng này (giây) coi là new_position_initial_review (mặc định 300 = 5 phút; high vol có thể truyền 180)."""
    if not getattr(settings, "openai_api_key", None) or not settings.openai_api_key.strip():
        return None
    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "sl_tp_update_from_pattern.md"
    if not prompt_path.exists():
        return None
    symbol, side = (symbol_key[0], symbol_key[1]) if symbol_key else ("?", "?")
    # Khoảng cách % tới SL/TP để log và chọn reason rõ ràng
    distance_to_sl_pct = None
    distance_to_tp_pct = None
    if current_price and current_price > 0:
        if current_sl is not None:
            distance_to_sl_pct = abs(current_price - current_sl) / current_price * 100
        if current_tp is not None:
            distance_to_tp_pct = abs(current_tp - current_price) / current_price * 100
    # Chỉ gọi AI khi: review sớm | gần SL | gần TP | lỗ sâu | reversal cao
    threshold_sec = max(60.0, float(min_age_sec_initial_review))
    ai_reason: str | None = None
    if position_age_sec is not None and position_age_sec < threshold_sec:
        ai_reason = "new_position_initial_review"
    elif distance_to_sl_pct is not None and distance_to_sl_pct <= AI_TRIGGER_NEAR_SL_PCT:
        ai_reason = "near_sl"
    elif distance_to_tp_pct is not None and distance_to_tp_pct <= AI_TRIGGER_NEAR_TP_PCT:
        ai_reason = "near_tp"
    elif pnl_pct_unrealized is not None and pnl_pct_unrealized <= AI_TRIGGER_DEEP_LOSS_PCT:
        ai_reason = "deep_loss_review"
    elif reversal_exit_score is not None and reversal_exit_score >= AI_TRIGGER_REVERSAL_MIN:
        ai_reason = "reversal_score_high"
    if ai_reason is None:
        return None
    logger.info(
        "AI_CALL reason=%s symbol=%s side=%s distance_to_sl_pct=%s distance_to_tp_pct=%s position_age_sec=%s",
        ai_reason, symbol, (side or position_side),
        f"{distance_to_sl_pct:.2f}" if distance_to_sl_pct is not None else "n/a",
        f"{distance_to_tp_pct:.2f}" if distance_to_tp_pct is not None else "n/a",
        int(position_age_sec) if position_age_sec is not None else "n/a",
    )
    system_prompt = prompt_path.read_text(encoding="utf-8")
    o, h, low, c = last_ohlc
    user = f"""Position: {position_side} | entry={entry_price} | SL={current_sl} | TP={current_tp}
Current price: {current_price}
Patterns: {', '.join(patterns)}
Last candle: O={o} H={h} L={low} C={c}
Trả về đúng một dòng ACTION theo format trong prompt."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key.strip())
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
            ],
            max_tokens=150,
        )
        text = (r.choices and r.choices[0].message and r.choices[0].message.content or "").strip().upper()
    except Exception:
        return None
    if not text or "NO_CHANGE" in text:
        return None
    new_sl, new_tp = current_sl, current_tp
    reason = "AI"
    if "MOVE_SL_BREAKEVEN" in text:
        new_sl = round(entry_price, 6)
        return (new_sl, new_tp, "AI: chuyển SL breakeven")
    if "TIGHTEN_SL:" in text:
        try:
            part = text.split("TIGHTEN_SL:")[1].strip().split()[0].replace(",", ".")
            new_sl = round(float(part), 6)
            return (new_sl, new_tp, "AI: thắt chặt SL")
        except Exception:
            pass
    if "TRAIL_TP:" in text:
        try:
            part = text.split("TRAIL_TP:")[1].strip().split()[0].replace(",", ".")
            new_tp = round(float(part), 6)
            return (new_sl, new_tp, "AI: nới TP")
        except Exception:
            pass
    if "UPDATE_BOTH:" in text:
        try:
            rest = text.split("UPDATE_BOTH:")[1].strip()
            for tok in rest.replace(",", " ").split():
                if "SL=" in tok:
                    new_sl = round(float(tok.split("=")[1]), 6)
                if "TP=" in tok:
                    new_tp = round(float(tok.split("=")[1]), 6)
            return (new_sl, new_tp, "AI: cập nhật SL và TP")
        except Exception:
            pass
    return None


def _cap_tp_for_short_term(
    position_side: str,
    new_sl: float | None,
    new_tp: float | None,
    current_price: float,
    reason: str,
) -> tuple[float | None, float | None, str]:
    """Áp dụng giới hạn TP theo giá hiện tại khi trading_style ngắn hạn (max_tp_pct_above_current > 0)."""
    pct = max(0.0, float(getattr(settings, "max_tp_pct_above_current", 0) or 0))
    if pct <= 0 or new_tp is None:
        return (new_sl, new_tp, reason)
    if position_side == "long" and new_tp > current_price * (1.0 + pct):
        new_tp = round(current_price * (1.0 + pct), 6)
        reason = (reason or "") + " (cap TP ngắn hạn)"
    elif position_side == "short" and new_tp < current_price * (1.0 - pct):
        new_tp = round(current_price * (1.0 - pct), 6)
        reason = (reason or "") + " (cap TP ngắn hạn)"
    return (new_sl, new_tp, reason)


def suggest_sl_tp_update(
    position_side: str,
    entry_price: float,
    current_sl: float | None,
    current_tp: float | None,
    candles: list,
    patterns: list[str],
    current_price: float,
    use_ai: bool = True,
    learned_max_tp_pct: float | None = None,
    quantity: float | None = None,
    symbol_key: tuple[str, str] | None = None,
    position_age_sec: float | None = None,
    min_age_sec_initial_review: float | None = None,
    pnl_pct_unrealized: float | None = None,
    reversal_exit_score: float | None = None,
) -> tuple[float | None, float | None, str] | None:
    """
    Gợi ý cập nhật SL/TP. Thứ tự ưu tiên: (1) Chốt lãi an toàn khi lãi >= lock_profit_min_usd;
    (2) AI nếu prefer_ai_sl_tp (có cooldown 10 phút per symbol_key — document/request); (3) rule theo hình nến.
    symbol_key: (symbol, side) để áp cooldown AI, tránh gọi OpenAI mỗi cycle.
    """
    min_profit_usd = max(0.0, float(getattr(settings, "lock_profit_min_usd", 0) or 0))
    buffer_pct = max(0.0, float(getattr(settings, "lock_profit_buffer_pct", 0.002) or 0.002))
    if quantity and quantity > 0 and min_profit_usd > 0:
        lock_suggestion = _suggest_lock_profit_sl(
            position_side, entry_price, quantity, current_sl, current_price, min_profit_usd, buffer_pct
        )
        if lock_suggestion:
            new_sl, reason = lock_suggestion
            out = (new_sl, current_tp, reason)
            return _cap_tp_for_short_term(position_side, out[0], out[1], current_price, out[2])
    suggestion = None
    prefer_ai = bool(getattr(settings, "prefer_ai_sl_tp", True))
    now_mono = time.monotonic()
    ai_allowed = use_ai and candles
    if symbol_key and ai_allowed:
        last_call = _LAST_AI_SL_TP_CALL.get(symbol_key, 0)
        if now_mono - last_call < AI_SL_TP_COOLDOWN_SECONDS:
            ai_allowed = False
    _initial_review_sec = 300.0 if min_age_sec_initial_review is None else max(60.0, float(min_age_sec_initial_review))
    if ai_allowed and prefer_ai:
        last = candles[-1]
        o, h, low, c = _to_candle(last)
        suggestion = _call_ai_sl_tp(
            position_side, entry_price, current_sl, current_tp, patterns, (o, h, low, c), current_price,
            symbol_key=symbol_key,
            position_age_sec=position_age_sec,
            min_age_sec_initial_review=_initial_review_sec,
            pnl_pct_unrealized=pnl_pct_unrealized,
            reversal_exit_score=reversal_exit_score,
        )
        if symbol_key and suggestion is not None:
            _LAST_AI_SL_TP_CALL[symbol_key] = now_mono
    if suggestion is None:
        suggestion = rule_based_suggest(
            position_side, entry_price, current_sl, current_tp, candles, patterns, current_price,
            learned_max_tp_pct=learned_max_tp_pct,
        )
    if suggestion is None and ai_allowed and not prefer_ai:
        last = candles[-1]
        o, h, low, c = _to_candle(last)
        suggestion = _call_ai_sl_tp(
            position_side, entry_price, current_sl, current_tp, patterns, (o, h, low, c), current_price,
            symbol_key=symbol_key,
            position_age_sec=position_age_sec,
            min_age_sec_initial_review=_initial_review_sec,
            pnl_pct_unrealized=pnl_pct_unrealized,
            reversal_exit_score=reversal_exit_score,
        )
        if symbol_key and suggestion is not None:
            _LAST_AI_SL_TP_CALL[symbol_key] = now_mono
    if suggestion is None:
        return None
    new_sl, new_tp, reason = suggestion
    new_sl, new_tp, reason = _cap_tp_for_short_term(position_side, new_sl, new_tp, current_price, reason)
    return (new_sl, new_tp, reason)
