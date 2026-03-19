# Khi nào dùng Telegram / OpenAI

## Telegram

Tin nhắn Telegram **chỉ được gửi** trong 2 trường hợp sau (đều do **Worker** thực hiện):

| Thời điểm | Điều kiện | Nội dung gửi |
|-----------|-----------|--------------|
| **1. Mỗi khi mở lệnh** | Worker chạy cycle mỗi **5 phút**. Nếu cycle đó **mở được ít nhất 1 lệnh** (strategy + risk cho phép) | Một tin nhắn cho mỗi lệnh mở: symbol, side, strategy, entry, size, SL, TP |
| **2. Báo cáo cuối ngày** | Worker chạy job lúc **23:55** hàng ngày | Một tin: headline + summary + recommendations |

**Lưu ý:**

- Worker phải **đang chạy** (qua `run_all.bat` hoặc `python apps/worker/runner.py`). Nếu chỉ mở API + Dashboard thì **không có** tin Telegram.
- Nếu **không có lệnh nào được mở** trong các cycle (điều kiện strategy/risk không thỏa), bạn **chỉ nhận** tin lúc **23:55** (báo cáo ngày).
- Để test ngay: chạy `python scripts/test_telegram.py` (gửi 1 tin thử) hoặc đợi đến 23:55 để nhận báo cáo.

**Cấu hình:** `.env` phải có `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` (số âm cho nhóm).

**Test Telegram:** chạy `python scripts/test_telegram.py` — nếu thành công sẽ có 1 tin trong nhóm; nếu lỗi sẽ in ra console.

---

## OpenAI API key

**Khi nào gọi OpenAI:**

| Thời điểm | Điều kiện | Việc làm |
|-----------|-----------|----------|
| **Dashboard: Journal + Reflection** | User mở trang Dashboard, có `OPENAI_API_KEY` | Gọi **daily_review** và **next_day_plan** từ `core.reflection.ai_service`. |
| **Dashboard: Phân tích tình huống** | User bấm "Phân tích tình huống" cho một symbol, có `OPENAI_API_KEY` | Lấy nến 1h (Binance), giá, volume, vị thế đang mở → gọi **market_structure_situation** (`core.reflection.ai_situation`). AI trả về: tình huống (capitulation, relief rally…), xác suất, kế hoạch rẽ nhánh (có/không có lệnh), mức giá cần theo dõi. |
| **Worker: Mỗi giờ (phút 5)** | Có `OPENAI_API_KEY` + Telegram | Nếu nến 1h vừa đóng có **body > 4%** hoặc **volume > 1.8x** nến trước → gọi AI phân tích tình huống cho symbol đó và gửi Telegram (tối đa 2 symbol/lần). |
| **Báo cáo 23:55** | Worker chạy Daily report, có `OPENAI_API_KEY` | Summary báo cáo có thêm "AI Reflection" và "Next day plan (AI)". |

- Biến đọc từ `.env`: `OPENAI_API_KEY`, lưu trong `core.config.settings.openai_api_key`.
- Model dùng: `gpt-4o-mini` (trong `core/reflection/ai_service.py`). Có thể đổi model trong code.
- **Nếu để trống OPENAI_API_KEY:** app vẫn chạy bình thường; reflection chỉ dùng số liệu tổng hợp (không có đoạn văn AI). Khuyến nghị vẫn chạy theo rule + metrics.
