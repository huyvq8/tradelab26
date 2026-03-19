# Cải tiến theo `bug/trading_system_bugs_and_fixes.md`

## Đã triển khai

| Mục | Mô tả | File / config |
|-----|--------|----------------|
| Fast exit (no follow-through) | Đóng vị thế `capital_bucket=fast` khi đủ phút, đang lỗ, MFE % thấp (không đi thuận). | `core/orchestration/exit_guards.py`, `review_positions_and_act` trong `cycle.py`; `fast_no_follow_through_*` trong `capital_split.v1.json` |
| Fast strategy vs swing | `fast_strategy_denylist` — strategy trong list không bao giờ gán fast (về core). | `core/portfolio/capital_split.py` |
| Over-extended sớm | `trend_following` / `breakout_momentum`: `extension_score > 0.88` → không sinh signal (có klines). | `core/strategies/implementations.py` |
| Pre-check size | Ước lượng size trước `_risk_assess_entry`; bỏ qua sớm nếu chắc chắn < min. | `core/risk/quick_sizing.py`, `cycle.py` |
| Correlation guard fast | `correlation_guard_max_same_sector_fast` > 0: giới hạn số vị thế fast cùng sector (map base → sector). | `core/portfolio/correlation_guard.py`, `correlation_sectors.v1.json` (optional) |
| Regime → strategy | Tắt strategy theo regime + thứ tự evaluate. | `core/orchestration/regime_strategy_filter.py`, `regime_strategy.v1.json` (bật `enabled: true` khi dùng) |
| Log noise | `SIGNAL_CANDIDATE` → `DEBUG`. | `cycle.py` |

## Cách bật

1. **Capital split** (đã có): `fast_no_follow_through_enabled: true` trong `config/capital_split.v1.json`.
2. **Regime filter**: copy `config/regime_strategy.v1.example.json` → `regime_strategy.v1.json`, đặt `"enabled": true`.
3. **Correlation**: đặt `correlation_guard_max_same_sector_fast` (vd. `2`); tùy chọn thêm `config/correlation_sectors.v1.json` với `base_to_sector`.

## Chưa làm (Phase 8 / dashboard)

- Panel metrics riêng từng strategy trên dashboard (dữ liệu đã có trong `Trade` + `compute_strategy_weights`).
- Hiển thị “pre-risk filtered” đồng bộ worker — có thể dùng `decision_log` / `rejected_signals`.
