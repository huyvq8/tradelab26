# Entry context gates (post-pump / structure)

## Vị trí trong pipeline

`cycle._apply_entry_edge_pipeline`:

1. Combo performance (`COMBO_BLOCKED_EDGE`)
2. **Entry context gates** (`CONTEXT_GATE_*`) — file này
3. Entry timing (`ENTRY_*`)
4. Signal levels (structural passthrough hoặc ATR adjust)

## Cấu hình

- Mặc định: `config/entry_context_gates.v1.json`
- Overlay thử nghiệm: env `ENTRY_CONTEXT_GATES_CONFIG` → merge sâu lên base (xem `core/experiments/paths.py`)

## Phase 1 — native signal

- `extension_score_max`, `setup_quality_min`, `entry_style_allowlist` / `blocklist`
- `when_native_fields_missing`: `pass` | `reject` (fallback không có field → reject nếu `reject`)

## Phase 2 — recent context (từ klines)

- `min_distance_from_high_pct`: % giá phải dưới đỉnh lookback (null = tắt)
- `reject_if_recent_rejection_from_high`: nến bóng trên lớn gần đỉnh
- `min_bars_since_local_high`: không vào đúng nến tạo đỉnh
- `reject_on_failed_breakout`: breakout rồi hồi (cần peak > prior range + giveback)

## Phase 3 — pullback quality

- Mặc định `enabled: false`
- `max_pullback_speed_atr_per_bar`, `min_pullback_volume_ratio`, reclaim EMA, broke swing low

## Observability

- Reject: `decision_log.jsonl` `entry_rejected` + `reason_code` `CONTEXT_GATE_*` + payload `phase1_native_signal` / `phase2_recent_context` / `phase3_pullback_quality`
- Pass toàn bộ: `log_pass_snapshot: true` → event `entry_context_gates_pass` (ồn)
- Pass có lọc: `pass_snapshot_debug.enabled` + `symbols` / `strategies` (list rỗng = wildcard dimension đó) → chỉ log pass khớp

## Đo lường / rollout

- `scripts/validate_edge_patch_report.py` → `decision_log.entry_context_gates` trong JSON report
- `docs/entry_context_rollout.md` — KPI, rollback, hard vs soft, giai đoạn 1 (SIREN-style)
