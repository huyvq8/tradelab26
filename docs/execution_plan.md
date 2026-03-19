# Kế hoạch thực hiện Trading Lab Pro v3

Thứ tự thực hiện để v3 đạt nền tảng v2 rồi bật các tính năng mới (backtest, Telegram, regime, AI reflection). Làm theo phase; mỗi phase có thể tách thành task nhỏ và tick khi xong.

---

## Phase 0: Nền tảng (P0)

*Mục tiêu: DB, config, có thể chạy cycle với data thật và lưu kết quả.*

| Bước | Việc | Tham chiếu v2 | Ghi chú |
|------|------|----------------|---------|
| 0.1 | **Config** (env, settings): database_url, redis_url, cmc_api_key, openai_api_key, capital, risk_pct, max_daily_loss_pct, max_concurrent_trades, sim_fee_bps, sim_slippage_bps, telegram_token/chat_id | core/config.py, .env.example | Tạo core/config.py + .env.example |
| 0.2 | **DB**: SQLAlchemy engine, SessionLocal, Base, get_db; tạo bảng (create_all hoặc Alembic) | core/db.py | Tạo core/db.py |
| 0.3 | **Models**: Portfolio, Position, Trade, DailySnapshot, JournalEntry, DailyReport | core/portfolio/models.py, journaling/models.py, reporting/models.py | Có thể đặt core/models/ hoặc giữ portfolio, journal, reporting |
| 0.4 | **Alembic** (khuyến nghị): init, migration đầu cho toàn bộ bảng | v2 implementation_tasks OPS-3 | Tránh create_all trong production |

---

## Phase 1: Data + Strategy + Risk + Execution (P0–P1)

*Mục tiêu: Cycle dùng data thật, strategy thật, risk, execution có DB.*

| Bước | Việc | Tham chiếu v2 | Ghi chú |
|------|------|----------------|---------|
| 1.1 | **Market data**: client CMC (hoặc giữ fetch_prices nhưng nối CMC), trả quote (price, percent_change_24h, volume_24h, market_cap) | core/market_data/client.py | Thay/core bổ sung core/market_data/fetch.py |
| 1.2 | **Regime**: Nguồn số cho volatility/trend (từ quote hoặc OHLCV sau này); gọi detect_regime(volatility, trend); thống nhất tên regime với strategy (high_momentum / risk_off / balanced hoặc high_vol_trend / range / mixed) | core/market_data/features.py, core/regime/detector.py | Có thể map v2 derive_regime → v3 detect_regime |
| 1.3 | **Strategy base + signal**: BaseStrategy, StrategySignal (symbol, side, confidence, entry, stop_loss, take_profit, rationale, regime) | core/strategies/base.py | Tạo hoặc refactor core/strategies/ |
| 1.4 | **4 strategies**: TrendFollowing, BreakoutMomentum, MeanReversion, LiquiditySweep; build_strategy_set() | core/strategies/implementations.py | Port từ v2 vào engine hoặc implementations |
| 1.5 | **Risk engine**: RiskEngine.assess(signal, cash, open_positions, daily_realized_pnl) → RiskDecision (approved, size_usd, reason); rule max_concurrent, daily loss limit, position size từ stop distance | core/risk/engine.py | v3 chưa có risk → tạo core/risk/ |
| 1.6 | **Execution simulator**: open_position(db, portfolio_id, signal, size_usd) → Position + Trade (slippage, fee); close_position(db, position, exit_price, note) → Trade | core/execution/simulator.py | Port từ v2, thay in-memory bằng DB |
| 1.7 | **Đóng lệnh theo stop/TP**: Trong cycle hoặc worker: lấy giá hiện tại (từ CMC hoặc mock), so sánh với stop_loss/take_profit, gọi close_position + journal add_outcome | v2 EX-1, JN-1 | Cần 1 bước “check & close” trong orchestration |

---

## Phase 2: Journal + Reflection + Recommendation + Report (P1)

*Mục tiêu: Journal đầy đủ, reflection thật, recommendation rule-based, daily report.*

| Bước | Việc | Tham chiếu v2 | Ghi chú |
|------|------|----------------|---------|
| 2.1 | **Journal service**: create_entry(signal, risk_reason, setup_score, trade_id); add_outcome(journal_id, result_summary, lessons, mistakes); gọi add_outcome khi đóng lệnh | core/journaling/service.py | Thay/song song core/journal/logger.py (JSON) bằng DB |
| 2.2 | **Reflection engine**: build_daily_reflection(db, target_date) → journal_count, realized_pnl, strategy_counts, lessons, mistakes, top_pattern | core/reflection/service.py | Thay stub run_reflection bằng logic v2 |
| 2.3 | **Recommendation engine**: next_steps(reflection, open_positions) → list[str] (rule: giảm risk sau lỗ, volume confirmation, không thêm position khi ≥3, v.v.) | core/recommendation/service.py | Thay stub recommend_next_day |
| 2.4 | **Daily report service**: generate(db, target_date) → DailyReport (headline, summary_markdown, recommendations_markdown); dùng ReflectionEngine + RecommendationEngine | core/reporting/service.py | Tạo core/reporting/ nếu chưa có |

---

## Phase 3: Orchestration + API + Dashboard + Worker (P0–P1)

*Mục tiêu: Một cycle hoàn chỉnh từ API/worker, dashboard xem được.*

| Bước | Việc | Tham chiếu v2 | Ghi chú |
|------|------|----------------|---------|
| 3.1 | **Orchestration cycle**: Fetch data → regime → for each symbol run strategies → risk.assess → execution.open_position + journal.create_entry; (option) check & close positions by stop/TP → close_position + journal.add_outcome; snapshot | core/orchestration/cycle.py | Tạo core/orchestration/ hoặc mở rộng worker |
| 3.2 | **API**: GET /health; POST /cycle/run (symbols, portfolio_name); POST /reports/daily (date); GET /portfolio/summary | apps/api/main.py | Mở rộng apps/api/server.py |
| 3.3 | **Dashboard**: Cash, PnL, open positions, recent trades, reflection (hoặc summary), recommendations, daily reports, equity/snapshot chart | apps/dashboard/app.py | Port logic v2 dashboard |
| 3.4 | **Worker**: Định kỳ run cycle (vd 5 phút); cuối ngày generate daily report; có thể dùng APScheduler | apps/worker/runner.py, v2 scheduler.py | Chạy scheduler trong runner hoặc script riêng |
| 3.5 | **Scripts**: seed_db (tạo portfolio mặc định); run_cycle (CLI symbols); generate_daily_report (CLI date) | v2 scripts/ | Tùy chọn, tiện vận hành |

---

## Phase 4: Tính năng mới v3 (P1–P2)

*Mục tiêu: Backtest thật, regime rõ ràng, Telegram, cải tiến recommendation/AI.*

| Bước | Việc | Ghi chú |
|------|------|--------|
| 4.1 | **Backtest engine**: Đầu vào: series giá (hoặc OHLCV) + strategy; chạy strategy theo thời gian, ghi danh sách trades; tính win rate, profit factor, expectancy, max drawdown (và R-multiple nếu có) | core/backtest/engine.py | Thay stub bằng logic thật; dùng cùng BaseStrategy nếu có thể |
| 4.2 | **Regime với data thật**: Đảm bảo volatility/trend lấy từ quote hoặc OHLCV; map regime với strategy (regime filter) | core/regime/detector.py, market_data | Phase 1.2 đã nối; có thể bổ sung regime-aware position size (P2) |
| 4.3 | **Telegram reporting**: Sau khi generate daily report, gọi send_report(telegram_token, chat_id, text) với headline + summary hoặc link; cấu hình token/chat_id trong config | integrations/telegram/report.py | Gắn vào bước “daily report” trong worker hoặc API |
| 4.4 | **Auto watchlist builder** (nếu làm): Từ volume/market cap/regime hoặc config, sinh list symbol cho cycle thay vì cố định | README v3 | Có thể đơn giản: config list + filter theo cap/volume |
| 4.5 | **Recommendation improvements**: Thêm rule từ backtest (vd strategy nào đang tốt/xấu), từ repeated mistakes; vẫn “chỉ đề xuất”, không tự sửa strategy | core/recommendation/engine.py | Mở rộng next_steps |
| 4.6 | **Daily AI reflection**: Gọi LLM (OpenAI hoặc khác) với prompt daily_review / trade_postmortem / next_day_plan; ghi kết quả vào DailyReport hoặc bảng riêng | v2 RF-2, RF-4, prompts/ | Cần OPENAI_API_KEY; có thể optional |

---

## Phase 5: Analytics + Vận hành (P1–P2)

*Mục tiêu: Chỉ số chuyên nghiệp, vận hành ổn định.*

| Bước | Việc | Ghi chú |
|------|------|--------|
| 5.1 | **Analytics**: Win rate, profit factor, expectancy, max drawdown, avg R-multiple, Sharpe (giả lập); setup accuracy theo regime | v2 AN-1, AN-2 | Module core/analytics/ hoặc trong reporting |
| 5.2 | **Dashboard metrics**: Hiển thị các chỉ số trên trong dashboard | v2 AN-4 | Tab hoặc section mới |
| 5.3 | **Repeated mistake detection**: Từ journal mistakes + trades, nhóm pattern → đưa vào reflection/recommendation | v2 RF-1 | Mở rộng ReflectionEngine hoặc recommendation |
| 5.4 | **Docker / env**: docker-compose (db, redis, api, dashboard, worker); production dùng PostgreSQL; .env.example đầy đủ | v2 docker-compose, OPS-1, OPS-2 | Worker chạy trong compose hoặc document rõ |
| 5.5 | **Docs**: risk_policy.md, operator_manual (guardrails, 30–50 samples trước khi đổi tham số) | v2 DOC-1, DOC-3 | Trong docs/ |

---

## Thứ tự thực hiện gợi ý (checklist)

- **Phase 0** → **Phase 1** → **Phase 2** → **Phase 3**: Để có “v2 tương đương” trong v3 (chạy được cycle, report, dashboard).
- **Phase 4**: Bật backtest, regime thật, Telegram, recommendation/AI.
- **Phase 5**: Analytics + vận hành + tài liệu.

Trong từng phase, ưu tiên: **0.1 → 0.2 → 0.3** rồi 1.1 → 1.2 → … → 1.7, sau đó 2.1 → … 3.5, rồi 4.x và 5.x. Có thể đánh dấu [x] khi hoàn thành từng bước trong file này hoặc trong issue/task tracker.
