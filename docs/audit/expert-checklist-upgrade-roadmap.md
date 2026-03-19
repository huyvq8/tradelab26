# Lộ trình nâng cấp v3 → tiêu chuẩn `expert-trading-decision-checklist.md`

Tài liệu này trả lời: **phương án tốt nhất** để tiến gần checklist **mà không phá** kiến trúc hiện có (`SimulationCycle`, `StrategySignal`, journal, execution).

---

## 1. Nguyên tắc chiến lược (vì sao đây là “tốt nhất”)

1. **Không rewrite cycle trước khi có “Decision Envelope”** — Checklist §4.1–4.3 và §11 yêu cầu *một* bản ghi quyết định có: invalidation, plan, no_trade_reason. Hiện logic rải nhiều nơi; gom **xuất** (serialize) trước, tách **refactor nội bộ** sau → giảm rủi ro.
2. **Nâng perception theo lát cắt dọc (vertical slice)** — Mỗi nguồn dữ liệu mới phải đi hết: ingest → `CycleMarketSnapshot` (hoặc tương đương) → ít nhất một gate hoặc một field trong Decision record → log/journal. Tránh chỉ “fetch funding rồi không ai dùng”.
3. **Học vòng kín chỉ sau khi có schema** — Checklist §9: thiếu bước “cập nhật rule/threshold → dùng cycle sau” thì mãi Level 0–1. Cách an toàn: **đề xuất thay đổi config + human approve + version** (không tự sửa file production trong cycle).
4. **Ưu tiên BTC / beta tối thiểu trước khi liquidation/OI nâng cao** — ROI cao, API ít, đáp ứng §3.1 và §6.3 một phần lớn.

---

## 2. Trục Bắc (North Star) — ba artifact hệ thống phải có

| Artifact | Mục đích (theo checklist) | Gợi ý triển khai trong v3 |
|----------|---------------------------|---------------------------|
| **A. `MarketContextSnapshot`** | §3 nhận thức thị trường | Mở rộng `core/orchestration/cycle_market.py`: BTC 1h/4h regime nhẹ, funding/OI (Futures), rel volume, optional sector tag |
| **B. `TradeDecision` (envelope)** | §4 decision + §5 action plan + §11 narrative | `core/decision/` dataclass: map từ `StrategySignal` + kết quả gates; luôn ghi JSON (DB hoặc file) mỗi lần evaluate entry / mỗi review vị thế |
| **C. `LearningArtifact`** | §7–9 | Sau đóng lệnh: `loss_category` (enum), `expectation_vs_actual`, link tới `TradeDecision` id; proposal queue cho config |

Cho đến khi B tồn tại, mọi feature “thông minh” mới dễ bị **PARTIAL** vĩnh viễn vì không audit được end-to-end.

---

## 3. Lộ trình theo giai đoạn (thứ tự nên làm)

### Giai đoạn 0 — P0: Nền móng quyết định & quan sát (1–2 tuần dev tùy bandwidth)

**Mục tiêu checklist:** §4.1–4.3 (một phần), §11, §12 phần trace.

| Việc | Chi tiết kỹ thuật |
|------|-------------------|
| **0.1 `TradeDecision` + serialization** | Thêm module `core/decision/envelope.py`: `symbol`, `regime_token`, `regime_market`, `setup_type`, `direction`, `confidence`, `invalidation_price_or_rule`, `entry_plan`, `stop_plan`, `tp_plan`, `manage_plan`, `no_trade_reason`, `passed_gates[]`, `rejected_reason_code`. Build từ `StrategySignal` sau pipeline; khi reject chỉ cần `no_trade_reason` + gates. |
| **0.2 Persist mỗi cycle (tối thiểu)** | Bảng `decision_events` (SQLite/Postgres) hoặc append JSONL trong `logs/decisions/` — một dòng / candidate hoặc / entry attempt. Liên kết `trade_id` / `position_id` khi mở lệnh. |
| **0.3 Dashboard / API “narrative”** | Tab hoặc endpoint: đọc `TradeDecision` gần nhất theo symbol — đáp §11. |
| **0.4 Sửa nợ kỹ thuật** | Đồng bộ test `test_klines_cache_key_and_ttl` với `KLINES_CACHE_TTL` thực tế (đã lệch trong audit). |

**Không làm ở G0:** thêm 5 indicator mới nếu chưa có chỗ ghi vào envelope.

---

### Giai đoạn 1 — Perception tối thiểu “expert-like” (2–4 tuần)

**Mục tiêu checklist:** §3.1, §3.2 (nâng từ PARTIAL), §6.3 một phần.

| Việc | Chi tiết |
|------|----------|
| **1.1 BTC / ETH context** | Trong mỗi cycle: fetch klines + quote cho BTC (và ETH nếu cần); tính `market_regime_simple` (vd. 4h slope + 1h range%) **tách** khỏi `derive_regime` của từng alt. Đưa vào `MarketContextSnapshot`. |
| **1.2 Funding + open interest (Binance Futures)** | Module `core/market_data/binance_futures_context.py`: `premiumIndex` / `fundingRate`, OI từ endpoint phù hợp; cache 60–300s. Chỉ bật khi `BinanceFuturesExecutor` hoặc flag `use_futures_metrics`. |
| **1.3 Relative volume** | `volume_1h / median(volume_1h last N)` từ klines đã có — không cần API mới. |
| **1.4 Gate sử dụng context** | Ví dụ: long alt khi `market_regime_simple == risk_off` và BTC dump mạnh → `NEED_CONFIRMATION` hoặc size multiplier (config). Viết vào `passed_gates` / `no_trade_reason`. |

**Tùy chọn sau 1.4:** liquidation aggregate — chỉ khi đã có chỗ trong snapshot và gate; tránh làm sớm.

---

### Giai đoạn 2 — Regime & token selection có cấu trúc (2–3 tuần)

**Mục tiêu checklist:** §3.2, §3.3, §4.2.

| Việc | Chi tiết |
|------|----------|
| **2.1 Hai lớp regime** | `regime_market` (BTC-led) vs `regime_token` (alt) — thay vì một `derive_regime` cho mọi thứ. Refactor dần: strategy đọc đúng layer. |
| **2.2 Candidate scoring trong cycle** | Sau khi mỗi symbol có signals: gán `clarity_score` + `edge_score` (rule-based từ `setup_quality`, extension, RR, context gates). **Chỉ đánh** best candidate per symbol hoặc best globally nếu `max_concurrent` đầy — đáp “token nào rõ hơn” (§2A, §3.3). |
| **2.3 WAIT / NEED_CONFIRMATION** | Trạng thái rõ ràng trong envelope khi signal yếu: không mở lệnh nhưng lưu lý do; cycle sau có thể tái đánh giá (không ép MARKET ngay). |

---

### Giai đoạn 3 — In-trade: noise vs invalidation (3–4 tuần)

**Mục tiêu checklist:** §6.1, §6.3, §14 câu 3–5.

| Việc | Chi tiết |
|------|----------|
| **3.1 Nhãn quản trị** | Mỗi lần `review_positions_and_act`: ghi `management_state`: `HOLD_THESIS_OK`, `HOLD_NOISE`, `REDUCE_RISK`, `EXIT_THESIS_BROKEN`, `EXIT_TIME_STOP`, … (map vào action hiện có: proactive, exit_guards, partial). |
| **3.2 Heuristic “thesis broken”** | Kết hợp: chạm invalidation (từ envelope lúc vào), MFE/MAE pattern, regime flip token+market, fast no-follow-through đã có. |
| **3.3 Giảm size có chủ đích** | Nếu chưa có nhánh REDUCE rõ: thêm `reduce_position` với % cố định khi `REDUCE_RISK` (trước khi full exit). |

---

### Giai đoạn 4 — Loss / Win intelligence & analytics (2–3 tuần)

**Mục tiêu checklist:** §7, §8.

| Việc | Chi tiết |
|------|----------|
| **4.1 `loss_category` enum** | Cột hoặc JSON trong journal: `wrong_direction`, `bad_timing`, `wrong_regime`, `bad_token`, `oversized`, `bad_management`, `stop_too_tight`, `held_too_long`, `data_gap`, `abnormal_market`. Gán rule-based sau đóng + cho phép override tay trên dashboard. |
| **4.2 Win analytics** | Báo cáo theo `(strategy, regime_token, regime_market, setup_type, hour_bucket)` — dùng SQL/group-by trên `trades` + `journal_entries` + `decision_events`. |

---

### Giai đoạn 5 — Closed-loop có guardrail (4+ tuần, đúng §9 Level 4)

**Mục tiêu checklist:** §9.1–9.2, §7.2.

| Việc | Chi tiết |
|------|----------|
| **5.1 Proposal queue** | Bảng `config_proposals`: diff JSON (vd. giảm weight strategy X khi `loss_category` lặp), trạng thái `pending/approved/rejected`, user id hoặc file sign-off. |
| **5.2 Applier an toàn** | CLI hoặc admin action: merge vào `config/experiments/` hoặc profit overlay — **không** ghi đè `*.json` production trong worker. |
| **5.3 Kết nối với code hiện có** | Mở rộng `strategy_weight_engine` / combo: thêm nguồn từ analytics §4.2; giữ min/max clamp. |

---

### Giai đoạn 6 — Portfolio intelligence (song song hoặc sau G1)

**Mục tiêu checklist:** §10.

- Tổng hợp exposure long/short notional, theo `capital_bucket`, theo sector (từ `correlation_sectors` + mapping symbol).
- Kill switch phụ thuộc `regime_market` (config: vd. tắt mở mới khi BTC `risk_off` mạnh) — bổ sung, không thay kill switch R hiện tại.

---

## 4. Thứ tự ưu tiên tóm tắt (nếu chỉ nhớ một hàng)

1. **Decision envelope + persist + narrative** (G0)  
2. **BTC context + funding/OI + rel vol + gate** (G1)  
3. **Regime tách lớp + candidate clarity** (G2)  
4. **Management labels + thesis/noise** (G3)  
5. **Loss taxonomy + analytics** (G4)  
6. **Proposal queue closed-loop** (G5)  
7. **Portfolio aggregates** (G6)

---

## 5. Việc *không* nên ưu tiên sớm

- **Thêm nhiều strategy mới** trước G0–G2 → tăng nhiễu, khó chứng minh đạt checklist.  
- **LLM “quyết định vào lệnh”** thay rule — vi phạm tinh thần audit “có luận điểm có cấu trúc”; chỉ nên AI cho **tổng hợp narrative / reflection** trên dữ liệu đã có trong envelope.  
- **Microstructure data đắt** (full liquidation tape) trước khi funding + BTC + envelope đã dùng trong gate.

---

## 6. Tiêu chí “đạt checklist” (định nghĩa operational)

Hệ coi là **đạt mức expert-grade theo file check** khi đồng thời:

- Mọi lệnh mở có **`TradeDecision`** đầy đủ plan + invalidation đã persist.  
- Mọi lệnh đóng có **`loss_category` hoặc win tag** + link decision id.  
- **BTC/market context** và **funding/OI** (Futures) tham gia ít nhất một gate có log.  
- **Review vị thế** ghi `management_state` không chỉ TP/SL.  
- Có **ít nhất một** cơ chế closed-loop: proposal → approve → áp dụng vào config được version hóa.

Cho đến khi các mục trên xong, kết luận audit vẫn nên là **Chưa** (theo §13.2 checklist).

---

## 7. Liên kết tài liệu trong repo

- Hiện trạng tổng quát: [`trading-lab-current-state-check.md`](./trading-lab-current-state-check.md)  
- Checklist gốc (ngoài repo app): `bug/expert-trading-decision-checklist.md`

---

*Cập nhật: có thể tick từng giai đoạn trong PR; không cần hoàn thành hết mới ship — nhưng thứ tự trên tối ưu chi phí / rủi ro.*
