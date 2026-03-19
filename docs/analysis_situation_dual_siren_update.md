# Phân tích tình huống: Cập nhật TP/SL khi có 2 vị thế SIREN long + -4130

**Nguyên nhân có 2 bản ghi DB cho 1 vị thế trên sàn:** Binance **tự gộp** vị thế cùng (symbol, side) thành một (quantity và entry trung bình). Không phải do người dùng sai — hệ thống cần **tự đồng bộ** lại vị thế thực tế (đã thêm bước gộp trong sync).

## Log mô tả

- Nhiều lần `[BinanceFutures] Updated STOP_MARKET / TAKE_PROFIT_MARKET for SIREN` với giá khác nhau (0.6972, 0.7025, 0.7045, 0.74787, 0.74294, 0.68988…).
- `[Cycle] Vi the SIREN (long): UPDATE_TP_SL` (engulfing_bear, rồi AI thắt chặt SL) và `HOLD`.
- `[Cycle] Bo qua SIREN: da co vi the cung chien luc` — có tín hiệu nhưng không mở thêm vì đã có vị thế cùng chiến lược.
- `mo_lenh=1` và đặt TP/SL mới cho SIRENUSDT (0.74294, 0.68988) — tức có mở thêm một lệnh (có thể SIREN hoặc symbol khác).
- Cuối cùng: `Update STOP_MARKET failed for SIREN (retry): -4130 An open stop or take profit order... already existing`.
- Kèm theo: job skip (maximum number of running instances reached), Telegram Timed out.

---

## Nguyên nhân chính

### 1. Hai bản ghi Position cùng symbol + side (SIREN long)

- Trên **Binance** chỉ tồn tại **một** vị thế theo (symbol, side) (one-way mode: một position SIREN long).
- Trong **DB** có thể có **hai** bản ghi `Position` (hai lần vào SIREN long, ví dụ hai chiến lược hoặc hai lần cùng chiến lược trước khi bật “tránh trùng”).
- Mỗi cycle, `review_positions_and_act` duyệt **từng** Position: với **mỗi** bản ghi SIREN long nó có thể quyết định UPDATE_TP_SL và gọi `update_position_sl_tp`.
- Hệ quả: **cùng một cycle** gọi update **hai lần** cho cùng SIREN long:
  - Lần 1: hủy algo → đặt TP/SL (ví dụ 0.7045, 0.74787).
  - Lần 2: hủy algo lại → đặt TP/SL khác → dễ gặp **-4130** vì sàn chưa kịp xử lý hủy hoặc đã có lệnh mới từ lần 1.

### 2. Lỗi -4130 khi retry

- Sau khi đặt TAKE_PROFIT_MARKET thành công, khi đặt STOP_MARKET có thể trả -4130 (đã tồn tại lệnh stop/take profit closePosition cùng hướng).
- Retry (hủy lại + sleep + đặt lại) vẫn báo lỗi: có thể do hủy chưa kịp có hiệu lực hoặc do **đang có hai luồng/c cycle** cùng cập nhật SIREN (job skip nhưng vẫn có cycle chạy dài).

### 3. Job skip và Telegram

- **Job skip:** Interval 10s nhưng một cycle tốn >10s (nhiều API Binance, sleep 1s sau hủy) → `max_instances=1` khiến lần kích hoạt tiếp theo bị skip.
- **Telegram Timed out:** Gửi tin sau khi có tín hiệu/mở lệnh bị timeout (mạng hoặc Telegram API chậm).

---

## Đã xử lý trong code

1. **Đồng bộ gộp vị thế theo sàn (sync_positions_from_binance)**  
   Trước khi xử lý “đóng vị thế không còn trên sàn”, mỗi cycle chạy **bước gộp**: với mỗi (symbol, side) có trên Binance, nếu DB có **2+** bản ghi Position mở thì: cập nhật **một** bản ghi (id nhỏ nhất) theo quantity và entry_price từ Binance, lấy TP/SL hiện tại trên sàn (nếu có); đánh dấu các bản ghi còn lại **is_open=False, closed_at=now** (không tạo Trade close — vị thế không đóng thật, chỉ gộp). Kết quả: DB phản ánh đúng “một vị thế trên sàn”.

2. **Chỉ một lần update TP/SL cho mỗi (symbol, side) trong một cycle**  
   Trong `review_positions_and_act`:
   - Dùng set `updated_symbol_side`.
   - Nếu đã gọi `update_position_sl_tp` cho (symbol, side) trong cycle này thì **không** gọi lại; chỉ thêm action HOLD với lý do “đã cập nhật TP/SL cho symbol này trong cycle này (1 bộ/symbol trên sàn)”.
   - Tránh hủy + đặt lại hai lần cho cùng SIREN long → giảm race và -4130.

3. **Đồng bộ SL/TP vào mọi Position cùng (symbol, side)**  
   Sau khi đặt TP/SL trên sàn thành công:
   - Cập nhật `stop_loss` / `take_profit` cho **tất cả** bản ghi Position mở cùng (symbol, side).
   - DB phản ánh đúng một bộ TP/SL trên sàn; cycle sau không “nghĩ” vị thế thứ hai cần update khác.

---

## Khuyến nghị vận hành

- **Tăng CYCLE_INTERVAL_SECONDS** (ví dụ 30–60s) để giảm job skip và giảm xung đột giữa nhiều cycle (ít gọi API Binance hơn, ít race hơn).
- **Telegram:** Kiểm tra mạng / proxy; có thể tăng timeout hoặc gửi bất đồng bộ để không chặn cycle.
- **Hai vị thế cùng symbol:** Nếu muốn tránh hai bản ghi Position cùng symbol (SIREN long), có thể giữ `add_only_different_strategy` và `max_per_symbol` (ví dụ 1) để ít khi có hai lệnh SIREN long; khi cho phép 2, logic “một update mỗi (symbol, side)” vẫn đảm bảo chỉ một bộ TP/SL trên sàn và DB đồng bộ.
