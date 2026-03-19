# Phân tích điểm vào lệnh và thông báo Telegram

## Cách hoạt động

1. **Phân tích điểm vào lệnh** (vùng vào, xác suất, tỷ lệ R)  
   - Từ **tín hiệu strategy** (regime + điều kiện), hệ thống tính:  
     - **Vùng vào lệnh**: entry ± 0,5% (có thể chỉnh trong `core.signals.analysis`).  
     - **Xác suất diễn biến**: heuristic từ confidence (ví dụ 60% hướng TP, 25% sideway, 15% chạm SL).  
     - **Tỷ lệ R**: (TP − entry) / (entry − SL).  
   - Hiển thị trên **Dashboard** (section “Phân tích điểm vào lệnh”) và dùng để soạn nội dung gửi Telegram.

2. **Khi nào gửi Telegram**  
   - Mỗi khi **Worker** chạy cycle (mặc định 5 phút) và **có ít nhất một tín hiệu** (strategy bật), hệ thống gửi **một tin nhắn Telegram** cho mỗi tín hiệu, gồm:  
     - Vùng LONG/SHORT đẹp (khoảng giá vào).  
     - Xác suất diễn biến (%, hướng TP / sideway / SL).  
     - Tỷ lệ R, strategy, lý do.  
     - Dòng nhắc: “Thực hiện vào lệnh nếu phù hợp.”  
   - Nếu cycle đó **thật sự mở lệnh paper**, tin nhắn thêm: “Đã tự động mở lệnh paper.”

3. **“Dấu hiệu nến theo kế hoạch”**  
   - Hiện tại “dấu hiệu theo kế hoạch” chính là **tín hiệu strategy** (regime + điều kiện) → đã gửi Telegram như trên.  
   - **Dấu hiệu nến** (V-reversal, engulfing, v.v.) cần **dữ liệu nến OHLC** (theo timeframe 5m/15m/1h).  
   - Dự án đang dùng CMC (quote 24h), **chưa** có OHLC. Để bật “dấu hiệu nến” thật:  
     - Bổ sung nguồn OHLC (ví dụ Binance/Bybit API).  
     - Thêm module nhận diện mẫu nến (pattern) và gọi logic gửi Telegram tương tự khi pattern thỏa.

## Tóm tắt

- **Điểm vào lệnh**: vùng giá (entry zone), SL, TP, R → có trong Dashboard và trong tin Telegram.  
- **Xác suất diễn biến**: % hướng TP / sideway / SL (heuristic từ confidence) → có trong Dashboard và Telegram.  
- **Tỷ lệ lợi nhuận chi tiết**: R-multiple → có trong Dashboard và Telegram.  
- **Thông báo khi có “dấu hiệu theo kế hoạch”**: mỗi khi có tín hiệu strategy, Telegram được gửi; “dấu hiệu nến” sẽ gửi thêm khi đã có OHLC và module pattern.
