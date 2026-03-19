"""
Phân tích điểm vào lệnh: vùng vào, xác suất diễn biến, tỷ lệ R.
Dùng để hiển thị trên dashboard và gửi Telegram khi có tín hiệu.
"""
from __future__ import annotations

from dataclasses import dataclass
from core.strategies.base import StrategySignal


@dataclass
class EntryAnalysis:
    symbol: str
    side: str
    strategy: str
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profit: float
    r_multiple: float
    prob_tp_pct: int
    prob_sideway_pct: int
    prob_sl_pct: int
    rationale: str


def build_entry_analysis_from_dict(data: dict, zone_pct: float = 0.005) -> EntryAnalysis:
    """Từ dict (từ cycle return) tạo EntryAnalysis."""
    entry = float(data["entry"]) if "entry" in data else float(data["entry_price"])
    sl = float(data["stop_loss"])
    tp = float(data["take_profit"])
    conf = float(data.get("confidence", 0.65))
    symbol = data["symbol"]
    side = data["side"]
    strategy = data.get("strategy") or data.get("strategy_name", "")
    rationale = data.get("rationale", "")
    zone = entry * zone_pct
    zone_low = entry - zone
    zone_high = entry + zone
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    r_multiple = round(reward / risk, 2) if risk > 0 else 0.0
    prob_tp = int(conf * 70)
    prob_sl = max(10, int((1 - conf) * 40))
    prob_sideway = max(0, 100 - prob_tp - prob_sl)
    return EntryAnalysis(
        symbol=symbol,
        side=side,
        strategy=strategy,
        entry_zone_low=round(zone_low, 6),
        entry_zone_high=round(zone_high, 6),
        stop_loss=sl,
        take_profit=tp,
        r_multiple=r_multiple,
        prob_tp_pct=prob_tp,
        prob_sideway_pct=prob_sideway,
        prob_sl_pct=prob_sl,
        rationale=rationale,
    )


def build_entry_analysis(signal: StrategySignal, zone_pct: float = 0.005) -> EntryAnalysis:
    """
    Từ signal tạo phân tích: vùng vào (entry ± zone_pct), R, xác suất heuristic.
    Xác suất lấy từ confidence: phân bố % hướng TP / sideway / SL.
    """
    entry = signal.entry_price
    sl = signal.stop_loss
    tp = signal.take_profit
    conf = signal.confidence
    # Vùng vào: entry ± 0.5%
    zone = entry * zone_pct
    if signal.side == "long":
        zone_low = entry - zone
        zone_high = entry + zone
    else:
        zone_low = entry - zone
        zone_high = entry + zone
    # R = khoảng cách TP / khoảng cách SL (đơn vị R)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    r_multiple = round(reward / risk, 2) if risk > 0 else 0.0
    # Heuristic xác suất: confidence -> % hướng TP, phần còn lại sideway + SL
    prob_tp = int(conf * 70)  # 0.7 -> 49%
    prob_sl = max(10, int((1 - conf) * 40))
    prob_sideway = 100 - prob_tp - prob_sl
    prob_sideway = max(0, prob_sideway)
    return EntryAnalysis(
        symbol=signal.symbol,
        side=signal.side,
        strategy=signal.strategy_name,
        entry_zone_low=round(zone_low, 6),
        entry_zone_high=round(zone_high, 6),
        stop_loss=sl,
        take_profit=tp,
        r_multiple=r_multiple,
        prob_tp_pct=prob_tp,
        prob_sideway_pct=prob_sideway,
        prob_sl_pct=prob_sl,
        rationale=signal.rationale,
    )


def format_telegram_alert(analysis: EntryAnalysis) -> str:
    """Tin nhắn Telegram: dấu hiệu theo kế hoạch, vùng vào, xác suất, tỷ lệ R."""
    side_upper = analysis.side.upper()
    return (
        f"📌 DẤU HIỆU VÀO LỆNH THEO KẾ HOẠCH\n\n"
        f"Vùng {side_upper}:\n"
        f"  Mã: {analysis.entry_zone_low} – {analysis.entry_zone_high}\n"
        f"  → {analysis.side} tốt\n\n"
        f"Xác suất diễn biến:\n"
        f"  • {analysis.prob_tp_pct}% hướng TP ({analysis.take_profit})\n"
        f"  • {analysis.prob_sideway_pct}% sideway\n"
        f"  • {analysis.prob_sl_pct}% chạm SL ({analysis.stop_loss})\n\n"
        f"Tỷ lệ R: {analysis.r_multiple}R | Strategy: {analysis.strategy}\n"
        f"Lý do: {analysis.rationale[:200]}\n\n"
        f"Thực hiện vào lệnh nếu phù hợp."
    )
