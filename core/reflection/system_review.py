"""
Quét logic toàn hệ thống mỗi 10 phút: tự đánh giá từ nhiều góc nhìn chuyên gia (risk, edge, execution, behavioral),
tìm điểm yếu và gợi ý cải thiện để nâng dần "sự thông minh" của Trading Lab.
"""
from __future__ import annotations

from datetime import datetime, date, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.portfolio.models import Portfolio, Position, Trade
from core.reflection.engine import repeated_mistakes
from core.reflection.learn_from_history import learn_from_closed_trades


def _gather_context(db: Session, portfolio_id: int | None = None) -> dict[str, Any]:
    """Thu thập ngữ cảnh hiện tại: config, metrics, học từ lịch sử, journal mistakes, vị thế mở."""
    from core.analytics.metrics import compute_metrics

    portfolio_id = portfolio_id or db.scalar(select(Portfolio.id).where(Portfolio.name == "Paper Portfolio"))
    metrics = compute_metrics(db, portfolio_id=portfolio_id) if portfolio_id else {}
    learned = learn_from_closed_trades(db, portfolio_id=portfolio_id, last_n_days=14, min_trades_per_group=2)
    mistakes = repeated_mistakes(db, top_n=8)

    today_start = datetime.combine(date.today(), time.min)
    today_end = today_start + timedelta(days=1)
    closed_today = list(db.scalars(
        select(Trade).where(
            Trade.action == "close",
            Trade.created_at >= today_start,
            Trade.created_at < today_end,
        )
    ))
    if portfolio_id:
        closed_today = [t for t in closed_today if t.portfolio_id == portfolio_id]
    daily_realized = round(sum(t.pnl_usd for t in closed_today), 2)

    open_positions = list(db.scalars(
        select(Position).where(Position.is_open == True)
    ))
    if portfolio_id:
        open_positions = [p for p in open_positions if p.portfolio_id == portfolio_id]
    open_count = len(open_positions)

    config_snapshot = {
        "max_concurrent_trades": getattr(settings, "max_concurrent_trades", 3),
        "max_daily_loss_pct": getattr(settings, "max_daily_loss_pct", 0.03),
        "default_capital_usd": getattr(settings, "default_capital_usd", 1000),
        "cycle_interval_seconds": getattr(settings, "cycle_interval_seconds", 10),
        "max_hold_hours": getattr(settings, "max_hold_hours", 0),
        "prefer_ai_sl_tp": getattr(settings, "prefer_ai_sl_tp", True),
        "trading_style": getattr(settings, "trading_style", "swing"),
    }

    return {
        "at": datetime.utcnow().isoformat(),
        "config": config_snapshot,
        "metrics": metrics,
        "learned_warnings": learned.get("warnings", []),
        "repeated_mistakes": [{"text": t, "count": c} for t, c in mistakes],
        "daily_realized_pnl": daily_realized,
        "open_positions_count": open_count,
        "closed_today_count": len(closed_today),
    }


def _perspective_risk(ctx: dict[str, Any]) -> dict[str, Any]:
    """Góc nhìn chuyên gia risk: giới hạn lỗ, số lệnh, concentration."""
    findings = []
    suggestions = []
    daily = ctx.get("daily_realized_pnl", 0) or 0
    cap = ctx.get("config", {}).get("default_capital_usd", 1000) or 1000
    max_loss_pct = ctx.get("config", {}).get("max_daily_loss_pct", 0.03) or 0.03
    limit_loss = -cap * max_loss_pct
    open_count = ctx.get("open_positions_count", 0)
    max_trades = ctx.get("config", {}).get("max_concurrent_trades", 3)

    if daily <= limit_loss:
        findings.append(f"Daily realized PnL ({daily:.2f}) đã chạm hoặc vượt giới hạn lỗ trong ngày ({limit_loss:.2f}).")
        suggestions.append("Hệ thống sẽ từ chối lệnh mới cho đến ngày mai; kiểm tra nguyên nhân lỗ hàng loạt.")
    elif daily < 0 and limit_loss < 0 and daily < limit_loss * 0.8:
        findings.append(f"Lỗ trong ngày ({daily:.2f}) gần giới hạn ({limit_loss:.2f}).")
        suggestions.append("Cân nhắc giảm size hoặc tạm dừng vào lệnh mới nếu chất lượng setup kém.")

    if open_count >= max_trades:
        findings.append(f"Số lệnh đang mở ({open_count}) bằng max_concurrent_trades ({max_trades}).")
        suggestions.append("Không mở thêm lệnh cho đến khi có vị thế đóng; đảm bảo TP/SL hợp lý để tránh kẹt.")

    if not findings:
        findings.append("Risk trong giới hạn: lỗ trong ngày và số lệnh mở chưa chạm ngưỡng.")
    return {"name": "Risk", "findings": findings, "suggestions": suggestions}


def _perspective_edge(ctx: dict[str, Any]) -> dict[str, Any]:
    """Góc nhìn chuyên gia edge / thống kê: win rate, profit factor, combo thua, SL quá nhanh."""
    findings = []
    suggestions = []
    metrics = ctx.get("metrics", {})
    win_rate = metrics.get("win_rate", 0) or 0
    pf = metrics.get("profit_factor", 0) or 0
    total_trades = metrics.get("total_trades", 0) or 0
    warnings = ctx.get("learned_warnings", [])

    if total_trades >= 5:
        if win_rate < 0.4:
            findings.append(f"Win rate thấp ({win_rate:.0%}) với {total_trades} lệnh đóng.")
            suggestions.append("Xem lại điều kiện vào lệnh và TP/SL; cân nhắc ưu tiên AI (prefer_ai_sl_tp) hoặc thu hẹp strategy.")
        if pf < 1.0 and total_trades >= 10:
            findings.append(f"Profit factor < 1 ({pf:.2f}) — lỗ trung bình lớn hơn lãi.")
            suggestions.append("Cải thiện risk/reward hoặc giảm tần suất vào lệnh kém chất lượng.")

    for w in warnings:
        findings.append(f"[Học từ lịch sử] {w.get('message', w)}")
        suggestions.append("Ưu tiên điều chỉnh SL/TP hoặc tạm tránh combo đang thua/SL quá nhanh.")

    if not findings:
        findings.append("Chưa đủ dữ liệu hoặc edge ổn: win rate và profit factor chưa báo động.")
    return {"name": "Edge / Thống kê", "findings": findings, "suggestions": suggestions}


def _perspective_behavioral(ctx: dict[str, Any]) -> dict[str, Any]:
    """Góc nhìn chuyên gia hành vi: lỗi lặp lại, kỷ luật."""
    findings = []
    suggestions = []
    mistakes = ctx.get("repeated_mistakes", [])

    for m in mistakes[:5]:
        text = m.get("text", "")
        count = m.get("count", 0)
        if count >= 2:
            findings.append(f"Lỗi lặp: '{text}' ({count} lần).")
            suggestions.append("Ghi nhận vào quy tắc vận hành: tránh lặp lại; có thể thêm rule hoặc cap (vd. max_tp_pct_above_current) để hạn chế.")

    if not findings:
        findings.append("Chưa có lỗi lặp đáng kể trong journal.")
    return {"name": "Hành vi / Kỷ luật", "findings": findings, "suggestions": suggestions}


def _perspective_execution_and_ops(ctx: dict[str, Any]) -> dict[str, Any]:
    """Góc nhìn vận hành: cycle, đồng bộ, cấu hình."""
    findings = []
    suggestions = []
    config = ctx.get("config", {})
    interval = config.get("cycle_interval_seconds", 10)
    max_hold = config.get("max_hold_hours", 0)
    prefer_ai = config.get("prefer_ai_sl_tp", True)

    if interval < 30:
        findings.append(f"Cycle chạy mỗi {interval}s — dễ job skip nếu cycle > {interval}s.")
        suggestions.append("Cân nhắc CYCLE_INTERVAL_SECONDS >= 30 để tránh skip và giảm tải API.")
    if max_hold <= 0:
        findings.append("max_hold_hours = 0: không giới hạn thời gian giữ lệnh.")
        suggestions.append("Nếu đánh trong ngày, đặt MAX_HOLD_HOURS (vd. 4 hoặc 8).")
    if not prefer_ai:
        findings.append("prefer_ai_sl_tp = False: rule cứng ưu tiên hơn gợi ý AI.")
        suggestions.append("Bật PREFER_AI_SL_TP=true để tôn trọng gợi ý AI khi cập nhật TP/SL.")

    if not findings:
        findings.append("Cấu hình vận hành ổn: interval và prefer_ai phù hợp.")
    return {"name": "Vận hành / Execution", "findings": findings, "suggestions": suggestions}


def _ai_synthesis(ctx: dict[str, Any], perspectives: list[dict]) -> str | None:
    """Gọi AI tổng hợp: từ context + findings của các góc nhìn, đưa ra 3–5 cải thiện cụ thể."""
    if not getattr(settings, "openai_api_key", None) or not (settings.openai_api_key or "").strip():
        return None
    import json
    import logging
    logging.getLogger(__name__).info("AI_CALL reason=daily_review (system_review synthesis)")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key.strip())
        summary = []
        for p in perspectives:
            summary.append(f"[{p['name']}] Findings: {'; '.join(p['findings'][:3])}. Suggestions: {'; '.join(p['suggestions'][:2])}.")
        user = f"""Hệ thống trading tự động (Trading Lab). Context: {json.dumps({k: v for k, v in ctx.items() if k != 'at'}, default=str)[:1500]}.
Các góc nhìn chuyên gia đã chạy:
{chr(10).join(summary)}

Nhiệm vụ: với tư cách chuyên gia trading/risk, đưa ra 3–5 cải thiện CỤ THỂ để hệ thống thông minh và chuyên nghiệp hơn (ngắn gọn, có thể làm trong code hoặc config). Chỉ trả về danh sách đánh số, không giải thích dài."""
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": user}],
            max_tokens=400,
        )
        text = (r.choices and r.choices[0].message and r.choices[0].message.content or "").strip()
        return text if text else None
    except Exception:
        return None


def run_system_review(db: Session, portfolio_name: str = "Paper Portfolio", use_ai_synthesis: bool = True) -> dict[str, Any]:
    """
    Quét logic toàn hệ thống: thu thập context, đánh giá từ 4 góc nhìn chuyên gia (risk, edge, behavioral, execution),
    tùy chọn gọi AI tổng hợp cải thiện. Trả về dict để log hoặc gửi báo cáo.
    """
    portfolio = db.scalar(select(Portfolio).where(Portfolio.name == portfolio_name))
    portfolio_id = portfolio.id if portfolio else None
    ctx = _gather_context(db, portfolio_id=portfolio_id)

    perspectives = [
        _perspective_risk(ctx),
        _perspective_edge(ctx),
        _perspective_behavioral(ctx),
        _perspective_execution_and_ops(ctx),
    ]
    ctx["perspectives"] = perspectives

    ai_text = None
    if use_ai_synthesis:
        ai_text = _ai_synthesis(ctx, perspectives)
    ctx["ai_synthesis"] = ai_text

    # Tóm tắt 1 đoạn
    parts = []
    for p in perspectives:
        if p["findings"] and ("thấp" in str(p["findings"]).lower() or "lỗ" in str(p["findings"]).lower() or "lặp" in str(p["findings"]).lower()):
            parts.append(f"{p['name']}: {p['findings'][0][:80]}")
    if ai_text:
        parts.append(f"AI: {ai_text[:120]}...")
    ctx["summary"] = " | ".join(parts) if parts else "Hệ thống ổn định; không phát hiện điểm yếu nghiêm trọng."

    return ctx
