# Brain V4 — P1 Adaptive Operating Layer (Spec triển khai)

**Repo:** `trading-lab-pro-v3`  
**Tiền đề:** P0 đã có theo `docs/audit/autonomous-regime-aware-trading-brain-v4.md` + `core/brain/*`.  
**Phạm vi:** Chỉ P1 — không lặp P0, không thêm strategy, không LLM quyết định vào lệnh, không viết lại `RiskEngine` — chỉ **modifier + persist + UI + replay**.

---

## 1. Executive summary

P1 biến V4 từ **lớp bảo vệ + chặn cycle** thành **lớp vận hành**: mọi chuỗi *infer → change-point → policy → reflex* được **ghi DB có trace**, `PolicyMode` **nhân vào sizing / siết nới entry / scale-in / proactive exit** tại đúng chỗ trong `SimulationCycle`, **dashboard + API** đọc được reasoning, **replay** tái tính deterministic để audit và regression.

---

## 2. Gap from current P0

| Thiếu | Hiện trạng P0 | P1 phải có |
|-------|----------------|------------|
| Reasoning persist | `decision_log` JSONL rời; `storage/brain_v4_runtime.json` không đủ audit DB | 4 bảng event + `cycle_id` thống nhất |
| Policy → sizing/entry | `should_block_cycle_symbol` + reflex | Modifier sau `RiskDecision`, trước execution; overlay proactive/scale-in |
| Operator visibility | Không màn hình V4 | Streamlit + REST |
| Kiểm chứng | Không replay đa lớp | CLI/API replay + diff |

---

## 3. P1 architecture

```text
cycle_id = uuid (mỗi SimulationCycle.run hoặc mỗi worker tick có run)
    ↓
build_brain_v4_cycle_context → persist policy_mode_events (market-wide)
    ↓
per symbol: infer states → INSERT state_inference_events
            change_point      → INSERT change_point_events
    ↓
entry path: apply_policy_entry_overlay → risk unchanged internally → apply_policy_size_modifier on USD size chain
    ↓
reflex / review: reflex_action_events; proactive_exit with merged pe_cfg overlay
    ↓
trade close: journal/trade links cycle_id + event_ids (optional FK hoặc JSON pointer)
```

### 3.1 `cycle_id` vs `decision_trace_id`

| Khái niệm | Phạm vi | Dùng khi |
|-----------|---------|----------|
| **`brain_cycle_id`** | Một vòng worker tick (`review` + `run` chung) | Gom mọi event trong cùng lần quét portfolio + symbols |
| **`market_decision_trace_id`** | Một UUID cho **policy market-wide** trong tick đó | Một dòng `policy_mode_events`; liên kết ngược từ symbol events qua cột `market_decision_trace_id` |
| **`decision_trace_id` (symbol / entry)** | Một chuỗi quyết định **theo symbol** trong cycle | Cùng id cho `state_inference` + `change_point` + `brain_sizing_events` + `trades.decision_trace_id` khi mở lệnh từ nhánh đó |
| **`decision_trace_id` (reflex)** | Một UUID **mỗi lần** reflex thực thi trong review | Tách nhánh review khỏi nhánh entry; vẫn có `market_decision_trace_id` để khớp tick |

API: `GET /brain/v4/trace/{decision_trace_id}` trả về mọi hàng có cùng `decision_trace_id`.

### 3.2 Market-wide policy vs symbol policy (rule khóa)

- **Mặc định:** `PolicyMode` **market-wide** điều khiển sizing, entry overlay, scale-in gate, proactive merge. Code P1 **không** ghi `policy_mode_events` scope=`symbol` trừ khi sau này bật override có điều kiện.
- **Symbol policy chỉ được khác market** khi thỏa ít nhất một điều kiện:
  1. **Context break riêng symbol** (`change_point.context_break_flag` hoặc điều kiện tương đương đã thống nhất trong code), hoặc
  2. **Thesis / position state lệch rõ** so với giả định market (ví dụ không còn `THESIS_HEALTHY` / `PROFIT_PROTECTED` khi đang quản trị vị thế).
- **Nếu không thỏa:** effective policy cho symbol = market policy (`core/brain/symbol_policy.py`: `effective_policy_mode_for_symbol`, `symbol_policy_override_allowed`).

Mục tiêu: tránh trạng thái mơ hồ kiểu market `NORMAL` + symbol `EXIT_ONLY` làm sizing và dashboard khó đọc.

### 3.3 Sizing audit (bốn mức + breakdown)

Mỗi lần vào lệnh (khi persist bật), ghi `brain_sizing_events`:

| Cột | Ý nghĩa |
|-----|---------|
| `post_risk_engine_usd` | Size sau `RiskEngine` approve (`decision.size_usd`) |
| `pre_modifier_usd` | Sau volatility guard + dynamic sizing + combo mult, **trước** brain policy modifier |
| `post_modifier_usd` | Sau `apply_policy_size_breakdown` (mult + stress + notional cap) |
| `final_executable_usd` | `min(post_modifier_usd, available_cash)` — size thực thi |
| `modifier_breakdown_json` | Chi tiết: `post_size_mult_usd`, `post_stress_usd`, `post_notional_cap_usd`, … |

---

**Module mới (đề xuất tên file cố định):**

| File | Trách nhiệm |
|------|-------------|
| `core/brain/models.py` | SQLAlchemy models 4 bảng + `BrainCycle` (optional header row) |
| `core/brain/persistence.py` | `insert_*_event`, `link_trace`, `sha256_config`, batch flush |
| `core/brain/policy_apply.py` | `apply_policy_size_modifier`, `apply_policy_entry_overlay`, `merge_proactive_exit_overlay`, `scale_in_policy_gate` |
| `core/brain/decision_trace.py` | `BrainTraceContext` dataclass: `cycle_id`, carry `event_id` stack |
| `core/brain/replay.py` | `load_snapshot`, `recompute_v4_layer`, `diff_report` |
| `core/brain/migrations_brain_v4_p1.sql` | DDL thuần (SQLite + Postgres variant nếu cần) |
| `scripts/replay_brain_v4.py` | CLI: `--cycle-id` / `--trade-id` |

**Sửa file hiện có:**

- `core/db.py` — `ensure_*` cho SQLite ALTER nếu không dùng Alembic
- `core/orchestration/cycle.py` — tạo `cycle_id`, gọi persist, gọi `policy_apply`, truyền `BrainTraceContext`
- `core/brain/context.py` — nhận `cycle_id`, trả context kèm hash config
- `core/brain/integration.py` — ghi `reflex_action_events`, nhận `position_id`, `trade_id` sau execute
- `apps/api/server.py` — endpoints reasoning
- `apps/dashboard/app.py` — page/section “Brain V4”

---

## 4. DB schema + migration

### 4.1 Bảng `brain_cycles` (khuyến nghị — 1 dòng đầu mỗi `run`)

| Column | Type | Ghi chú |
|--------|------|---------|
| id | UUID hoặc TEXT PK | = `cycle_id` |
| started_at | DateTime UTC | |
| portfolio_id | INT FK nullable | |
| config_hash_v4 | VARCHAR(64) | SHA256 file `brain_v4.v1.json` merged |
| trace_version | VARCHAR(8) | `"p1"` |
| market_decision_trace_id | VARCHAR(36) nullable | UUID policy market tick |

**Index:** `(started_at DESC)`, `(portfolio_id, started_at)`.

### 4.2 `state_inference_events`

| Column | Type | Typed vs JSON |
|--------|------|----------------|
| id | BIGSERIAL / INTEGER PK | |
| cycle_id | TEXT NOT NULL | indexed |
| decision_trace_id | VARCHAR(36) nullable | indexed — chuỗi quyết định symbol |
| market_decision_trace_id | VARCHAR(36) nullable | indexed — liên kết policy tick |
| symbol | VARCHAR(20) NOT NULL | indexed |
| ts_utc | DateTime NOT NULL | |
| inferred_market_state | VARCHAR(32) | typed |
| inferred_token_state | VARCHAR(32) | typed |
| inferred_position_state | VARCHAR(32) nullable | typed |
| conf_market | REAL | |
| conf_token | REAL | |
| conf_position | REAL nullable | |
| feature_snapshot_json | TEXT/JSON | **JSON**: chỉ snapshot tối thiểu (btc_regime, change_24h, rel_vol proxy, trend_strength proxy — keys cố định trong spec code) |
| reason_codes_json | TEXT/JSON | array string |
| config_hash_v4 | VARCHAR(64) | |

**Index:** `(cycle_id, symbol)`, `(symbol, ts_utc DESC)`.

### 4.3 `change_point_events`

| Column | Type |
|--------|------|
| id | PK |
| cycle_id | TEXT NOT NULL |
| decision_trace_id | VARCHAR(36) nullable |
| market_decision_trace_id | VARCHAR(36) nullable |
| symbol | VARCHAR(20) NOT NULL |
| ts_utc | DateTime |
| structure_score | REAL |
| participation_score | REAL |
| btc_leader_score | REAL |
| crowding_score | REAL |
| shock_score | REAL |
| change_point_score | REAL |
| context_break_flag | BOOLEAN |
| shift_type | VARCHAR(40) |
| urgency_level | VARCHAR(16) |
| recommended_action | VARCHAR(32) |
| reason_codes_json | TEXT/JSON |
| config_hash_v4 | VARCHAR(64) |

**Index:** `(cycle_id, symbol)`.

### 4.4 `policy_mode_events`

| Column | Type |
|--------|------|
| id | PK |
| cycle_id | TEXT NOT NULL |
| decision_trace_id | VARCHAR(36) nullable | = `market_decision_trace_id` khi scope=market |
| scope | VARCHAR(16) | `"market"` \| `"symbol"` |
| symbol | VARCHAR(20) nullable | null = market-wide |
| ts_utc | DateTime |
| previous_mode | VARCHAR(24) |
| new_mode | VARCHAR(24) |
| policy_confidence | REAL |
| switch_reason_codes_json | TEXT/JSON |
| cooldown_blocked | BOOLEAN |
| ttl_sec | INT |
| re_evaluate_after_sec | INT |
| emergency_override | BOOLEAN |
| config_hash_v4 | VARCHAR(64) |

**Index:** `(cycle_id)`, `(ts_utc DESC)`.

### 4.5 `reflex_action_events`

| Column | Type |
|--------|------|
| id | PK |
| cycle_id | TEXT NOT NULL |
| decision_trace_id | VARCHAR(36) nullable | mỗi lần reflex |
| market_decision_trace_id | VARCHAR(36) nullable |
| symbol | VARCHAR(20) |
| position_id | INT FK nullable |
| ts_utc | DateTime |
| urgency_level | VARCHAR(16) |
| reflex_action | VARCHAR(32) |
| preconditions_json | TEXT/JSON |
| action_reason | TEXT |
| result | VARCHAR(24) | `executed` \| `skipped` \| `failed` |
| linked_trade_ids_json | TEXT/JSON | array int |
| change_point_event_id | BIGINT nullable FK | optional link |

**Index:** `(position_id, ts_utc DESC)`, `(cycle_id)`.

### 4.6 `brain_sizing_events`

| Column | Type |
|--------|------|
| id | PK |
| cycle_id | TEXT NOT NULL |
| decision_trace_id | VARCHAR(36) nullable |
| market_decision_trace_id | VARCHAR(36) nullable |
| symbol | VARCHAR(20) |
| strategy_name | VARCHAR(50) |
| side | VARCHAR(10) |
| post_risk_engine_usd | REAL |
| pre_modifier_usd | REAL |
| post_modifier_usd | REAL |
| final_executable_usd | REAL |
| available_cash_usd | REAL nullable |
| modifier_breakdown_json | TEXT |

### 4.7 Traceability fields (bắt buộc thiết kế)

- `cycle_id` (string) sinh ở **đầu** worker tick, truyền xuống mọi `insert_*`.
- `decision_trace_id`: chuỗi quyết định (symbol entry / reflex / market policy).
- `Trades`: nullable `brain_cycle_id`, `decision_trace_id`.
- `JournalEntry`: (P1 tùy chọn) — ưu tiên **cột trên `trades`** ít đụng journal nhất cho P1.

**Retention:** config `brain_v4_p1.retention_days` (default 90); job `scripts/prune_brain_events.py` xóa theo `ts_utc` (P1 có thể chỉ document, implement P1.1).

**Migration plan:**

1. Thêm models vào `core/brain/models.py`.
2. `Base.metadata.create_all` trong worker/dashboard (đã có pattern) **hoặc** `ensure_brain_v4_p1_tables()` trong `core/db.py` giống các `ensure_*` hiện tại cho SQLite.
3. Postgres: cùng DDL; user set `DATABASE_URL` thì dùng chung engine.

---

## 5. Integration points in current repo

### 5.1 `SimulationCycle.run` (`core/orchestration/cycle.py`)

**Thêm ngay sau khi có `portfolio` + `quotes` + `daily_realized` (trước vòng `for symbol, quote`):**

1. `cycle_id = str(uuid.uuid4())`
2. `INSERT brain_cycles` (portfolio_id)
3. Gọi `build_brain_v4_cycle_context(..., cycle_id=cycle_id)` — **sửa signature** `context.py`
4. `persist_policy_mode_event(...)` từ `BrainV4CycleContext.policy`

**Trong vòng symbol (sau có `klines_full`, `regime`):**

5. Gọi infer token/market (đã có logic trong P0 — tách hàm thuần trả tuple + conf)
6. `INSERT state_inference_events` (symbol-level; market state lặp lại hoặc chỉ 1 row `scope=market` — chọn **1 row market đầu cycle** + mỗi symbol 1 row token)

**Sau khi có change-point cho symbol (tính trong P0 hoặc tái gọi):**

7. `INSERT change_point_events`

**Entry strictness (§2.1) — chèn sau signal hợp lệ, trước `_risk_assess_entry`:**

- Hàm `apply_policy_entry_overlay(signal, brain_v4_ctx, regime, quote) -> Optional[reject_dict]`
- Logic deterministic:
  - `entry_strictness > 1` → tăng ngưỡng tương đương: ví dụ yêu cầu `signal.confidence >= base_min + (strictness-1)*0.08` (hằng số trong `policy_apply.py` + override từ `brain_v4.v1.json` section `p1_entry`)
  - `no_trade_sensitivity > 1` → nếu `regime_clarity` proxy (từ feature snapshot) dưới ngưỡng động → reject với `reason_code=BRAIN_V4_ENTRY_SENSITIVITY`
- **Không** chạy trước `strategy.evaluate` (tránh đụng strategy)

**Sizing (§2.2) — chèn sau chuỗi hiện có ~dòng 773–812:**

Pseudo:

```python
decision = self._risk_assess_entry(...)
size_after_vol = decision.size_usd  # sau vol guard hiện tại
# ...
final_size_usd = apply_dynamic_sizing(...)  # hoặc bypass
final_size_usd = round(float(final_size_usd) * entry_combo_mult, 2)
final_size_usd = apply_policy_size_modifier(
    final_size_usd,
    policy=brain_v4_ctx.policy,
    market_state=brain_v4_ctx.market_state,
    portfolio_stress=brain_v4_ctx.portfolio_stress_score,
    symbol=symbol,
)
```

`apply_policy_size_modifier`:

- `out = base * policy.modifiers.size_multiplier`
- `out *= max(0.25, 1.0 - 0.5 * portfolio_stress)` chỉ khi `CAPITAL_PRESERVATION` hoặc `DEFENSIVE` (bật flag trong config)
- `out = min(out, cap_symbol_usd)` nếu có trong `p1_sizing.max_notional_per_symbol_usd` (optional)
- Không được vượt `available_cash`; vẫn kiểm tra `>= 25` sau modifier

**Scale-in (§2.3) — ngay trước `ScaleInEngine(...).evaluate` (~596, ~982):**

```python
if not scale_in_policy_gate(brain_v4_ctx, symbol, change_point_score=cp_sym, position=position):
    _log_scale_in_rejected(..., reason="brain_v4_policy_gate")
    continue
```

Gate rules:

- `EXIT_ONLY` / `size_multiplier==0` → False
- `DEFENSIVE` / `CAPITAL_PRESERVATION` → False trừ khi `inferred_position_state == THESIS_HEALTHY` và `change_point_score < 0.45`
- `change_point_score` tăng so với `runtime_state.last_cp[symbol]` > 0.15 → False

### 5.2 `review_positions_and_act`

- Trước `try_brain_v4_reflex_for_position`: đã có — bổ sung tham số `cycle_id` (worker có thể không có — dùng `cycle_id=None` và không persist hoặc tạo `cycle_id` mini cho review-only; **spec:** worker `runner.py` truyền `cycle_id` từ `run()` result — P1: **lưu `cycle_id` trên session** trong `run()` return dict và đọc lại không khả thi; đơn giản hơn: **mỗi worker tick tạo `cycle_id` một lần ở đầu `run_cycle_job`**, truyền vào `SimulationCycle().run(..., brain_cycle_id=...)` và `review_positions_and_act(..., brain_cycle_id=...)`).

**Sửa chữ ký:**

- `SimulationCycle.run(self, db, portfolio_name, symbols, ..., brain_cycle_id: str | None = None)`
- Nếu None → tự sinh trong `run`.

**Proactive exit (§2.4):**

- Sau `pe_cfg = load_proactive_exit_config()`:

```python
pe_cfg = merge_proactive_exit_overlay(pe_cfg, brain_v4_ctx.policy)
```

Overlay bảng deterministic:

| Mode | partial_1r_min_r | trail tighter | reversal threshold |
|------|------------------|----------------|--------------------|
| DEFENSIVE | ×0.85 | +15% aggressiveness | -0.05 (sớm hơn) |
| AGGRESSIVE | ×1.15 | -10% | +0.05 |
| CAPITAL_PRESERVATION | ×0.75 | +25% | -0.08 |
| NORMAL | không đổi | | |

Chỉ sửa **số trong dict config** passed vào `evaluate_position`, **không** sửa file JSON disk.

### 5.3 Priority resolution (§2.5) — một action quản trị mỗi bước

Thứ tự **cố định** trong `review_positions_and_act` (document trong code comment + spec):

1. **Reflex V4 HIGH** (FORCE_EXIT) — đã có  
2. **Kill / time-stop / regime close** (existing)  
3. **Hedge** (existing)  
4. **Reflex V4 MEDIUM** (PARTIAL) — đã có trước proactive — **P1:** chuyển **sau** hedge hay trước? Giữ **sau hedge, trước proactive** để hedge vẫn chạy khi không reflex exit.  
5. **Proactive exit** (đã merge overlay policy)  
6. **TP/SL pattern update** (existing)

Nếu proactive và reflex MEDIUM cùng partial: **reflex đã `continue`** → không chạm proactive cùng tick; OK.

---

## 6. Dashboard / API design

### 6.1 API (`apps/api/server.py`)

| Method | Path | Response |
|--------|------|----------|
| GET | `/brain/v4/latest` | `{ cycle_id, market_state, policy_mode, policy_confidence, btc_regime, portfolio_stress, events_summary }` |
| GET | `/brain/v4/cycle/{cycle_id}` | full join 4 loại event theo cycle_id |
| GET | `/brain/v4/symbol/{symbol}` | last N inference + change_point + policy rows for symbol |
| GET | `/brain/v4/position/{position_id}` | reflex_action_events + policy history từ cycle_ids linked |

**Schema `latest` (tối thiểu):**

```json
{
  "cycle_id": "...",
  "started_at": "...",
  "market_state": "RISK_OFF",
  "policy_mode": "CAPITAL_PRESERVATION",
  "policy_confidence": 0.81,
  "btc_regime": "risk_off",
  "portfolio_stress_score": 0.62,
  "symbols_blocked": true,
  "per_symbol": [
    {
      "symbol": "ETH",
      "token_state": "EXHAUSTION",
      "change_point_score": 0.71,
      "urgency": "MEDIUM",
      "context_break": true
    }
  ]
}
```

**Implementation:** thin queries qua `core/brain/persistence.py` `fetch_*`.

**Caching:** không cache server-side P1; dashboard poll 10–15s.

### 6.2 Dashboard (`apps/dashboard/app.py`)

- Thêm `st.tabs` hoặc `st.expander` **“Brain V4 (P1)”**:
  - **Market-wide:** đọc `/brain/v4/latest` hoặc query DB qua `SessionLocal` (giống pattern hiện tại)
  - **Theo symbol:** `st.selectbox` watchlist + bảng inference/cp cuối
  - **Theo position:** chọn từ open positions → reflex history + `unrealized_R` (tính client-side từ price)

**Graceful degradation:** nếu bảng chưa migrate → hiển thị “Brain P1 DB chưa bật” (try/except query).

---

## 7. Replay design (`core/brain/replay.py`)

### 7.1 Input bundle (1 file JSON hoặc row `brain_replay_snapshots` tùy chọn P1)

Tối thiểu P1: đọc từ DB rows theo `cycle_id`:

- `state_inference_events` where cycle_id
- `change_point_events` where cycle_id
- `policy_mode_events` where cycle_id
- `reflex_action_events` where cycle_id
- `feature_snapshot_json` đủ để gọi lại `infer_*` và `aggregate_change_point` **nếu** snapshot chứa inputs đã chuẩn hóa (P1 bắt buộc lưu trong `feature_snapshot_json`: `klines_digest` hoặc raw OHLCV last N — **khuyến nghị** lưu `closes`, `highs`, `lows`, `vols` arrays 24 float, không lưu full tick)

### 7.2 `recompute_v4_layer(bundle, config_hash_A) -> BrainReplayResult`

- Load config file tại hash A (từ `config/snapshots/` nếu có) hoặc current + flag
- Chạy lại pure functions: `infer_market_state`, `infer_token_state`, `compute_change_point_for_symbol`, `choose_policy_mode`, `resolve_reflex`

### 7.3 Output diff

```python
@dataclass
class BrainReplayDiff:
    state_match: bool
    cp_score_delta: float
    policy_mode_match: bool
    reflex_action_match: bool
```

### 7.4 Use cases

- CLI `python scripts/replay_brain_v4.py --cycle-id X --config path` in ra diff
- So V4 on/off: chạy replay với `enabled=false` trong snapshot config — cần lưu `config_snapshot_json` trong `brain_cycles` (P1 optional column)

---

## 8. Test plan

| ID | Loại | Nội dung |
|----|------|----------|
| T1 | Persist | Insert 4 loại event + query theo cycle_id; FK cycle tồn tại |
| T2 | Trace | `trade.brain_cycle_id` set khi mở lệnh; query ngược từ trade_id → events |
| T3 | Policy size | Mock `RiskDecision(1000)` → `apply_policy_size_modifier` với `CAPITAL_PRESERVATION` → giảm đúng hệ số |
| T4 | Entry overlay | `DEFENSIVE` + confidence thấp → reject trước risk |
| T5 | Scale gate | `EXIT_ONLY` → `scale_in_policy_gate` False |
| T6 | Proactive merge | `merge_proactive_exit_overlay` thay đổi key số đo được |
| T7 | Replay | Cùng snapshot + cùng config → diff toàn match |
| T8 | Replay config | Đổi weight change_point trong config → `cp_score_delta != 0` |
| T9 | API | `GET /brain/v4/latest` 200 + schema keys (pytest + TestClient) |
| T10 | Dashboard smoke | Import page function không lỗi khi DB trống |

---

## 9. Acceptance checklist P1

| # | Tiêu chí | Pass khi |
|---|----------|----------|
| A1 | 4 class event persist | Có bảng + ghi được mỗi cycle có V4 bật |
| A2 | Trace outcome → reasoning | Từ `trade_id` hoặc `position_id` query được chuỗi event cùng `cycle_id` |
| A3 | Policy → sizing | `final_size_usd` sau risk giảm/tăng theo `PolicyModifiers.size_multiplier` + stress (log hoặc assert test) |
| A4 | Policy → entry | Có nhánh reject thêm với `reason_code` BRAIN_V4_* trước `_risk_assess_entry` |
| A5 | Policy → scale-in | Scale-in không chạy khi policy cấm |
| A6 | Policy → proactive | `evaluate_position` nhận dict đã merge overlay |
| A7 | Dashboard | Có section hiển thị market + symbol + position summary |
| A8 | API | Ít nhất `/brain/v4/latest` + `/brain/v4/cycle/{id}` |
| A9 | Replay | CLI hoặc hàm `recompute_v4_layer` + diff deterministic |
| A10 | Tests | T1–T7 pass trên CI/local |

---

## 10. Config bổ sung (`brain_v4.v1.json` section `p1`)

```json
"p1": {
  "persistence": { "enabled": true, "retention_days": 90 },
  "entry": { "confidence_penalty_per_strictness": 0.08, "min_regime_clarity_defensive": 0.42 },
  "sizing": { "stress_defensive_mult": 0.5, "max_notional_per_symbol_usd": null },
  "scale_in": { "max_cp_increase_for_allow": 0.15 },
  "proactive_overlay": { "enabled": true }
}
```

---

## 11. Trạng thái triển khai (mã nguồn)

- [x] Bảng event + `trades.brain_cycle_id` + `trades.decision_trace_id` + `ensure_*` SQLite
- [x] `market_decision_trace_id` + `decision_trace_id` trên inference / CP / policy / reflex; `brain_sizing_events` (4 mức size + breakdown JSON)
- [x] `build_brain_v4_tick_context` — một `brain_cycle_id` / tick, context dùng chung `review_positions_and_act` + `run`
- [x] `cycle.py`: persist inference + CP theo symbol, entry overlay, sizing breakdown + persist, scale-in gate, gán `open_trade.brain_cycle_id` + `decision_trace_id`
- [x] `merge_proactive_exit_overlay` trong review; reflex ghi `reflex_action_events` (trace riêng + `market_decision_trace_id`)
- [x] API: `GET /brain/v4/latest`, `/cycle/{id}`, `/symbol/{s}`, `/position/{id}`, `/trace/{decision_trace_id}`
- [x] Dashboard: section Brain V4 P1 trace
- [x] `core/brain/replay.py` + `scripts/replay_brain_v4.py`
- [x] `core/brain/symbol_policy.py` — rule market vs symbol (override có điều kiện)
- [x] `tests/test_brain_v4_p1.py` (bổ sung cho bảng T §8)

---

*Tài liệu này là hợp đồng triển khai P1; mọi PR phải map thay đổi vào mục §5 và tick §9.*
