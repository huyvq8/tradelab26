# Acceptance criteria — Negative edge patch (phase 2)

Patch **chỉ được coi thành công** sau khi có **sample đủ lớn** (khuyến nghị: ≥ 80–100 lệnh đóng trong cửa sổ “after”, hoặc ≥ 30 ngày paper cùng cấu hình).

**Công cụ:** `scripts/validate_edge_patch_report.py` xuất `sample_sufficiency` (mặc định `min_closed_after=80`, `min_decision_lines=200`, `min_entry_funnel=40`). Chỉ kết luận acceptance khi `sample_sufficiency.ok == true` hoặc bạn chủ động hạ ngưỡng và ghi rõ lý do.

## Bắt buộc (tất cả đạt)

1. **SL nhanh**  
   - Số lệnh **lỗ** đóng trong **&lt; 5 phút** giảm **rõ rệt** so với cửa sổ “before” (cùng portfolio / symbol mix).  
   - Metric: `sl_loss_lt_5min_count` / `sl_loss_lt_5min_pct_of_losses` trong `scripts/validate_edge_patch_report.py`.

2. **Combo âm edge**  
   - Số **lệnh mở** (hoặc notional) trên combo đã xác định xấu (vd. `trend_following|SIREN`, `breakout_momentum|SIREN`) **giảm**; `COMBO_BLOCKED_EDGE` và/hoặc `soft_mult` hoạt động.  
   - Metric: `decision_log` (`entry_rejected` + `reason_code`), combo audit trong report.

3. **Profit factor**  
   - **PF &gt; 1** trên cửa sổ “after” (toàn portfolio hoặc tập symbol chính).

4. **Expectancy**  
   - **Expectancy ≥ 0 USD** mỗi lệnh (after window).

5. **Giải thích được**  
   - `data/decision_log.jsonl` có thể map phần lớn **entry / reject / structural passthrough** (`STRUCTURAL_LEVELS`, `ENTRY_*`, `COMBO_BLOCKED_EDGE`, `entry_opened`).  
   - Worker / dashboard vẫn đọc được `blocked_signals.json` (có `reason_code`).

## Cách verify

Chi tiết từng bước: **`docs/validation_runbook.md`**.

1. Chọn mốc patch: `--split-ts` = thời điểm deploy patch (UTC).  
2. Chạy:  
   `python scripts/validate_edge_patch_report.py --split-ts "..." --combo-audit --record-experiment`  
3. So sánh khối **Before** vs **After** trong `reports/edge_validation_*.md`.  
4. A/B: lặp lại với `EDGE_EXPERIMENT` + `ENTRY_TIMING_CONFIG` / `PROFIT_ACTIVE_OVERLAY` khác nhau; so sánh `storage/experiments/results_*.jsonl`.

## Không regression

- Worker chạy được; sync Binance không đổi contract; dashboard mở được.  
- Nếu một tiêu chí không đạt: **điều chỉnh config** (entry_timing, combo thresholds, proactive_exit) trước khi thêm tính năng mới.
