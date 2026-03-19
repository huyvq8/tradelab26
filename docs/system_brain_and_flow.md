# Bộ não của hệ thống — vận hành thế nào

Tài liệu mô tả **bộ não thực sự** của Trading Lab Pro: thành phần nào ra quyết định, luồng chạy mỗi chu kỳ, và cách các bộ phận kết nối với nhau.

---

## 1. Bộ não là gì?

**Bộ não** của hệ thống là **một chu kỳ lặp (Simulation Cycle)** do **Worker** kích hoạt định kỳ (mặc định mỗi 10 giây). Trong mỗi chu kỳ, thứ tự xử lý cố định:

1. **Bảo vệ vị thế đang có** — kiểm tra SL/TP, đồng bộ với sàn, quyết định đóng / cập nhật TP-SL / giữ.
2. **Tìm cơ hội mới** — lấy giá + regime, chạy chiến lược, kiểm tra risk, mở lệnh nếu được phép.

Toàn bộ logic “nghĩ và hành động” nằm trong **`core/orchestration/cycle.py`** (lớp `SimulationCycle`) và được gọi từ **`apps/worker/runner.py`** (job `run_cycle_job`). Không có AI hay “bộ não” trung tâm nào khác: mọi quyết định đều theo **rule + số liệu thị trường + risk**.

---

## 2. Ai kích hoạt chu kỳ? (Worker)

| Thành phần | Vai trò |
|------------|--------|
| **APScheduler** | Chạy job `run_cycle_job` theo **interval** (ví dụ mỗi 10s, cấu hình bởi `CYCLE_INTERVAL_SECONDS`). |
| **run_cycle_job** | Mở DB session, tạo một `SimulationCycle`, gọi lần lượt: `check_sl_tp_and_close` → `sync_positions_from_binance` (nếu Binance) → `review_positions_and_act` → `cycle.run(...)`, rồi `commit`. Log tín hiệu / mở lệnh / từ chối / skip. Gửi Telegram nếu có tín hiệu. |

**Điểm quan trọng:** Mỗi lần job chạy là **một lần “bộ não” chạy một vòng**: trước tiên xử lý vị thế cũ (đóng / cập nhật SL-TP), sau đó mới quét và mở lệnh mới.

---

## 3. Trong một chu kỳ, bộ não làm gì? (SimulationCycle)

Luồng **cố định** mỗi cycle:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. check_sl_tp_and_close(db, "Paper Portfolio")                         │
│    → Với mỗi vị thế mở: nếu giá hiện tại chạm SL hoặc TP → đóng lệnh    │
│      (Paper: đóng trong DB; Binance: lệnh TP/SL do sàn xử lý, bước này   │
│       chỉ đóng trong DB khi dùng Paper). Tự ghi journal outcome.          │
├─────────────────────────────────────────────────────────────────────────┤
│ 2. sync_positions_from_binance(db, "Paper Portfolio")  [chỉ khi Binance]│
│    → So sánh vị thế mở trong DB với GET /fapi/v2/positionRisk.           │
│    → Vị thế có trên DB nhưng không còn trên sàn → đánh dấu đóng trong DB,│
│      tạo Trade (close), lấy PnL từ /fapi/v1/income (REALIZED_PNL).       │
│      Tự ghi journal outcome.                                             │
├─────────────────────────────────────────────────────────────────────────┤
│ 3. review_positions_and_act(db, "Paper Portfolio")                      │
│    → Với mỗi vị thế mở còn lại:                                         │
│      • Có đóng chủ động? (max_hold_hours, risk_off long, high_momentum   │
│        short) → CLOSE.                                                   │
│      • Có pattern nến + gợi ý cập nhật TP/SL? (sl_tp_update) →           │
│        UPDATE_TP_SL (hủy algo cũ, đặt lại trên Binance / cập nhật DB).   │
│      • Không → HOLD.                                                     │
├─────────────────────────────────────────────────────────────────────────┤
│ 4. cycle.run(db, "Paper Portfolio", symbols)  ← ĐÂY LÀ PHẦN “TÌM LỆNH MỚI” │
│    a) Lấy quotes (giá, change_24h, volume_24h) cho toàn bộ symbols.     │
│    b) Với mỗi (symbol, quote):                                           │
│       • regime = derive_regime(quote.percent_change_24h, quote.volume_24h)│
│       • Với mỗi strategy: signal = strategy.evaluate(symbol, price,     │
│         change_24h, volume_24h, regime)                                  │
│       • Nếu signal is None → tiếp symbol/strategy khác.                  │
│       • Nếu có signal: kiểm tra đã đủ lệnh/symbol chưa, zone entry,      │
│         trùng chiến lược, khoảng cách entry → có thể skip.               │
│       • Nếu không skip: risk.assess(signal, available_cash,               │
│         open_positions_count, daily_realized_pnl) → approved? size_usd? │
│       • Nếu approved: execution.open_position(...) → ghi journal entry   │
│         (create_entry với trade_id). Chỉ mở tối đa 1 lệnh mới mỗi cycle │
│         (break sau khi mở một lệnh).                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

Tóm lại: **bộ não** = một vòng lặp cố định: (1) bảo vệ lệnh cũ, (2) đồng bộ sàn (nếu Binance), (3) review từng vị thế (đóng / update TP-SL / giữ), (4) chạy **run()** để từ **quotes → regime → strategies → risk → execution** và có thể mở **một** lệnh mới.

---

## 4. Các “nơron” quyết định bên trong run()

Khi **run()** quét cơ hội mới, thứ tự và vai trò từng bộ phận:

| Bước | Thành phần | File / Hàm | Vai trò |
|------|------------|------------|--------|
| **Vào** | Watchlist | `core/watchlist.py` | Danh sách symbol cần quét (BTC, SIREN, …). |
| **Dữ liệu** | Market data | `get_quotes_with_fallback(symbols)` | Giá, change_24h, volume_24h cho mỗi symbol. |
| **Bối cảnh** | Regime | `core/regime/detector.py` → `derive_regime(change_24h, volume_24h)` | Một trong: **high_momentum** (tăng mạnh + volume lớn), **risk_off** (giảm mạnh), **balanced**. Chiến lược dựa vào regime để bắn hay không. |
| **Ý tưởng** | Strategies | `core/strategies/implementations.py` → `strategy.evaluate(...)` | Mỗi chiến lược: nếu thỏa điều kiện (regime + change_24h + volume) → trả về **StrategySignal** (symbol, side, entry, SL, TP, rationale); không thì `None`. Không có “AI” ở đây, chỉ rule. |
| **Kiểm soát** | Risk | `core/risk/engine.py` → `RiskEngine.assess(...)` | Giới hạn số lệnh đồng thời, giới hạn lỗ trong ngày, size theo khoảng cách stop. Trả về **approved** và **size_usd** hoặc từ chối. |
| **Hành động** | Execution | `core/execution/simulator.py` hoặc `binance_futures.py` | **open_position**: ghi Position + Trade (open), đặt TP/SL (Paper chỉ DB; Binance gọi API đặt lệnh). **close_position** / **update_position_sl_tp** dùng ở bước 1–3. |
| **Ghi nhớ** | Journal | `core/journal/service.py` | Mỗi lệnh mở: **create_entry** (trade_id = open Trade id). Mỗi lệnh đóng: **record_outcome_from_close** (result_summary, lessons, mistakes) để reflection và “học từ lịch sử” đọc lại. |

**Không có** một “model AI” hay “neural net” trung tâm: toàn bộ là **rule + số liệu (quotes, regime) + risk**. AI (nếu bật) chỉ dùng ở **phản tư / gợi ý** (reflection, tình huống), không điều khiển vào/ra lệnh.

---

## 5. Tóm tắt một câu

- **Bộ não thực sự** = **SimulationCycle** chạy định kỳ bởi Worker: mỗi lần chạy = (1) bảo vệ vị thế (SL/TP, sync Binance, review đóng hoặc update TP-SL), (2) **run()** = quotes → **regime** → **strategies.evaluate** → **risk.assess** → **execution.open_position** (+ journal).  
- **Vận hành:** Rule cố định (regime, điều kiện từng chiến lược, risk), không có “AI ra lệnh”; dữ liệu vào là **giá và volume 24h** từ market client; “ghi nhớ” là DB (Position, Trade, Journal) và reflection đọc lại để tự nhận diện sai lầm (SL quá nhanh, combo thua nhiều).

File cốt lõi: **`core/orchestration/cycle.py`** (SimulationCycle) và **`apps/worker/runner.py`** (run_cycle_job).
