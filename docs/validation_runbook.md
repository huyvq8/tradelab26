# Runbook: chứng minh patch cải thiện edge (ưu tiên 1)

Không thêm tính năng mới cho đến khi có **số liệu before/after** rõ ràng.

## Bước 1 — Ghi nhận mốc thời gian

Ghi lại **UTC** lúc deploy patch (hoặc bật config mới): ví dụ `2026-03-19T08:00:00`.

## Bước 2 — Chạy paper / worker đủ lâu

- Cùng portfolio (vd. `Paper Portfolio`).
- Khuyến nghị: **≥ 80–100 lệnh đóng** trong cửa sổ “after”, hoặc **≥ 2–4 tuần** cùng cấu hình.

## Bước 3 — Xuất báo cáo

```powershell
cd trading-lab-pro-v3
python scripts/validate_edge_patch_report.py --portfolio "Paper Portfolio" --split-ts "2026-03-19T08:00:00" --combo-audit --record-experiment
```

- File: `reports/edge_validation_*.md` + `.json`
- Mục **Measurable improvement**: chênh lệch `after − before` cho PF, expectancy, SL <5 phút, TP distance, …
- **Sample sufficiency**: khối `sample_sufficiency` trong JSON + section trong MD; điều chỉnh ngưỡng bằng `--min-closed-after`, `--min-decision-lines`, `--min-entry-funnel`. `--strict-sample` → exit code 3 nếu chưa đủ mẫu.
- **Decision log**: `reason_breakdown_by_event`, `entry_funnel_filter_cost` (tỷ lệ reject trên funnel + phân bổ theo `reason_code`), `native_signal_payload_coverage`, **`entry_context_gates`** (đếm `CONTEXT_GATE_*`, share funnel — so sánh gates ON vs OFF).
- **Win rate**: thêm `win_rate_wilson_95_low/high` trên mỗi cửa sổ trade DB (khoảng tin cậy Wilson).

## Bước 4 — So sánh hai cấu hình / hai thời điểm

Sau khi có hai file JSON (hai experiment hoặc hai split khác nhau):

```powershell
python scripts/compare_edge_reports.py reports/edge_validation_OLD.json reports/edge_validation_NEW.json
```

Bảng so sánh cột **`after`** của từng file (đọc kỹ chú thích trong script nếu split-ts khác nhau).

## Bước 5 — Đối chiếu acceptance

Xem `docs/negative_edge_acceptance.md`.

## Ghi chú

- Lệnh cũ không có `entry_regime` trên `Position` → combo theo regime dùng `unknown` cho đến khi có lệnh mới sau migrate.
- `decision_log.jsonl` + `improvement_delta` là bằng chứng vận hành chính.
