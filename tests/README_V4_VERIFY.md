# Xác minh v4 — Hệ thống vận hành đúng yêu cầu document/v4

## Bốn tiêu chí "thông minh thật"

| Tiêu chí | Cách kiểm tra | Kết quả |
|----------|----------------|---------|
| **1. Biết vì sao vừa vào lệnh** | Journal lưu `reasons`, `market_context`, `risk_score`, `timeframe`, `side`. Cycle gọi `build_entry_context` và truyền vào `create_entry`. | OK — test_journal_service_create_and_outcome + test_1 |
| **2. Biết vì sao vừa thua** | Journal có `exit_reason`, `mistake_tags`. `record_outcome_from_close` ghi khi đóng lệnh. | OK — test_2 + test_journal_service_create_and_outcome |
| **3. Biết nên sửa gì** | Reflection trả `suggested_actions` (JSON). Optimizer ghi vào `strategy.candidate.json`. | OK — test_3, `run_reflection.py` in suggested_actions |
| **4. Biết sửa xong có tốt hơn thật không** | Backtest với active vs candidate, `check_promotion`, script `run_backtest.py` + `promote_candidate.py`. | OK — test_4, `run_backtest.py` chạy so sánh |

## Chạy kiểm tra

```bash
cd trading-lab-pro-v3
python tests/test_v4_requirements.py
```

Kỳ vọng: `All checks passed (v4 requirements).`

## Script thực tế đã chạy

- `python scripts/migrate_journal_v4_columns.py` — cột journal v4 đã tồn tại.
- `python scripts/run_reflection.py` — reflection chạy, in Suggested actions + Reflection summary.
- `python scripts/run_backtest.py BTC` — fetch klines, so sánh active vs candidate, in Promotion pass.

## Test bao gồm

1. Journal entry context (reasons, market_context, risk_score, timeframe).
2. Journal exit context (exit_reason, mistake_tags).
3. JournalService create_entry + add_outcome (tích hợp DB).
4. Reflection engine output (suggested_actions, reflection_summary, mistakes_found).
5. Optimizer + candidate config.
6. Promotion rules + backtest với strategy_config.
7. Rejected signals log (blocked trades).
8. Config files tồn tại (strategy.active, candidate, promotion_rules).
