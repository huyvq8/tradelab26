# A/B experiment configs

Chạy worker / script với biến môi trường để so sánh phiên bản.

## Biến môi trường

| Biến | Ý nghĩa |
|------|---------|
| `EDGE_EXPERIMENT` | Nhãn ghi vào `decision_log.jsonl` + `storage/experiments/results_*.jsonl` (vd. `entry_v2`, `strict_combo`). |
| `EDGE_SESSION` | Id phiên (optional). |
| `ENTRY_TIMING_CONFIG` | Đường dẫn tới JSON entry timing (relative từ project root hoặc absolute). Ví dụ: `config/entry_timing.v2.json`. |
| `PROFIT_ACTIVE_OVERLAY` | File JSON merge sâu vào `profit.active.json`. Ví dụ: `config/experiments/profit_overlay.strict_combo.json`. |

## Ví dụ PowerShell (Windows)

```powershell
cd trading-lab-pro-v3
$env:EDGE_EXPERIMENT = "strict_combo"
$env:PROFIT_ACTIVE_OVERLAY = "config/experiments/profit_overlay.strict_combo.json"
python apps/worker/runner.py
```

```powershell
$env:EDGE_EXPERIMENT = "entry_v2"
$env:ENTRY_TIMING_CONFIG = "config/entry_timing.v2.json"
python apps/worker/runner.py
```

Kết hợp proactive overlay:

```powershell
$env:PROFIT_ACTIVE_OVERLAY = "config/experiments/profit_overlay.proactive_v2.json"
```

## Ghi snapshot metrics sau khi chạy paper

```powershell
python scripts/validate_edge_patch_report.py --split-ts "2026-03-19T00:00:00" --combo-audit --record-experiment
```

File output: `reports/edge_validation_*.md`, `storage/experiments/results_YYYY-MM-DD.jsonl`.

## So sánh hai báo cáo JSON

```powershell
python scripts/compare_edge_reports.py reports/run_A.json reports/run_B.json
```

Runbook chi tiết: `docs/validation_runbook.md`.
