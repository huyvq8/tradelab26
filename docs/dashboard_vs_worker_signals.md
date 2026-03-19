# Dashboard vs Worker — tín hiệu

## Vấn đề đã xử lý

- Dashboard trước đây gọi `strategy.evaluate` **không** truyền `klines_1h` → rationale kiểu **"fallback: no klines"** dù Worker có fetch klines.
- Dashboard gộp mọi thứ thành “tín hiệu vào lệnh” trong khi Worker còn context gates, entry timing, volatility guard, risk, scale-in.

## Hiện tại (app dashboard)

1. **Ứng viên strategy** — `evaluate(..., klines_1h)` với **25** nến 1h (cùng ý với worker khi đủ dữ liệu). Bảng có `klines_1h_bars`, `pipeline_stage=strategy_candidate`.
2. **Luồng quyết định** — metrics + `blocked_signals.json` (watchlist) + `decision_log` (`entry_opened` / `entry_rejected`).
3. **Không** hiển thị scale-in reject (chủ yếu trong log Worker `SCALE_IN_REJECTED`).

## Kết luận edge / PF

Chỉ kết luận cải thiện edge khi có **đủ lệnh đóng** và báo cáo `validate_edge_patch_report` — xem `docs/validation_runbook.md`.
