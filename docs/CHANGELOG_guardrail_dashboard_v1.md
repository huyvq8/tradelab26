# Changelog — Guardrail dashboard, metrics, execution, learning (2026-03-20)

## Summary

- **Dashboard**: Bảng ứng viên và `decision_log` (entry_opened / entry_rejected) thêm cột guardrail: `stop_distance_pct`, `final_notional_pct_of_equity`, `risk_efficiency_ratio`, `stop_floor_applied`, `notional_cap_applied`, `mr_reversal_confirmation`, `rejection_stage`. Expander **Guardrail effectiveness (24h / 7d) + MR report**.
- **Metrics**: `core/observability/guardrail_metrics.py` — đếm reject (stop tight, MR no reversal, notional cap), avg notional/stop trên opened; MR summary; proxy fast stopout từ DB (nếu có `portfolio_id`).
- **Learning**: `data/guardrail_learning.jsonl` (append từ `log_decision` cho entry_opened / entry_rejected). Journal `market_context.guardrail_snapshot` khi mở lệnh; khi đóng, merge `close_outcome_tags` / `hold_minutes`.
- **Binance execution**: `close_position` / `reduce_position` — lấy `positionRisk` để clamp qty, làm tròn LOT, **không** fallback Paper khi lỗi; log warning và raise / return None.
- **Cycle**: Không fallback Paper khi đóng thất bại trên live Binance (`_close_position_with_optional_paper`). Partial TP chỉ log `PARTIAL_TP` khi trade partial thực sự được tạo.
- **Brain reflex**: Cùng nguyên tắc — không paper-close khi live fail; partial chỉ khi `reduce_position` trả trade.
- **Dedupe**: `reject_log_dedupe` — bỏ `MR_NO_REVERSAL_CONFIRMATION` / `STOP_DISTANCE_TOO_TIGHT` khỏi `never_dedupe_codes`, thêm cooldown + state keys. **Telegram**: `config/telegram_signal_dedupe.v1.json` + `should_send_signal_telegram` trong worker.
- **Profiles**: `load_profit_config_resolved()` merge `config/mr_guardrail_profiles.v1.json` theo `entry_guardrail_profile` hoặc `ENTRY_GUARDRAIL_PROFILE` — mẫu `MR_SAFE` / `MR_BALANCED`.
- **MR reversal**: `mr_long_has_reversal_confirmation` hỗ trợ nến dạng `Kline1h` (dashboard).

## Tests

- `tests/test_guardrail_observability.py` — row flatten, MR+kline dataclass, reject dedupe MR, telegram dedupe, profile merge.

## Config / env

- `ENTRY_GUARDRAIL_PROFILE=MR_SAFE` hoặc `MR_BALANCED` (optional).
- `profit.active.json`: `"entry_guardrail_profile": "MR_SAFE"`.
