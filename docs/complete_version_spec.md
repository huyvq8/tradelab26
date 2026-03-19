# Trading Lab Pro — Version hoàn chỉnh (tổng hợp v2 + v3)

Một phiên bản duy nhất gộp nền tảng v2 (đã chạy đầy đủ) và tính năng mới v3 (backtest, Telegram, regime, AI reflection).

---

## Mục tiêu

- Paper trading chuyên nghiệp: multi-strategy, risk control, journal + reflection, không lệnh thật.
- Học từ tập mẫu: recommendation chỉ đề xuất; cập nhật chiến lược chỉ sau backtest/forward test + duyệt.
- Tích hợp AI: daily reflection, post-mortem, next-day plan (LLM optional); Telegram báo cáo.

---

## Stack

- **Python 3.11+**
- **FastAPI** — API
- **SQLAlchemy 2** — ORM
- **PostgreSQL** (production) / SQLite (dev)
- **Redis** — cache/queue (optional)
- **Streamlit** — dashboard
- **APScheduler** — worker định kỳ
- **httpx** — CMC / HTTP
- **python-telegram-bot** — Telegram

---

## Kiến trúc 6 lớp

1. **Market Data** — CMC (và sau này exchange), quote + regime (volatility/trend).
2. **Strategy Engine** — Nhiều strategy (trend, breakout, mean reversion, liquidity sweep); signal có entry/stop/tp/confidence/rationale.
3. **Risk Engine** — Max concurrent, daily loss limit, position size theo stop; correlation/anti-hedge (P1).
4. **Execution Simulator** — Paper only: open/close, slippage, fee; đóng theo stop/TP.
5. **Journal + Memory** — JournalEntry (DB), liên kết trade, add_outcome khi đóng lệnh.
6. **Reflection + Report** — ReflectionEngine, RecommendationEngine (rule-based), DailyReport; Telegram; optional LLM.

---

## Module chính (core/)

| Module | Nội dung |
|--------|----------|
| **config** | Settings (env): database_url, cmc_api_key, openai_api_key, capital, risk_pct, max_daily_loss, max_concurrent_trades, sim_fee/slippage, telegram_token/chat_id. |
| **db** | Engine, SessionLocal, Base, get_db. |
| **portfolio** | Models: Portfolio, Position, Trade, DailySnapshot. |
| **journal** | Model: JournalEntry. Service: create_entry, add_outcome. |
| **reporting** | Model: DailyReport. Service: generate daily report. |
| **market_data** | Client CMC (quote); fetch; chuẩn hóa price/volume/change. |
| **regime** | detect_regime(volatility, trend) — high_vol_trend / range / mixed (hoặc map high_momentum/risk_off/balanced). |
| **strategies** | BaseStrategy, StrategySignal; 4 strategies (TrendFollowing, Breakout, MeanReversion, LiquiditySweep); build_strategy_set. |
| **risk** | RiskEngine.assess → RiskDecision (approved, size_usd, reason). |
| **execution** | PaperExecutionSimulator: open_position, close_position (DB, slippage, fee). |
| **reflection** | ReflectionEngine: build_daily_reflection (journal_count, realized_pnl, strategy_counts, lessons, mistakes, top_pattern). |
| **recommendation** | RecommendationEngine: next_steps(reflection, open_positions) — rule-based. |
| **backtest** | run_backtest(data/strategy) → trades, win_rate, profit_factor, expectancy, max_dd. |
| **orchestration** | SimulationCycle: data → regime → strategies → risk → execution → journal; check & close stop/TP; snapshot. |

---

## Ứng dụng (apps/)

- **api** — FastAPI: /health, POST /cycle/run, POST /reports/daily, GET /portfolio/summary.
- **dashboard** — Streamlit: cash, PnL, positions, trades, reflection, recommendations, daily reports, equity chart.
- **worker** — APScheduler: mỗi 5 phút run cycle; 23:55 daily report; (optional) gửi Telegram.

---

## Tích hợp (integrations/)

- **telegram** — send_report(token, chat_id, text); gọi sau khi generate daily report.

---

## Automation loop

1. Fetch market data (CMC).
2. Detect regime (volatility/trend).
3. Generate strategy signals.
4. Risk filter.
5. Simulate execution (open; check & close stop/TP).
6. Update portfolio.
7. Log journal (entry + outcome khi đóng).
8. End-of-day: reflection → daily report → recommendations.
9. (Optional) Gửi Telegram; (optional) LLM reflection.

---

## Vận hành

- **Config**: Copy .env.example → .env; điền CMC_API_KEY, (optional) OPENAI_API_KEY, TELEGRAM_TOKEN/CHAT_ID.
- **DB**: Alembic migrations hoặc create_all dev; seed portfolio.
- **Chạy**: docker compose (db, api, dashboard, worker) hoặc python apps/api/server.py + python apps/worker/runner.py + streamlit run apps/dashboard/app.py.
- **Scripts**: seed_db, run_cycle (--symbols), generate_daily_report (--date).

Đây là đặc tả version hoàn chỉnh; triển khai theo `execution_plan.md` (Phase 0 → 5).
