# Hệ thống tự nhận ra sai lầm (đọc log thực tế & dữ liệu cũ)

Tài liệu mô tả cách hệ thống **tự ghi và đọc** kết quả lệnh, nhận diện pattern sai lầm (SL quá nhanh, combo strategy+symbol thua nhiều) để phản tư và gợi ý.

---

## 1. Tự ghi kết quả khi đóng lệnh (journal outcome)

- **Khi nào:** Mỗi khi một vị thế được đóng (SL/TP kích hoạt, đồng bộ từ Binance, đóng chủ động), hệ thống tìm **journal entry** tương ứng (qua `trade_id` = open Trade id) và gọi `JournalService.record_outcome_from_close(db, position, close_trade)`.
- **Ghi gì:** Từ dữ liệu thật của lệnh đóng:
  - **result_summary:** lý do đóng (SL/TP/sync/proactive), PnL, thời gian giữ (phút), R-multiple (nếu có risk_usd).
  - **lessons:** gợi ý rút ra (vd. "SL kích hoạt rất nhanh; cân nhắc nới SL hoặc vào lệnh sau confirmation", "Lệnh lỗ; xem lại entry và SL/TP").
  - **mistakes:** cụm từ chuẩn để aggregation (vd. `sl hit in under 5 min`, `sl hit in under 15 min`, `loss after short hold`, `loss larger than 1.5r`).
- **Luồng:** `check_sl_tp_and_close` và `sync_positions_from_binance` sau khi tạo Trade đóng đều gọi `record_outcome_from_close`. Journal entry phải đã có `trade_id` khi mở lệnh (cycle đã được sửa để truyền `trade_id` khi `create_entry`).

---

## 2. Đọc dữ liệu cũ để nhận diện pattern (learn_from_history)

- **Module:** `core/reflection/learn_from_history.py`.
- **Hàm chính:** `learn_from_closed_trades(db, portfolio_id, last_n_days=30, min_trades_per_group=2)`.
  - Đọc tất cả Trade đóng trong 30 ngày (hoặc `last_n_days`), join Position để có thông tin vị thế.
  - Lấy open Trade để tính **thời gian giữ** (phút).
  - Nhóm theo **(strategy_name, symbol, side)**. Với mỗi nhóm (ít nhất 2 lệnh):
    - Win rate, total PnL, avg hold (phút).
    - % lệnh chạm SL trong &lt;5 phút, &lt;15 phút.
  - **Cảnh báo (warnings):**
    - **low_win_rate:** win rate &lt; 40% → gợi ý "cân nhắc nới SL hoặc tạm tránh combo này".
    - **sl_very_fast:** ≥50% lệnh chạm SL trong &lt;5 phút → "SL có thể quá sát".
  - Đồng thời trả về **from_journal_mistakes:** top cụm từ lỗi lặp lại từ journal (dùng `repeated_mistakes` trong reflection engine).

---

## 3. Tích hợp vào reflection và dashboard

- **Reflection:** `ReflectionEngine.build_daily_reflection` gọi `learn_from_closed_trades` và đưa kết quả vào `out["learned_from_history"]` (gồm `warnings`, `by_group`, `from_journal_mistakes`).
- **Dashboard:** Trang Báo cáo / Phản tư có expander **"Hệ thống tự nhận ra (đọc lịch sử 30 ngày)"** — hiển thị danh sách cảnh báo (combo thua nhiều, SL quá nhanh). Dữ liệu từ DB (Trade + Journal), không cần log file riêng.
- **Lỗi lặp lại:** Các cụm từ trong journal (mistakes) được gộp bởi `repeated_mistakes(db)` và hiển thị ở expander "Lỗi lặp lại & pattern (học từ journal)" — hệ thống "tự nhận ra" sai lầm lặp lại nhờ **dữ liệu cũ đã được ghi tự động** khi đóng lệnh.

---

## 4. Tóm tắt luồng

| Bước | Nguồn dữ liệu | Hành động |
|------|----------------|-----------|
| Mở lệnh | Signal, risk | Tạo Position + Trade (open) + **JournalEntry** (trade_id = open Trade id). |
| Đóng lệnh | Trade (close), Position | **Tự ghi** result_summary, lessons, mistakes vào JournalEntry tương ứng (`record_outcome_from_close`). |
| Reflection / Báo cáo | Trade đóng + Journal | **Đọc** 30 ngày: nhóm theo strategy+symbol+side, tính win rate, % SL nhanh → **warnings**; gộp mistakes từ journal → **repeated_mistakes**. |
| Dashboard | Reflection | Hiển thị "Lỗi lặp lại" và "Hệ thống tự nhận ra (đọc lịch sử 30 ngày)" để operator thấy hệ thống đã tự nhận diện sai lầm từ log thực tế / dữ liệu cũ. |

Hệ thống không cần file log riêng: mọi thứ lưu trong DB (Trade, Position, JournalEntry) và được đọc lại bởi reflection + learn_from_history để tự nhận ra pattern và đưa vào gợi ý / cảnh báo.
