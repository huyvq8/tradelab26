"""
Tổng R trong ngày (lệnh đóng): R = pnl_usd / risk_usd mỗi lệnh.
Dùng chung cho Kill switch (cycle/worker) và Dashboard để không lệch số.
"""
from __future__ import annotations

# Bỏ qua lệnh có risk_usd quá nhỏ — nếu không, PnL/risk phình cực lớn (vd -2030 R) trong khi Dashboard hiển thị -4.79 R.
MIN_RISK_USD_FOR_R_AGGREGATION = 0.01


def sum_daily_realized_r_from_trades(trades) -> float:
    """Cộng R từ danh sách Trade (thường là close trong ngày)."""
    return sum(
        float(t.pnl_usd or 0) / float(t.risk_usd)
        for t in trades
        if getattr(t, "risk_usd", None) is not None
        and float(t.risk_usd) >= MIN_RISK_USD_FOR_R_AGGREGATION
    )
