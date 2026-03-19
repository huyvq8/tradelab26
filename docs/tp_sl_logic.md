# Cách tính TP và SL trong hệ thống

Tài liệu mô tả **logic và luồng** từ lúc có tín hiệu đến khi đặt lệnh TP/SL trên Binance (ví dụ: `TAKE_PROFIT_MARKET triggerPrice=0.8054500`, `STOP_MARKET triggerPrice=0.7370600`).

---

## 1. Nguồn gốc ban đầu: Chiến lược (Strategy)

TP và SL **không** được tính bằng ATR hay indicator phức tạp — chúng được **mỗi chiến lược định nghĩa theo % cố định** so với **giá tại thời điểm phát tín hiệu** (`price`).

Định nghĩa nằm trong **`core/strategies/implementations.py`**:

| Chiến lược | Hướng | Công thức SL | Công thức TP | Ý nghĩa |
|------------|--------|--------------|--------------|---------|
| **Trend Following** | long | `price × 0.97` | `price × 1.06` | SL −3%, TP +6% |
| **Breakout Momentum** | long | `price × 0.975` | `price × 1.05` | SL −2.5%, TP +5% |
| **Mean Reversion** | long | `price × 0.96` | `price × 1.04` | SL −4%, TP +4% |
| **Liquidity Sweep Reversal** | short | `price × 1.03` | `price × 0.95` | SL +3% (trên giá), TP −5% |

Ví dụ với **Trend Following** và giá tại lúc tín hiệu = 0.76:

- SL = 0.76 × 0.97 = **0.7372** → làm tròn theo tickSize Binance có thể thành **0.7370600**
- TP = 0.76 × 1.06 = **0.8056** → làm tròn thành **0.8054500**

→ Các con số bạn thấy trong log (`triggerPrice=0.8054500`, `0.7370600`) **khớp với logic % cố định** của một trong các chiến lược (ở đây là kiểu Trend Following: −3% / +6%), với `price` là giá lúc tín hiệu (có thể gần với giá vào lệnh).

---

## 2. Luồng từ tín hiệu đến sàn

```
Strategy.evaluate(symbol, price, ...)
    → StrategySignal(entry_price=price, stop_loss=..., take_profit=...)
         ↓
RiskEngine.assess(signal, ...)  ← dùng |entry - stop_loss| để tính size
         ↓
Execution (Paper hoặc Binance)
    → open_position(db, ..., signal, size_usd)
         ↓
Binance: _round_stop_price(signal.stop_loss, tickSize) → triggerPrice SL
         _round_stop_price(signal.take_profit, tickSize) → triggerPrice TP
         POST /fapi/v1/algoOrder (STOP_MARKET, TAKE_PROFIT_MARKET)
```

- **Risk engine** dùng `|entry_price - stop_loss|` để tính **kích thước vị thế** (risk theo khoảng cách stop), không thay đổi giá TP/SL.
- **Execution** chỉ truyền nguyên `signal.stop_loss` và `signal.take_profit` vào DB và vào lệnh Binance.
- **Binance** yêu cầu giá trigger phải khớp **PRICE_FILTER** (tickSize) nên có bước **làm tròn** trong `core/execution/binance_futures.py` (`_round_stop_price`): giá được làm tròn xuống bội số của `tickSize` (ví dụ 0.0001 hoặc 0.01 tùy symbol).

---

## 3. Cập nhật TP/SL sau khi đã vào lệnh (optional)

### 3.0 Khi nào cập nhật TP/SL vào vị thế đang có

- **Thời điểm:** Mỗi **cycle** của worker, **sau** bước kiểm tra SL/TP đóng lệnh và **sau** đồng bộ vị thế từ Binance. Worker gọi `review_positions_and_act()` trong `core/orchestration/cycle.py`.
- **Đối tượng:** Chỉ các vị thế **đang mở** theo DB (chưa bị đóng bởi proactive close trong cùng hàm).
- **Điều kiện để UPDATE_TP_SL:**
  1. Có **pattern** nến (từ ~20 nến 1h) cho symbol.
  2. `suggest_sl_tp_update()` trả về gợi ý **(new_sl, new_tp, reason)** với ít nhất một giá trị **khác** giá hiện tại (new_sl ≠ pos.stop_loss hoặc new_tp ≠ pos.take_profit).
  3. **Hợp lệ theo phía:** Long → new_sl < giá hiện tại, new_tp > giá hiện tại; Short → new_sl > giá hiện tại, new_tp < giá hiện tại.
- **Thực thi:** Gọi `executor.update_position_sl_tp(db, pos, sl_final, tp_final, note=reason)`. Luôn truyền **cả SL và TP**: nếu gợi ý chỉ đổi TP thì `sl_final = pos.stop_loss`, `tp_final = new_tp` (và ngược lại) để sau khi hủy algo cũ trên sàn, cả hai lệnh TP và SL đều được đặt lại, tránh mất một bên.

Sau khi có vị thế mở, **mỗi cycle** trong `review_positions_and_act` thực hiện:

1. Lấy nến 1h cho symbol.
2. **Phát hiện pattern** (hammer, engulfing_bear, big_body_bull, …) trong `core/patterns/candlestick.py`.
3. **Gợi ý SL/TP mới** trong `core/reflection/sl_tp_update.py`:
   - **Rule-based:** ví dụ hammer/rejection_low (long) → chuyển SL về breakeven; engulfing_bear → thắt chặt SL dưới đáy nến; **big_body_bull → nới TP** (xem công thức bên dưới).
   - **AI (nếu bật OpenAI):** prompt đọc pattern + nến, trả về hành động kiểu `MOVE_SL_BREAKEVEN`, `TIGHTEN_SL: 0.74`, `TRAIL_TP: 0.82`, …
4. Nếu chấp nhận gợi ý (và giá trị hợp lệ: long thì SL < price, TP > price; short ngược lại), **Binance** sẽ hủy algo cũ và đặt lại **cả SL và TP** (giữ giá cũ cho bên không đổi) qua Algo Order API.

Vì vậy, **TP/SL trên sàn** có thể là:
- **Ban đầu:** 100% từ chiến lược (công thức % theo `price` như bảng trên).
- **Sau đó:** có thể đã được cập nhật một lần (hoặc vài lần) bởi pattern + rule/AI, rồi mới đặt lại lên Binance.

### 3.1 Nguyên nhân TP “leo cao” (vd. 0.92 khi entry ~0.77): rule **big_body_bull**

Khi nến 1h được nhận diện là **big_body_bull** (nến tăng, body lớn) và:
- giá hiện tại > entry, và  
- **đỉnh nến (close `c`) ≥ 98% TP hiện tại** (`c > current_tp * 0.98`),

hệ thống **nới TP lên** theo công thức (trong `core/reflection/sl_tp_update.py`):

```
extra     = (c - entry_price) × 0.5
candidate_tp = entry_price + extra + (current_tp - entry_price) × 0.3
new_tp    = candidate_tp   (nếu candidate_tp > current_tp)
```

Tương đương:

**`new_tp = 0.2×entry + 0.5×c + 0.3×current_tp`**

Trong đó:
- **entry** = giá vào lệnh (position),
- **c** = giá **đóng cửa** của nến 1h gần nhất,
- **current_tp** = TP hiện tại (ban đầu từ strategy, sau đó là kết quả các lần cập nhật trước).

---

#### Ví dụ số: TP từ ~0.82 lên **0.99** (entry giả sử = 0.77)

| Bước | current_tp (trước) | c (close nến 1h) | Tính: 0.2×0.77 + 0.5×c + 0.3×current_tp | new_tp (sau) |
|------|--------------------|------------------|----------------------------------------|--------------|
| 0 (strategy) | — | — | Trend Following: 0.77×1.06 | **0.816** |
| 1 | 0.816 | 0.85 | 0.154 + 0.425 + 0.245 | **0.824** |
| 2 | 0.824 | 0.88 | 0.154 + 0.44 + 0.247 | **0.841** |
| 3 | 0.841 | 0.91 | 0.154 + 0.455 + 0.252 | **0.861** |
| 4 | 0.861 | 0.94 | 0.154 + 0.47 + 0.258 | **0.882** |
| 5 | 0.882 | 0.97 | 0.154 + 0.485 + 0.265 | **0.904** |
| 6 | 0.904 | 1.00 | 0.154 + 0.50 + 0.271 | **0.925** |
| 7 | 0.925 | 1.02 | 0.154 + 0.51 + 0.278 | **0.942** |
| 8 | 0.942 | 1.04 | 0.154 + 0.52 + 0.283 | **0.957** |
| 9 | 0.957 | 1.05 | 0.154 + 0.525 + 0.287 | **0.966** |
| 10 | 0.966 | 1.06 | 0.154 + 0.53 + 0.290 | **0.974** |
| 11 | 0.974 | 1.08 | 0.154 + 0.54 + 0.292 | **0.986** |
| 12 | 0.986 | 1.09 | 0.154 + 0.545 + 0.296 | **≈ 0.995** → làm tròn **0.99** |

Mỗi **cycle** (vd. 10s), nếu nến 1h vẫn là **big_body_bull** và **c > current_tp × 0.98**, công thức chạy một lần và TP nhảy lên một nấc. Sau nhiều cycle (nến đóng cửa cao dần), TP có thể leo từ 0.82 lên **0.99** như trên. Con số 0.99 bạn thấy trên Binance là kết quả **nhiều lần** cập nhật như vậy, không phải một công thức duy nhất từ entry.

- **SL 0.72609:** Có thể từ strategy ban đầu (entry×0.97) hoặc từ rule **engulfing_bear** (thắt chặt SL dưới đáy nến) hoặc AI; giá trị làm tròn theo tickSize Binance.

### 3.2 Logic thông minh (phong cách chuyên gia): ATR + cấu trúc + học từ lệnh

Hệ thống **không** dùng cấu hình % cố định. Mỗi lần nới TP (rule **big_body_bull**), TP được giới hạn bởi: **(1) ATR (14 nến 1h):** TP long ≤ entry + 2,5×ATR (biến động thực tế). **(2) Cấu trúc:** TP long ≤ đỉnh 10 nến gần nhất × 1,01. **(3) Học từ journal:** phân vị 75% (exit−entry)/entry từ lệnh đã đóng → TP ≤ entry×(1+pct); cần ≥5 lệnh. Toàn bộ do **logic** (dữ liệu + xác suất + kinh nghiệm), không phải config tùy ý.

### 3.3 Chốt lãi an toàn (Lock profit / Securing profit)

**Tên chiến thuật:** **Lock in profit**, **Securing profit** hoặc **Break-even plus**. Khi vị thế đang có lãi (vd. +30 USD), hệ thống kéo SL lên (long) hoặc xuống (short) để nếu đảo chiều thì chạm SL vẫn đóng với lãi. **Điều kiện:** Lãi chưa chốt ≥ LOCK_PROFIT_MIN_USD; công thức long: new_sl = entry + min_profit_usd/quantity. **Thứ tự:** Xét trước AI và rule trong suggest_sl_tp_update.

### 3.4 Playbook nhiều chiến thuật giảm rủi ro

1. **Chốt lãi an toàn** (lãi ≥ LOCK_PROFIT_MIN_USD) 2. **Breakeven** (pattern hammer/rejection) 3. **Thắt chặt SL** (engulfing) 4. **Nới TP** (big_body_bull) 5. **AI**. Bật LOCK_PROFIT_MIN_USD > 0 trong .env để dùng chốt lãi.

---

## 4. Tóm tắt: “Bộ công cụ” tính TP/SL

| Bước | Công cụ / Logic |
|------|-----------------|
| **Giá trị ban đầu** | Mỗi strategy trong `implementations.py`: **% cố định** so với giá tại thời điểm tín hiệu (0.96–0.97, 1.03–1.06, v.v.). |
| **Làm tròn gửi sàn** | `_round_stop_price(price, tick_size)` trong `binance_futures.py`: làm tròn theo **PRICE_FILTER (tickSize)** của Binance. |
| **Cập nhật sau khi vào lệnh** | `sl_tp_update.py`: **chốt lãi an toàn** (lãi ≥ LOCK_PROFIT_MIN_USD) + **rule** (pattern) + **ATR/cấu trúc/journal** + **AI**; hủy algo cũ và đặt lại trên Binance. |

**Khung thời gian (dài vs ngắn hạn):** Mặc định hệ thống không khai báo rõ: TP/SL theo % từ entry, pattern/AI có thể nới TP xa (0.74 → 0.76…) nên TP dễ cách giá hiện tại 5–10%. Nếu **đánh ngắn** (chốt lãi theo giờ/phiên) thì TP xa như vậy không hợp lý. Đặt **MAX_TP_PCT_ABOVE_CURRENT** = 0.02 trong .env để giới hạn TP ≤ giá hiện tại × 1.02 (long); kèm **MAX_HOLD_HOURS** (vd. 4) để đóng lệnh trong ngày.

Hệ thống **không** dùng ATR, volatility hay indicator khác để tính TP/SL — toàn bộ xuất phát từ **tỷ lệ % cố định của từng chiến lược** và (tùy chọn) **điều chỉnh theo pattern + AI** sau khi đã mở lệnh.
