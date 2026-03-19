# ADR: Capital split (Core / Fast) trên một Portfolio

## Bối cảnh

Spec: `document/capital_split_fast_trading_module.md`. Hệ v3 có một bản ghi `Portfolio` + `Position`/`Trade` chung `cash_usd`.

## Quyết định

- **Một portfolio DB**, không tách hai dòng `portfolios` cho Core/Fast (tránh đồng bộ cash phức tạp với một tài khoản sàn).
- Thêm cột **`capital_bucket`** (`core` | `fast`) trên `positions` và `trades` để:
  - giới hạn concurrent / daily loss / consecutive loss **theo bucket**;
  - sizing risk **theo slice ảo** (ví dụ 70% / 30% của `risk_capital_usd`);
  - báo cáo PnL theo bucket.

## Hệ quả

- Tiền thật vẫn một pool (`cash_usd`); bucket là **kế toán & risk**, không ngăn hard ở DB giữa hai ví.
- Cần migration SQLite (`ALTER TABLE`) qua `core.db.ensure_*`.
- Bật/tắt bằng `config/capital_split.v1.json` → `enabled: false` mặc định giữ hành vi cũ.

## Thay thế đã xem xét

- Hai portfolio DB: đúng nghĩa “sub-portfolio” nhưng khó map một balance Binance → hai dòng equity.
