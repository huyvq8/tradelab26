# V6 Audit Report (theo `v6_checklist_final`)

Ngày: 2026-03-19  
Codebase: `trading-lab-pro-v3`

---

## A. PASS / FAIL theo module

| Module | Trạng thái | Ghi chú ngắn |
|--------|------------|--------------|
| **1. Decision Core** | **PARTIAL** | Flow chính trong `cycle.py` (~1200 dòng); có `REJECTED_SIGNAL`, `SCALE_IN_REJECTED`, `SIGNAL_CANDIDATE`. Chưa có `decision_engine` tập trung. |
| **2. State Core** | **PARTIAL** | DB `Portfolio`/`Position`/`Trade`; sync Binance `sync_positions_from_binance`. Không có `StateManager` trừu tượng; cache rải `client.py`, `binance_futures.py`, WS. |
| **3. Strategy Core** | **PASS** | Strategies tách `core/strategies/`; routing `token_classifier` + `strategy_routing.v1.json`; blocked strategies theo profile. |
| **4. Risk Core** | **PARTIAL (đã sửa 1 bug)** | Kill switch + daily R: **đã đồng nhất** Worker ↔ Dashboard qua `core/risk/daily_r.py`. Scale-in equity vẫn dùng `portfolio.capital_usd` (khác `default_capital_usd` trong sizing). |
| **5. Execution Core** | **PASS** | `exchangeInfo` 24h TTL; hedge mode 30m; balance 60s; algo orders 5s; throttle TP/SL 120s (`binance_futures.py`). |
| **6. AI Advisory** | **PARTIAL** | Có `AI_CALL reason=...` (`sl_tp_update.py`, `ai_situation.py`). TP/SL có guard tuổi position + PnL (`cycle.py`). System review / situation gọi theo job 10 phút — không mỗi cycle entry. |
| **7. Reflection / Journal** | **PARTIAL** | Journal khi mở/đóng qua `JournalService`. **Reject signal** chủ yếu `rejected_signals` list + `blocked_signals.json` / log — không phải mọi reject đều có journal DB row. |
| **8. Market optimization** | **PASS** | Live Futures: `_use_futures_only_for_quotes()` → fapi + WS; tránh spot 400. Klines TTL `KLINES_CACHE_TTL`. |
| **9. Compute** | **PASS** | Token intel cache 120s (`cycle.py` `_TOKEN_INTEL_CACHE`). |
| **10. Scheduler** | **WARN** | `cycle_interval_seconds=10` + nhiều HTTP/OpenAI trong một job → log `maximum number of running instances reached` — cycle có thể > interval. |

---

## B. BUG / inconsistency list (file + mô tả)

| # | File | Mô tả | Mức |
|---|------|--------|-----|
| B1 | `core/risk/engine.py` + `scale_in_engine.py` | **Sizing / daily loss USD** dùng `settings.default_capital_usd`; **scale-in exposure** dùng `portfolio.capital_usd`. Hai số có thể lệch (ví dụ DB 1000 vs thực tế 500). | P1 |
| B2 | `apps/worker/runner.py` | Vòng `rejected_signals`: vừa `logger.info` vừa `print` → **trùng dòng** nếu stdout vào cùng file log. | P2 |
| B3 | `core/orchestration/cycle.py` | Decision rải trong một file lớn — khó audit “mọi nhánh có reason”. | P2 (kiến trúc) |
| B4 | Checklist §7.1 | **Reject** không luôn ghi journal chuẩn — chỉ list + file phụ. | P2 |
| B5 | Scheduler | Job cycle overlap khi tải nặng — **không phải bug logic** nhưng ảnh hưởng độ ổn định. | P1 ops |

**Đã fix (P0):** Worker tính `daily_realized_r` với mọi `risk_usd > 0` trong khi Dashboard lọc `>= 0.01` → Kill switch sai số khổng lồ. **Sửa:** `core/risk/daily_r.py` + dùng chung trong `cycle.py` và `dashboard/app.py`.

---

## C. Auto-fix đã làm / đề xuất patch

| Patch | Trạng thái |
|-------|------------|
| `sum_daily_realized_r_from_trades` + `MIN_RISK_USD_FOR_R_AGGREGATION` | **Đã merge** |
| Trả thêm `daily_realized_r` / `daily_realized_usd` trong `cycle.run()` return | **Đã thêm** — Worker/Dashboard có thể log đối chiếu |
| Bỏ `print` trùng trong `runner.py` (reject loop) | **Đề xuất** — chỉ `logger.info` |
| `RiskEngine`: `risk_dollars = portfolio.capital_usd * risk_pct` (truyền từ cycle) | **Đề xuất P1** — cần refactor signature |
| `decision_engine.py` gom nhánh mở lệnh | **Đề xuất P2** — refactor lớn |

---

## D. Optimization plan

- **P0:** Đồng nhất daily R (xong).  
- **P1:** Giảm overlap scheduler (tăng interval hoặc tách OpenAI khỏi hot path); căn chỉnh `default_capital_usd` với `portfolio.capital_usd` hoặc sync vốn từ Binance vào DB.  
- **P2:** StateManager; journal cho mọi reject; dedup signal theo (symbol, strategy, side) trong TTL; tách `decision_engine`.

---

## E. Mapping checklist nhanh

- **1.1 flow:** Có trong `cycle.run`; reason log một phần.  
- **1.2 tắt OpenAI:** Entry/exit rule-based; AI optional cho SL/TP / review.  
- **1.3 guards:** `max_positions_per_symbol`, scale-in (`scale_in.v1.json`), volatility guard, position age cho AI.  
- **4.3 TP/SL churn:** Throttle 120s + cache algo 5s.  
- **5.x cache:** Như bảng Execution.  
- **8.1:** Futures-only quotes khi live Futures bật.  
- **11 red flags:** Đã có case “daily R ảo” — đã xử lý bằng `daily_r.py`.

---

## F. Đã triển khai (v6 tối ưu — 2026-03-19)

| Hạng mục | Thay đổi |
|----------|----------|
| **B1 Risk capital** | `core/risk/engine.py`: `effective_risk_capital_usd()`, `assess(..., capital_usd_for_risk=)`. Daily loss USD + `risk_dollars` dùng `cap` thay vì luôn `default_capital_usd`. |
| **Cycle** | `cycle.py`: `risk_capital_usd` từ `portfolio.capital_usd` (fallback default), truyền vào mọi `risk.assess`. Return thêm `risk_capital_usd`. |
| **Binance live** | `runner.py`: mỗi cycle cập nhật `portfolio.capital_usd = available balance` cùng lúc `cash_usd` — đồng bộ vốn risk với sàn. |
| **B2 log trùng** | `runner.py`: bỏ `print` trong vòng reject risk (chỉ `logger.info`). |
| **Scheduler / overlap** | `config.py`: `cycle_interval_seconds` mặc định **10 → 15** giây. |
| **Worker summary** | Dòng tóm tắt cycle thêm `risk_capital=...`. |

*Cập nhật khi refactor thêm: ghi vào cuối file.*
