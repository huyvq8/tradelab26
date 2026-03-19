# Tầm nhìn: Hệ thống có tư duy riêng và tự học hỏi

Trading Lab Pro được mong muốn là **hệ thống có tư duy riêng và tự học**, không phải cỗ máy phụ thuộc quá nhiều cấu hình thủ công, vào lệnh với con số cứng nhắc, và đi ngược gợi ý từ AI.

---

## Hiện trạng (điểm cứng nhắc)

- **TP/SL:** Phần lớn từ % cố định trong strategy (0.97, 1.06…) và config (max_tp_pct_above_current, max_hold_hours). Pattern + AI chỉ **gợi ý** cập nhật; rule vẫn có thể chặn hoặc override.
- **Vào lệnh:** Điều kiện vào lệnh là ngưỡng cứng (change_24h > 6, volume > 10M, regime = high_momentum). Không có “học từ lịch sử” để điều chỉnh ngưỡng hoặc tạm tránh combo thua nhiều.
- **Số dư / Cash:** Đã sửa: khi Binance đóng lệnh không cộng notional vào cash (tránh cộng dồn); mỗi cycle cập nhật portfolio.cash_usd từ số dư sàn để dashboard đúng.

---

## Hướng đi: ít config cứng, tôn trọng AI và dữ liệu

1. **Ưu tiên gợi ý AI khi có**  
   Khi AI (OpenAI) trả về cập nhật TP/SL hoặc hành động, **ưu tiên dùng** thay vì bị rule cứng ghi đè (trừ khi vi phạm an toàn rõ ràng, ví dụ SL sai phía giá). Có thể thêm config kiểu `prefer_ai_sl_tp: bool = True` để bật/tắt.

2. **Học từ lịch sử điều chỉnh hành vi**  
   Đã có: journal outcome tự ghi khi đóng lệnh, learn_from_history (combo strategy+symbol thua nhiều, SL quá nhanh). Bước tiếp: dùng cảnh báo đó để **tạm giảm ưu tiên** hoặc **không vào** combo đang thua liên tục (thay vì chỉ hiển thị trên dashboard).

3. **Giảm số cấu hình “magic number”**  
   Thay vì nhiều biến env cứng (%, giờ, ngưỡng volume), hướng tới: (a) giá trị mặc định hợp lý; (b) một số nguồn từ dữ liệu (ATR, học từ lệnh đóng); (c) AI gợi ý tham số hoặc hành động khi có key.

4. **Số liệu và nguồn sự thật**  
   Khi đánh Binance: **số dư / equity** lấy từ sàn (đã đồng bộ cash từ Binance mỗi cycle); không cộng notional khi đóng lệnh đồng bộ để tránh số dư cộng dồn sai.

---

## Đã làm trong code (liên quan)

- Không cộng notional vào `portfolio.cash_usd` khi đóng lệnh trong `sync_positions_from_binance` (tránh cộng dồn).
- Mỗi cycle (Binance): cập nhật `portfolio.cash_usd = get_available_balance_usd()` để dashboard Cash và equity phản ánh sàn.
- Journal + learn_from_history: tự ghi outcome, nhận diện combo thua / SL quá nhanh; có thể mở rộng sang “tự điều chỉnh” (ví dụ tạm tránh combo) thay vì chỉ báo cáo.

Tài liệu này có thể cập nhật khi thêm bước “ưu tiên AI”, “học điều chỉnh vào lệnh”, hoặc giảm config cứng.
