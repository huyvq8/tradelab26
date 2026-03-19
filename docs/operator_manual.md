# Operator manual — Trading Lab Pro

Hướng dẫn vận hành hàng ngày và guardrails chuyên nghiệp.

---

## Thói quen hàng ngày

1. **Khởi động stack**: `docker compose up -d` hoặc chạy API + dashboard + worker riêng.
2. **Seed DB (một lần)**: `python scripts/seed_db.py` nếu chưa có portfolio.
3. **Cycle định kỳ**: Worker chạy cycle mỗi 5 phút (BTC, ETH, SOL). Có thể gọi thủ công: `python scripts/run_cycle.py --symbols BTC,ETH,SOL`.
4. **Xem dashboard**: Chạy `streamlit run apps/dashboard/app.py` (hoặc `start_dashboard.bat`), mở trình duyệt tại **http://localhost:8501**. Trên dashboard: xem báo cáo trade theo thời gian thực, cấu hình watchlist (token theo dõi), xem chiến lược đang đánh, PnL (realized + unrealized), tín hiệu/điểm vào lệnh, khuyến nghị. Thông tin quan trọng (mở lệnh, báo cáo ngày) được gửi lên nhóm Telegram nếu đã cấu hình.
5. **Báo cáo cuối ngày**: Worker tạo daily report lúc 23:55. Có thể tạo tay: `python scripts/generate_daily_report.py --date 2026-03-16`. Nếu cấu hình Telegram, báo cáo được gửi vào nhóm/chat.
6. **Đọc recommendations**: Chỉ **đề xuất**; không tự ý thay đổi tham số strategy khi chưa đủ mẫu.

---

## Guardrails chuyên nghiệp

- **Không** tắt hoặc sửa logic stop loss trong execution simulator.
- **Không** tăng risk sau phiên lỗ (ví dụ tăng DEFAULT_RISK_PCT sau một ngày âm).
- **Không** đánh giá một setup chỉ bằng một khung thời gian hoặc một vài lệnh; cần **ít nhất 30–50 mẫu** trước khi kết luận.
- **Không** thay đổi tham số strategy chỉ vì vài lệnh thua; ưu tiên backtest và forward test trước khi chỉnh.

---

## API nhanh

- `GET /health` — Kiểm tra API.
- `POST /cycle/run?symbols=BTC,ETH,SOL` — Chạy một cycle.
- `POST /reports/daily?report_date=2026-03-16` — Tạo báo cáo ngày.
- `GET /portfolio/summary` — Tổng quan portfolio, positions, trades, reports.

---

## Cấu hình quan trọng (.env)

- **CMC_API_KEY**: Bắt buộc để lấy giá thật; không có thì dùng giá mock.
- **TELEGRAM_BOT_TOKEN**, **TELEGRAM_CHAT_ID**: Tùy chọn; điền để nhận báo cáo cuối ngày qua Telegram.
  - Token: lấy từ [@BotFather](https://t.me/BotFather) khi tạo bot (ví dụ bot của bạn: t.me/tradelab221_abot).
  - Chat ID: thêm bot vào nhóm (hoặc gửi /start cho bot trong chat riêng), gửi một tin nhắn trong nhóm/chat đó, rồi mở trong trình duyệt: `https://api.telegram.org/bot<TOKEN>/getUpdates` — trong JSON tìm `"chat":{"id": -123456789}` (số âm cho nhóm). Dán số đó vào `TELEGRAM_CHAT_ID`.
- **DATABASE_URL**: Mặc định SQLite; production nên dùng PostgreSQL (ví dụ `postgresql://user:pass@host:5432/dbname`).

---

## Vì sao một symbol (vd. BTC) không có tín hiệu?

Hệ thống **quét đều** mọi symbol trong watchlist (BTC, SIREN, …). Tín hiệu chỉ xuất hiện khi **ít nhất một chiến lược** thỏa điều kiện **rất cụ thể** (ngưỡng % biến động 24h và volume). Regime được suy từ `change_24h` và `volume_24h`:

| Regime          | Điều kiện |
|-----------------|-----------|
| high_momentum   | change_24h > 5% **và** volume_24h > 5M |
| risk_off        | change_24h < -5% |
| balanced        | Còn lại (vd. -5% ≤ change_24h ≤ 5%) |

Tín hiệu chỉ phát khi:

| Chiến lược              | Điều kiện phát tín hiệu |
|-------------------------|--------------------------|
| Trend Following         | regime = high_momentum **và** change_24h > 3% → long |
| Breakout Momentum       | change_24h > 6% **và** volume_24h > 10M → long |
| Mean Reversion          | change_24h < -6% → long |
| Liquidity Sweep Reversal| 4% ≤ change_24h ≤ 10% **và** high_momentum → short |

- **BTC** thường biến động trong ngày nhỏ hơn altcoin (vd. +1% ~ +3%) → dễ rơi vào **balanced**, không đạt >6% hay < -6% → **không chiến lược nào bắn**.
- **SIREN** (altcoin) dễ có biến động lớn (vd. +8%, -7%) → dễ trúng một trong các ngưỡng trên.

**Cách kiểm tra:** Trên dashboard, mục **Regime + chiến lược** (hoặc bảng tín hiệu theo symbol) hiển thị với từng symbol: **regime**, **change_24h**, **volume_24h** và từng chiến lược là **CÓ** hay **KHÔNG** (kèm lý do, vd. "change_24h=1.2, regime=balanced"). Xem số liệu BTC tại đó để xác nhận vì sao không có tín hiệu. Nếu muốn BTC thỉnh thoảng có tín hiệu, cần chỉnh ngưỡng trong `core/strategies/implementations.py` (vd. giảm 6% xuống 4%) hoặc thêm chiến lược phù hợp biến động nhỏ; nên backtest trước khi dùng thật.

---

## Khi có sự cố

- API không lên: Kiểm tra `DATABASE_URL`, port 8000.
- Dashboard không load: Chạy từ thư mục gốc dự án, đảm bảo Streamlit dùng đúng port 8501.
- Worker không chạy cycle: Kiểm tra log; đảm bảo DB đã seed và CMC key (nếu dùng) hợp lệ.
- Telegram không gửi: Kiểm tra token và chat_id; lỗi Telegram không làm fail job daily report.
