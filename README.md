# Trading Lab Pro

**Nhánh code duy nhất.** Paper-trading research environment: multi-strategy, risk control, journal + reflection, backtest, Telegram, AI-assisted daily report.

**Đặc tả đầy đủ:** [docs/complete_version_spec.md](docs/complete_version_spec.md)  
**Kế hoạch thực hiện:** [docs/execution_plan.md](docs/execution_plan.md)

## Tính năng

- **Market data** — CMC quote; regime (volatility/trend)
- **Multi-strategy** — Trend, Breakout, Mean Reversion, Liquidity Sweep
- **Risk engine** — Max concurrent, daily loss limit, position sizing
- **Paper execution** — Slippage, fee; đóng theo stop/TP
- **Journal + Reflection** — DB journal; daily reflection; recommendation (rule-based)
- **Backtest** — Historical strategy testing
- **Telegram** — Gửi báo cáo cuối ngày
- **Daily AI reflection** — Optional LLM (prompts)

## Quick start

**Chạy tất cả (API + Worker + Dashboard) và mở trình duyệt:** double-click `run_all.bat` → dashboard tại http://localhost:8501.

Hoặc chạy tay:
```bash
cp .env.example .env   # điền đủ biến (CMC_API_KEY, TELEGRAM_*, ...) — xem .env.example
pip install -r requirements.txt
python scripts/seed_db.py
python apps/api/server.py
```

- API: http://localhost:8000
- **Dashboard**: chạy `start_dashboard.bat` hoặc `streamlit run apps/dashboard/app.py` → mở trình duyệt **http://localhost:8501** (báo cáo theo thời gian thực, watchlist, tín hiệu, PnL, Telegram)
- Worker: `python apps/worker/runner.py`

## Cấu trúc core/

| Module      | Mô tả |
|------------|--------|
| config     | Settings từ .env |
| db         | SQLAlchemy engine, Session, Base |
| portfolio  | Portfolio, Position, Trade, DailySnapshot |
| journal    | JournalEntry, service |
| reporting  | DailyReport, DailyReportService |
| market_data| CMC client, fetch |
| regime     | derive_regime, detect_regime |
| strategies | BaseStrategy, 4 strategies |
| risk       | RiskEngine |
| execution  | PaperExecutionSimulator |
| reflection | ReflectionEngine, repeated_mistakes |
| recommendation | RecommendationEngine |
| backtest   | run_backtest (OHLCV → metrics) |
| analytics  | compute_metrics (win rate, PF, expectancy, max DD) |
| orchestration | SimulationCycle |

**Integrations:** Telegram (gửi báo cáo cuối ngày khi cấu hình token/chat_id).

**Docs:** [docs/complete_version_spec.md](docs/complete_version_spec.md), [docs/risk_policy.md](docs/risk_policy.md), [docs/operator_manual.md](docs/operator_manual.md), [docs/redis_setup.md](docs/redis_setup.md), [docs/telegram_and_openai.md](docs/telegram_and_openai.md) (khi nao gui Telegram / dung OpenAI).

**Redis (tùy chọn):** Chạy `ensure_redis.bat` hoặc `python scripts/ensure_redis.py` để kiểm tra và thử tự khởi chạy Redis bằng Docker.
