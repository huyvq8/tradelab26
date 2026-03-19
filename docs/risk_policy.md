# Risk policy — Trading Lab Pro

Chính sách rủi ro áp dụng cho paper trading. **Không bỏ qua** các rule này khi chạy mô phỏng.

---

## 1. Giới hạn vị thế

- **Max concurrent trades**: Số lệnh mở đồng thời tối đa (mặc định: 3). Khi đạt ngưỡng, không mở lệnh mới cho đến khi đóng ít nhất một lệnh.
- **Position sizing**: Mỗi lệnh risk tối đa `default_risk_pct` × vốn (mặc định 1%). Size USD = risk_dollars / stop_distance, không vượt quá cash hiện có.
- **Minimum size**: Không mở lệnh nếu size sau risk adjustment < 25 USD.

---

## 2. Giới hạn lỗ

- **Daily loss limit**: Nếu realized PnL trong ngày ≤ -(vốn × max_daily_loss_pct) (mặc định 3%), engine **từ chối mọi lệnh mới** đến hết ngày.
- Không tăng risk sau phiên lỗ; không “gồng lỗ” vượt giới hạn.

---

## 3. Stop loss

- Mỗi signal từ strategy **bắt buộc** có stop_loss và take_profit.
- Execution simulator **không** cho phép đóng lệnh bỏ stop; mọi đóng lệnh đều theo rule (stop / TP / logic cycle).
- Operator **không** tắt hoặc nới lỏng stop logic trong code.

---

## 4. Đa dạng hóa và hedge

- (Tương lai) Correlation filter: giới hạn exposure cùng nhóm tài sản.
- (Tương lai) Cấm hedge vô nghĩa: không duy trì long + short cùng symbol/cặp kéo dài mà không có lý do rõ ràng.

---

## 5. Thay đổi tham số

- **Không** đổi tham số strategy (threshold, regime filter, v.v.) chỉ vì 1–2 lệnh thua.
- Chỉ xem xét thay đổi sau khi có **ít nhất 30–50 mẫu** và (khuyến nghị) backtest / forward test.
- Mọi thay đổi tham số nên được **review thủ công** trước khi áp dụng.

---

## 6. Giải thích dòng “Risk engine” trên Dashboard

Dòng có dạng:  
**Risk engine: Còn X slot được mở lệnh (Y/Z). Daily PnL = … USD (nếu ≤ −W sẽ TỪ CHỐI mở thêm). Đang an toàn… / Gần hoặc đã chạm ngưỡng.**

- **Còn X slot được mở lệnh (Y/Z)**  
  - **Z** = số lệnh tối đa được phép mở cùng lúc (cấu hình `MAX_CONCURRENT_TRADES`, mặc định 3).  
  - **Y** = số lệnh đang mở hiện tại.  
  - **X** = Z − Y = số “chỗ trống” còn lại.  
  - **Khi X = 0** (ví dụ 3/3): đã đủ 3 lệnh, hệ thống **không cho mở lệnh mới** cho đến khi đóng ít nhất một lệnh. Đó là lý do bạn thấy “Còn 0 slot (3/3)” — đang có 3 lệnh mở nên không còn slot.

- **Daily PnL và ngưỡng −W**  
  - Daily PnL = tổng lãi/lỗ đã thực hiện (đã đóng lệnh) trong ngày.  
  - W = vốn × max_daily_loss_pct (vd. 1000 × 3% = 30). Nếu Daily PnL **≤ −30** thì hệ thống **từ chối mở thêm lệnh** đến hết ngày.

- **“Gần hoặc đã chạm ngưỡng”**  
  Hiển thị khi: **không còn slot (X = 0)** hoặc Daily PnL đã ≤ ngưỡng. Lúc đó risk engine đang **chặn** không cho mở lệnh mới.  
  **“Đang an toàn, có thể mở thêm”** khi vẫn còn slot và Daily PnL chưa chạm ngưỡng.

---

## 7. Biến môi trường liên quan

| Biến | Mặc định | Mô tả |
|------|----------|--------|
| DEFAULT_CAPITAL_USD | 1000 | Vốn giả lập |
| DEFAULT_RISK_PCT | 0.01 | Risk mỗi lệnh (1%) |
| MAX_DAILY_LOSS_PCT | 0.03 | Ngưỡng dừng mở lệnh theo lỗ trong ngày (3%) |
| MAX_CONCURRENT_TRADES | 3 | Số lệnh mở tối đa |

---

Dự án dùng cho **paper trading và nghiên cứu**. Forward test kỹ trước khi cân nhắc bất kỳ hình thức thực thi thật nào.
