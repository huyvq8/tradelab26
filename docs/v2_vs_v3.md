# So sánh Trading Lab Pro v2 và v3

## Tóm tắt

- **v2**: Ứng dụng **đã triển khai đầy đủ**: DB (SQLAlchemy/PostgreSQL), CMC, 4 strategy thật, RiskEngine, execution có slippage/fee, journal + reflection + recommendation (rule-based), orchestration cycle, API, dashboard, worker, Docker.
- **v3**: **Khung mới** với các module **mới** (backtest, regime, Telegram, AI reflection) nhưng phần lớn code hiện tại là **placeholder/stub**. v3 chưa có DB, chưa có risk, chưa dùng CMC, strategy/reflection/recommendation đều giả.

---

## Cải tiến / điểm mới trong v3 (theo README)

| Tính năng | v2 | v3 | Ghi chú |
|-----------|----|----|--------|
| **Backtest engine** | Không | Có module `core/backtest` | v3: stub (trả dict fake). v2 có task ST-3 “backtest pipeline” trong implementation_tasks. |
| **Telegram reporting** | Không | Có `integrations/telegram/report.py` | v3: wrapper gửi message; chưa gắn vào daily report. |
| **Auto watchlist builder** | Không (symbols cố định/CLI) | Đọc trong README | v3: chưa có code tương ứng. |
| **Market regime detection** | Có trong `core/market_data/features.py` (`derive_regime`) | Module riêng `core/regime/detector.py` | v2: 3 regime (high_momentum, risk_off, balanced). v3: 3 regime (high_vol_trend, range, mixed) theo volatility/trend, nhưng detector nhận (volatility, trend) chưa có nguồn số liệu thật. |
| **Recommendation engine improvements** | Có, rule-based (DailyReportService + RecommendationEngine) | Có `core/recommendation/engine.py` | v3: stub (suggestion cố định). v2: rule theo PnL, top_pattern, open_positions. |
| **Daily AI reflection loop** | Có ReflectionEngine + DailyReport (rule-based) | Có `core/reflection/engine.py` | v3: stub (list “Review trade on X”). v2: thống kê journal, strategy_counts, lessons, mistakes. |

---

## So sánh từng lớp

### 1. Market Data

| | v2 | v3 |
|---|----|----|
| **Nguồn** | CoinMarketCap (API thật, fallback mock) | `fetch_prices()` random, không API |
| **Output** | MarketQuote (price, percent_change_24h, volume_24h, market_cap) | Dict symbol -> price (giả) |
| **Regime** | `derive_regime(price, change_24h, volume_24h)` trong features | `detect_regime(volatility, trend)` trong module regime (chưa có đầu vào thật) |

**Kết luận**: v3 thêm ý tưởng regime tách riêng nhưng data layer yếu hơn v2; cần đưa CMC (hoặc nguồn thật) + feed volatility/trend cho regime.

---

### 2. Strategy Engine

| | v2 | v3 |
|---|----|----|
| **Cấu trúc** | BaseStrategy + 4 class (TrendFollowing, BreakoutMomentum, MeanReversion, LiquiditySweep) | `run_strategies(prices)` trả list signals |
| **Logic** | Theo price, change_24h, volume_24h, regime | Placeholder: `price % 2 > 1` |
| **Signal** | StrategySignal (symbol, side, confidence, entry, stop, take_profit, rationale, regime) | Dict symbol, action, confidence |

**Kết luận**: v3 chưa có strategy thật; nên port 4 strategy từ v2 (và base/signal) vào v3.

---

### 3. Risk Engine

| | v2 | v3 |
|---|----|----|
| **Tồn tại** | Có `core/risk/engine.py` (max concurrent, daily loss, position size theo stop) | README nhắc “risk filter” nhưng **không có** module risk trong code |

**Kết luận**: v3 thiếu hoàn toàn risk; bắt buộc port RiskEngine từ v2 (P0).

---

### 4. Execution Simulator

| | v2 | v3 |
|---|----|----|
| **Lưu trữ** | SQLAlchemy (Position, Trade) | In-memory list dict |
| **Slippage / fee** | Có (sim_slippage_bps, sim_fee_bps) | Không |
| **Mở/đóng** | open_position, close_position với DB session | execute_signals → list trades (không đóng theo stop/TP) |

**Kết luận**: v3 cần port execution + persistence (DB) từ v2.

---

### 5. Portfolio

| | v2 | v3 |
|---|----|----|
| **Model** | Portfolio, Position, Trade, DailySnapshot (DB) | Class Portfolio in-memory (balance, positions[]) |
| **API** | GET /portfolio/summary (từ DB) | GET /portfolio (từ object trong memory) |

**Kết luận**: v3 cần DB và model portfolio như v2 để có PnL, snapshot, báo cáo.

---

### 6. Journal

| | v2 | v3 |
|---|----|----|
| **Lưu** | JournalEntry (DB), JournalService (create_entry, add_outcome) | JSON file `storage/trade_log.json`, log_trades(trades) |
| **Nội dung** | entry_reason, risk_plan, regime, setup_score, result_summary, lessons, mistakes | Chỉ timestamp + list trades (symbol, action, size) |

**Kết luận**: v3 cần port journal sang DB và schema đủ cho reflection (entry + outcome).

---

### 7. Reflection & Recommendation

| | v2 | v3 |
|---|----|----|
| **Reflection** | ReflectionEngine: build_daily_reflection (journal_count, realized_pnl, strategy_counts, lessons, mistakes, top_pattern) | run_reflection(trades) → list string “Review trade on X” |
| **Recommendation** | Rule-based (giảm risk sau lỗ, volume confirmation, không thêm position khi ≥3) | recommend_next_day(journal) → dict suggestion cố định |
| **Daily report** | DailyReport (DB), headline + summary_markdown + recommendations_markdown | Không có |

**Kết luận**: v3 cần port reflection + recommendation thật và daily report; sau đó mới nâng cấp “improvements” và “Daily AI reflection”.

---

### 8. Backtest

| | v2 | v3 |
|---|----|----|
| **Backtest** | Chỉ trong implementation_tasks (ST-3) | Module `core/backtest/engine.py` có sẵn nhưng stub (trả trades/winrate/profit_factor giả) |

**Kết luận**: v3 đã có “chỗ” backtest; cần implement thật (chạy strategy trên series giá, ghi trades, tính metrics).

---

### 9. Telegram

| | v2 | v3 |
|---|----|----|
| **Telegram** | Không | integrations/telegram/report.py: send_report(token, chat_id, text) |

**Kết luận**: v3 thêm Telegram; cần gắn vào luồng “end-of-day report” (gửi tóm tắt/khuyến nghị).

---

### 10. Orchestration & Apps

| | v2 | v3 |
|---|----|----|
| **Cycle** | SimulationCycle: client → regime → strategies → risk → execution → journal (có DB) | run_cycle: fetch_prices → run_strategies → execute_signals → log_trades (không risk, không DB) |
| **API** | /health, POST /cycle/run, POST /reports/daily, GET /portfolio/summary | /health, GET /portfolio |
| **Dashboard** | Streamlit: cash, PnL, positions, trades, reflection, recommendations, reports, chart | Streamlit: 2 metric cố định + text |
| **Worker** | APScheduler: mỗi 5 phút cycle, 23:55 daily report | runner.py: 1 lần run_cycle (không schedule, không report) |

**Kết luận**: v3 cần luồng cycle đầy đủ (risk + DB), API mở rộng, dashboard và worker định kỳ + daily report.

---

## Tổng hợp: v3 “cải tiến thêm” gì so với v2

- **Ý tưởng / hướng**: Backtest, Telegram, regime tách riêng, recommendation/AI reflection “improvements”.
- **Code hiện tại**: Hầu hết là stub; so với v2 thì v3 **thiếu** DB, risk, data thật, strategy thật, execution thật, journal/reflection/report thật.

Để v3 thực sự “cải tiến” so với v2 cần:

1. Đưa nền tảng v2 vào v3 (DB, config, risk, execution, portfolio, journal, reflection, recommendation, cycle, API, dashboard, worker).
2. Implement thật các module mới: backtest engine, regime (nối với data thật), Telegram (gắn daily report), auto watchlist (nếu làm), recommendation improvements, daily AI reflection (LLM).

Thứ tự thực hiện chi tiết nằm trong `docs/execution_plan.md`.
