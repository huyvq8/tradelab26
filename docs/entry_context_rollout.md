# Entry context gates — đo lường, rollout theo giai đoạn, rollback

**Mục tiêu:** chứng minh gating post-pump (Phase 1 + Phase 2 một phần) **giảm long xấu** mà **không** làm tắc các pullback continuation tốt.

**Phase 4 (HTF supply / regime phase / distribution)** — chưa triển khai.

---

## 1) Đo hiệu quả gate theo `CONTEXT_GATE_*`

**Nguồn:** `data/decision_log.jsonl`, event `entry_rejected`, `reason_code` bắt đầu `CONTEXT_GATE`.

**Báo cáo:** chạy `scripts/validate_edge_patch_report.py` — khối JSON `entry_context_gates` gồm:

- `context_gate_reject_count`, `context_gate_share_of_entry_rejects`, `context_gate_share_of_funnel`
- `by_reason_code` (từng `CONTEXT_GATE_*`)
- `by_symbol`, `by_strategy_name`
- `filter_cost_vs_funnel`: liên hệ với `entry_opened` + toàn bộ `entry_rejected`

**Cách đọc:**

- **Effectiveness:** tỷ lệ reject theo từng mã (vd. `CONTEXT_GATE_DISTANCE_FROM_HIGH`) có tăng đúng lúc bật Phase 2 không.
- **Over-filter:** `context_gate_share_of_funnel` tăng mạnh nhưng `expectancy` / PF cửa sổ `after` **không** cải thiện hoặc `sl_loss_lt_5min` không giảm → coi là overfilter hoặc sai nhóm symbol.

---

## 2) Filter cost vs edge improvement

**Filter cost (đo lường, không phải $):**

- `context_gate_reject_share_of_funnel` = số lần bị context gate / (mở + reject entry).
- So sánh **hai kỳ** cùng portfolio: **gates OFF** (overlay tắt Phase 2 / hoặc file config cũ) vs **gates ON** (Stage 1).

**Edge improvement (DB lệnh đóng):**

- `expectancy_usd`, `profit_factor`, `win_rate` (+ Wilson), `sl_loss_lt_5min_count` / `% of losses`.
- Dùng `--split-ts` trùng mốc bật gates để khối `after` phản ánh đúng policy.

**Kết luận chấp nhận (gợi ý, chỉnh theo risk appetite):**

- `sl_loss_lt_5min_pct_of_losses` giảm **hoặc** `expectancy_usd` tăng **đồng thời** `context_gate_share_of_funnel` dưới ngưỡng vận hành (vd. &lt; 35–45% tùy symbol mix).
- Nếu chỉ thấy reject tăng mà metrics không đổi → giảm `min_distance_from_high_pct` hoặc `min_bars_since_local_high`, hoặc rollback Stage 1.

---

## 3) Rollout theo giai đoạn + KPI + rollback

### Giai đoạn 1 (áp dụng case kiểu SIREN — đã cấu hình mặc định trong `entry_context_gates.v1.json`)

| Thành phần | Trạng thái |
|------------|------------|
| Phase 1 native | **Bật đủ** (`extension_score_max`, `setup_quality_min`, `entry_style`) |
| Phase 2 | **Bật** chỉ `min_distance_from_high_pct` + `min_bars_since_local_high` |
| Phase 2 | **Tắt** `reject_on_failed_breakout`, `reject_if_recent_rejection_from_high` |
| Phase 3 | **Tắt** |

**KPI (paper, ≥ 1–2 tuần hoặc đủ sample trong runbook):**

| KPI | Hướng tốt |
|-----|-----------|
| `CONTEXT_GATE_DISTANCE_FROM_HIGH` + `CONTEXT_GATE_BARS_SINCE_HIGH` count | > 0, phân bổ hợp lý (không 100% một mã) |
| `sl_loss_lt_5min_pct_of_losses` (long, symbol quan tâm) | Giảm vs baseline |
| `expectancy_usd` (after) | ≥ baseline hoặc không xấu hơn đáng kể (xem Wilson) |
| `entry_opened_count` | Không sập gần 0 trên watchlist |

**Rollback (bất kỳ điều kiện nào đủ nặng):**

1. `context_gate_share_of_funnel` > **50%** trong 48h liên tục **và** `entry_opened` → gần 0 cho symbol mục tiêu.  
2. `expectancy_usd` after **giảm** > 20–30% so với baseline cùng thời lượng **và** sample sufficiency OK.  
3. Spike lỗi / worker: tắt nhanh bằng `phase2_recent_context.enabled: false` hoặc merge overlay rỗng Phase 2.

**Cách rollback thực tế:** env `ENTRY_CONTEXT_GATES_CONFIG` trỏ file JSON chỉ tắt Phase 2 hoặc nới ngưỡng; hoặc revert commit config.

---

## 4) Hard reject vs soft size reduction (thiết kế hiện tại vs đề xuất)

**Hiện tại (v1):** mọi `CONTEXT_GATE_*` đều **hard reject** (dừng pipeline, không vào lệnh).

| Gate / nhóm | Khuyến nghị | Lý do ngắn |
|-------------|-------------|------------|
| `CONTEXT_GATE_EXTENSION_SCORE`, `SETUP_QUALITY`, `ENTRY_STYLE` | **Hard** | Sai “hình thái” cơ bản; giảm size không sửa điểm vào xấu. |
| `CONTEXT_GATE_DISTANCE_FROM_HIGH`, `BARS_SINCE_HIGH` | **Hard** (Stage 1) | Rõ ràng, đo được; tránh long sát đỉnh pump. |
| `CONTEXT_GATE_FAILED_BREAKOUT`, `RECENT_REJECTION_HIGH` | **Hard** khi bật | Tín hiệu phân phối / reject đỉnh. |
| Phase 3: volume ratio, reclaim EMA | **Ứng viên soft** (tương lai) | Có thể “vào nhỏ” khi context hơi yếu; **chưa implement** trong v1. |
| `CONTEXT_GATE_PULLBACK_SPEED` | **Hard hoặc soft** tùy data | Flush quá nhanh: thường nên hard; biên giới mơ → có thể soft. |

**Lưu ý:** soft mult cần thay đổi pipeline (tương tự combo soft 0.5) — ghi rõ trong backlog, không bắt buộc trong rollout Stage 1.

---

## 5) Debug pass snapshot có chọn lọc

Trong `entry_context_gates.v1.json`:

```json
"log_pass_snapshot": false,
"pass_snapshot_debug": {
  "enabled": true,
  "symbols": ["SIREN"],
  "strategies": ["trend_following", "breakout_momentum"]
}
```

- `log_pass_snapshot: true` → log **mọi** pass (`entry_context_gates_pass`) — ồn.  
- `pass_snapshot_debug.enabled: true` + danh sách → chỉ log pass khi **symbol ∈ symbols** (list rỗng = wildcard) **và** **strategy ∈ strategies** (list rỗng = wildcard).

Event: `entry_context_gates_pass`, `reason_code`: `CONTEXT_GATES_PASS`, payload = metrics phase1/2/3.

---

## 6) Tài liệu liên quan

- `docs/entry_context_gates.md` — vị trí pipeline & ý nghĩa từng phase.  
- `docs/validation_runbook.md` — chạy report before/after bật gates.
