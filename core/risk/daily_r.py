"""
Tổng R trong ngày (lệnh đóng): R = sum_i (pnl_usd_i / risk_usd_i) cho từng Trade action=close.

- **Không phải** “số USD lỗ trong ngày”. USD đã chốt = sum(pnl_usd) của các lệnh đóng.
- **risk_usd** trên bản ghi close (sau sửa) lấy theo **initial_stop_loss** nếu có, để SL trailing/breakeven
  không làm risk_usd → 0 và phình R vô lý.
- **partial_close** không vào metric kill switch mặc định (chỉ action=close); nếu sau này gộp cần định nghĩa riêng.
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
