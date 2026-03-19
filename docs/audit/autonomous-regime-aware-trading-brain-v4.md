# Autonomous Regime-Aware Trading Brain v4

**Canonical copy trong repo:** `trading-lab-pro-v3/docs/audit/` (đồng bộ với `document/autonomous-regime-aware-trading-brain-v4.md`).  

### Trạng thái triển khai P0 (đã có trong code)

| Mục | Vị trí |
|-----|--------|
| State inference | `core/brain/state_inference.py` |
| Change-point | `core/brain/change_point.py` |
| Meta-policy | `core/brain/meta_policy.py` + `policy_templates.py` |
| Reflex | `core/brain/reflex.py` + `integration.py` |
| Runtime / hysteresis | `core/brain/runtime_state.py` → `storage/brain_v4_runtime.json` |
| Cycle context | `core/brain/context.py` |
| Tích hợp cycle | `SimulationCycle.run` (`should_block_cycle_symbol`: bỏ qua cả symbol khi `EXIT_ONLY` hoặc `size_multiplier≤0`), `review_positions_and_act` (reflex trước hedge/proactive) |
| Config | `config/brain_v4.v1.example.json` — copy thành `brain_v4.v1.json` để override; `enabled: false` tắt toàn bộ |
| Test | `tests/test_brain_v4.py` |

**Chưa làm (P1/P2 theo §9–10):** bảng DB `brain_runtime_state` / event log riêng, dashboard reasoning view, proposal workflow, replay đầy đủ multi-layer.

**Spec triển khai P1 (adaptive operating layer):** [`brain-v4-p1-adaptive-layer-spec.md`](./brain-v4-p1-adaptive-layer-spec.md)

## Mục tiêu

Nâng cấp Trading Lab từ một hệ thống **config-driven + deterministic scoring** thành một hệ thống **state-driven, regime-aware, policy-adaptive**, có khả năng:

- tự suy ra trạng thái thị trường thay vì phụ thuộc quá nhiều vào threshold rời rạc
- tự chọn mode hành vi phù hợp trong biên an toàn
- phát hiện token/market đổi trạng thái đột ngột trước khi thua lỗ lớn
- phản xạ bảo vệ vị thế nhanh hơn stop-loss tĩnh
- học từ chất lượng suy luận trạng thái, policy choice và reflex timing

---

## 1. Critique của brain hiện tại (v3 / current brain)

### 1.1 Vấn đề 1: quá config-driven

Brain hiện tại vẫn mang đặc trưng của một rule engine nâng cấp:

- `weights`
- `caps`
- `entry_thresholds`
- `playbook`
- `portfolio_exposure_cap_usd`
- nhiều reason code gắn trực tiếp với ngưỡng số học

Điều này tạo ra 3 hệ quả:

1. **Khó mở rộng**: càng thêm tình huống càng thêm config.
2. **Khó giải thích ở cấp chiến lược**: hệ thống trả lời “vì score < threshold”, chưa trả lời “vì state hiện tại không còn phù hợp để hành động”.
3. **Giả thông minh**: nhìn có vẻ tinh vi, nhưng thật ra chỉ là nhiều if/else hơn.

### 1.2 Vấn đề 2: quá reactive

Các biến như:

- `reversal_risk_score`
- `btc_context_score`
- `funding_rate_signal`
- `relative_volume_score`
- `regime_shift` reason

mới dừng ở mức **reactive scoring**. Hệ thống phản ứng khi một số feature đã đủ xấu, nhưng chưa có tầng suy luận:

- token đang continuation hay exhaustion
- breakout còn khỏe hay đã bắt đầu fail
- market đang risk-on hay shock/unstable
- thesis còn khỏe hay đã broken trước khi chạm SL

### 1.3 Tình huống hiện tại có thể phản ứng chậm

1. **Failed breakout sau pump mạnh**
   - score vẫn còn tốt do dữ liệu chậm cập nhật hoặc lookback còn đẹp
   - hệ thống chưa kịp hạ mode hành vi

2. **BTC-led break**
   - alt token chưa chạm SL nhưng thesis thực tế đã gãy vì market leader đổi pha

3. **Funding/OI crowd unwind**
   - định hướng trước đó vẫn có thể cho HOLD trong khi market đang unwind rất nhanh

4. **Liquidity vacuum / shock bar**
   - stop tĩnh là phản xạ quá muộn

### 1.4 Phân loại config hiện tại

#### A. Core safety config — nên giữ

- max leverage hard cap
- kill switch hard cap
- max daily drawdown
- max portfolio exposure
- forbidden symbols / forbidden strategies
- approval / replay / audit constraints

#### B. Operational config — nên giảm mạnh

- quá nhiều weight cho từng signal
- nhiều threshold entry rời rạc
- nhiều cap riêng cho từng playbook branch
- số lượng lớn no-trade gates chi tiết

#### C. Adaptive config — nên internalize vào runtime policy layer

- entry strictness
- threshold offset
- scale-in allowance
- trailing aggressiveness
- partial TP speed
- sizing multiplier
- no-trade sensitivity

---

## 2. V4 Architecture Overview

V4 thêm 2 lớp lớn vào brain hiện tại.

### 2.1 Meta-Policy Layer

Đây là tầng quyết định hệ thống nên hành xử theo mode nào.

#### Policy modes đề xuất

- `DEFENSIVE`
- `NORMAL`
- `AGGRESSIVE`
- `CAPITAL_PRESERVATION`
- `EXIT_ONLY`

#### Input

- market state
- token state distribution
- portfolio stress
- recent execution quality
- recent regime stability
- BTC context
- anomaly / shock flags

#### Output

- `active_policy_mode`
- `policy_confidence`
- `policy_reason_codes[]`
- `policy_ttl_sec`
- `re_evaluate_after`

### 2.2 Change-Point / Regime-Shift Detection Layer

Đây là tầng chuyên phát hiện khi context thay đổi đột ngột.

#### Output

- `change_point_score`
- `context_break_flag`
- `shift_type`
- `urgency_level`
- `recommended_protective_action`

#### shift_type đề xuất

- `FAILED_BREAKOUT`
- `EXHAUSTION_BREAK`
- `BTC_LED_BREAK`
- `VOLATILITY_SHOCK`
- `LIQUIDITY_VACUUM`
- `CROWD_UNWIND`
- `THESIS_INVALIDATION_PRE_SL`

---

## 3. State Inference Engine

Mục tiêu của tầng này là: từ nhiều feature rời rạc, suy ra một **state có nghĩa chiến lược**.

### 3.1 Market states

| State | Ý nghĩa | Feature chính |
|---|---|---|
| `RISK_ON_TRENDING` | thị trường thuận xu hướng, continuation dễ thành công | btc_context tốt, breadth tốt, volatility lành mạnh |
| `RISK_ON_EXHAUSTING` | còn tăng nhưng rủi ro đu đỉnh cao | momentum cao nhưng reversal risk tăng |
| `BALANCED` | không rõ edge toàn thị trường | context trung tính, breadth yếu |
| `RISK_OFF` | ưu tiên phòng thủ | BTC yếu, alt underperform, breakdown tăng |
| `SHOCK_UNSTABLE` | biến động bất thường, policy phải phòng thủ mạnh | vol shock, change point cao, BTC/context break |

### 3.2 Token states

| State | Ý nghĩa |
|---|---|
| `CONTINUATION` | xu hướng tiếp diễn khỏe |
| `EARLY_BREAKOUT` | vừa ra khỏi nền, còn dư địa |
| `LATE_BREAKOUT` | đã tăng mạnh, rủi ro exhaustion |
| `EXHAUSTION` | động lượng suy giảm sau extension |
| `FAILED_BREAKOUT` | vượt cản thất bại, quay lại range |
| `MEAN_REVERSION_CANDIDATE` | quá lệch khỏi cân bằng |
| `PANIC_UNWIND` | bị xả nhanh, thesis momentum invalid |
| `DEAD_CHOP` | nhiễu, không có edge rõ |

### 3.3 Position states

| State | Ý nghĩa |
|---|---|
| `THESIS_HEALTHY` | luận điểm còn khỏe |
| `THESIS_STRETCHED` | đang lời nhưng bắt đầu kéo dài quá mức |
| `THESIS_WEAK` | luận điểm suy yếu, nên bảo vệ |
| `THESIS_BROKEN` | không còn lý do giữ |
| `PROFIT_PROTECTED` | đã khóa lợi nhuận đáng kể |
| `EXIT_URGENT` | cần hành động ngay |

### 3.4 Logic suy ra state

#### Pseudo code

```python
market_state = infer_market_state(features)
token_state = infer_token_state(symbol_features, market_state)
position_state = infer_position_state(position, symbol_features, market_state, token_state)
```

#### Ví dụ logic rule-based ban đầu

```python
def infer_token_state(f):
    if f.change_point_score >= 0.85 and f.breakout_failed:
        return "FAILED_BREAKOUT", 0.92
    if f.momentum_score > 0.75 and f.trend_strength_score > 0.7 and f.reversal_risk_score < 0.35:
        return "CONTINUATION", 0.78
    if f.momentum_score > 0.85 and f.reversal_risk_score > 0.6 and f.relative_volume_score is falling:
        return "EXHAUSTION", 0.74
    if f.volatility_shock and f.price_structure_break:
        return "PANIC_UNWIND", 0.88
    return "DEAD_CHOP", 0.55
```

### 3.5 Confidence + hysteresis

Để tránh flip liên tục, mỗi state cần:

- `state_confidence`
- `state_min_hold_bars`
- `state_switch_margin`
- `state_decay`

#### Quy tắc

- chỉ switch state nếu state mới vượt state cũ ít nhất `switch_margin`
- giữ tối thiểu `N` bars trừ khi có emergency break
- `SHOCK_UNSTABLE` và `THESIS_BROKEN` được phép bypass hysteresis

---

## 4. Meta-Policy Adaptation

### 4.1 Những gì được adaptive

- entry strictness
- score threshold offset
- scale-in allowance
- trailing aggressiveness
- partial TP behavior
- position sizing multiplier
- no-trade sensitivity

### 4.2 Những gì không được adaptive

- max leverage hard cap
- max daily drawdown hard cap
- kill switch hard cap
- max portfolio exposure hard cap
- forbidden strategies / symbols
- audit / replay integrity rules

### 4.3 Policy mode matrix

| Mode | Bối cảnh điển hình | Entry | Management | Size multiplier | Allowed | Disabled |
|---|---|---|---|---:|---|---|
| `DEFENSIVE` | market không rõ hoặc risk tăng | rất chặt | protect nhanh | 0.5x | hold, partial, reduce | scale-in mạnh |
| `NORMAL` | market cân bằng tích cực | chuẩn | cân bằng | 1.0x | enter, hold, trail | none |
| `AGGRESSIVE` | continuation mạnh, regime rõ | bớt chặt | cho phép hold winner | 1.25x | scale-in chọn lọc | reduce sớm quá mức |
| `CAPITAL_PRESERVATION` | equity stress hoặc regime xấu dần | cực chặt | partial nhanh, trail chặt | 0.35x | reduce, trail, exit | entry lớn |
| `EXIT_ONLY` | shock/unstable, kill conditions gần tới | cấm entry | chỉ thoát/bảo vệ | 0.0x | exit, reduce | tất cả entry mới |

### 4.4 Policy switching rules

#### Input

- `market_state`
- `recent_policy_outcome_quality`
- `portfolio_stress_score`
- `change_point_score`
- `btc_context_score`
- `shock_flags`

#### Pseudo code

```python
def choose_policy_mode(ctx):
    if ctx.kill_risk_near_limit or ctx.market_state == "SHOCK_UNSTABLE":
        return "EXIT_ONLY"
    if ctx.portfolio_stress_score > 0.8:
        return "CAPITAL_PRESERVATION"
    if ctx.market_state == "RISK_ON_TRENDING" and ctx.regime_stability > 0.7 and ctx.change_point_score < 0.35:
        return "AGGRESSIVE"
    if ctx.market_state in ["BALANCED", "RISK_ON_EXHAUSTING"]:
        return "DEFENSIVE"
    return "NORMAL"
```

#### Cooldown

- `policy_min_ttl_sec`
- `policy_switch_cooldown_sec`
- emergency override nếu `context_break_flag = true`

---

## 5. Change-Point / Context-Break Detection

Đây là tầng quan trọng nhất cho case token đổi trạng thái đột ngột.

### 5.1 Price structure break detector

#### Dấu hiệu

- breakout failed rồi close lại trong range
- mất last impulse low/high
- rejection mạnh sau extension
- mất cấu trúc micro-trend

#### Output

- `structure_break_score`
- `break_type`

### 5.2 Participation break detector

#### Dấu hiệu

- relative volume collapse
- spike volume nhưng follow-through kém
- slippage/spread proxy xấu lên
- bid support biến mất nhanh

#### Output

- `participation_break_score`

### 5.3 Market leader break detector

#### Dấu hiệu

- BTC đổi state nhanh sang `RISK_OFF` hoặc `SHOCK_UNSTABLE`
- tương quan alt với BTC tăng mạnh trong lúc BTC gãy

#### Output

- `btc_leader_break_score`

### 5.4 Derivatives crowding break detector

#### Dấu hiệu

- funding cực đoan kéo dài
- OI divergence nếu có
- squeeze/unwind bất đối xứng

#### Output

- `crowding_break_score`

### 5.5 Abnormal shock detector

#### Dấu hiệu

- move speed bất thường
- volatility jump
- gap-like candles
- state divergence quá nhanh so với baseline gần nhất

#### Output

- `shock_score`

### 5.6 Tổng hợp change point

```python
change_point_score = weighted_sum(
    structure_break_score,
    participation_break_score,
    btc_leader_break_score,
    crowding_break_score,
    shock_score,
)
```

### 5.7 False positive guard

- yêu cầu xác nhận trên ít nhất 2 detector với urgency >= medium
- hoặc 1 detector nhưng score cực cao + BTC/context break đồng thời
- cooldown để không spam reflex action liên tục

---

## 6. Protective Reflex Layer

Khi `context_break_flag` hoặc `change_point_score` cao, hệ thống phải phản xạ nhanh hơn stop-loss tĩnh.

### 6.1 LOW urgency

#### Điều kiện

- `change_point_score >= 0.55`
- thesis chưa broken hoàn toàn

#### Action

- block scale-in
- giảm entry score cho lệnh mới cùng symbol
- arm tighter trail
- hạ policy mode xuống 1 bậc nếu đang `AGGRESSIVE`

### 6.2 MEDIUM urgency

#### Điều kiện

- `change_point_score >= 0.7`
- hoặc `market_state = RISK_OFF` khi đang giữ alt long

#### Action

- reduce partial
- tighten stop
- suspend new entries cùng symbol
- downgrade policy sang `DEFENSIVE` hoặc `CAPITAL_PRESERVATION`

### 6.3 HIGH urgency

#### Điều kiện

- `change_point_score >= 0.85`
- hoặc `THESIS_BROKEN`
- hoặc `BTC_LED_BREAK + structure_break`

#### Action

- force reduce / force exit
- portfolio de-risk
- switch `EXIT_ONLY`
- ghi rõ `thesis_broken_before_sl = true`

### 6.4 Rollback condition

Chỉ gỡ reflex state khi:

- shock score giảm dưới ngưỡng trong `N` bars liên tiếp
- market/token state ổn định trở lại
- policy cooldown kết thúc

---

## 7. Config Reduction Strategy

### 7.1 Tầng 1 — Hard safety config

Giữ rất ít field, ví dụ:

```json
{
  "max_daily_drawdown_r": 3.0,
  "max_portfolio_exposure_usd": 1500,
  "max_leverage": 3,
  "kill_switch_r_threshold": 5.0,
  "forbidden_symbols": [],
  "require_human_approval_for_major_policy_change": true
}
```

### 7.2 Tầng 2 — Policy templates

Thay vì hàng trăm threshold rời rạc, giữ vài template mode:

- defensive
- normal
- aggressive
- capital_preservation
- exit_only

Mỗi template chỉ chứa offset và behavior class, không chứa toàn bộ não.

### 7.3 Tầng 3 — Learned / adaptive runtime state

Không nên nằm trong config production tĩnh. Phải lưu như runtime state:

- current policy mode
- detector baselines
- recent regime stability
- confidence priors
- recent false alarm rate
- proposal candidates

### 7.4 Field migration strategy

#### Giữ lại trong config

- safety caps
- approval rules
- policy template names
- detector hard floors/ceilings

#### Bỏ hoặc giảm khỏi config hiện tại

- quá nhiều entry thresholds riêng lẻ
- playbook branch numbers chi tiết
- nhiều weight granular cho từng case

#### Chuyển sang runtime adaptive state

- entry strictness offset
- scale-in permission state
- trail tightness state
- no-trade sensitivity
- mode-specific confidence prior

---

## 8. Learning Loop for Adaptive Policy

V4 không chỉ học thắng/thua, mà còn học:

- state inference có đúng không
- policy mode có phù hợp không
- reflex có nhanh không
- detector có quá nhạy hoặc quá chậm không

### 8.1 Outcome evaluation fields

Mỗi outcome cần thêm:

- `state_inference_quality`
- `policy_choice_quality`
- `reflex_timing_quality`
- `damage_prevented_score`
- `missed_opportunity_score`
- `false_alarm_penalty`

### 8.2 Proposal system mở rộng

Hệ thống có thể tạo proposal cho:

- detector sensitivity adjustment
- policy switching tuning
- playbook refinement
- hysteresis tuning

### 8.3 Governance

#### Auto-allowed

- update baseline runtime state
- update rolling priors
- update confidence memory

#### Human approval required

- thay đổi hard safety caps
- thay đổi policy template structure
- thay đổi detector sensitivity vượt range an toàn
- thêm/bỏ action classes

---

## 9. Implementation Plan for Current Repo

### 9.1 Module mới

- `core/brain/state_inference.py`
- `core/brain/meta_policy.py`
- `core/brain/change_point.py`
- `core/brain/reflex.py`
- `core/brain/runtime_state.py`
- `core/brain/policy_templates.py`

### 9.2 Module cần sửa

- `SimulationCycle`
  - thêm state inference trước decision scoring
  - thêm meta-policy selection
  - thêm change-point scoring
- `review_positions_and_act`
  - thêm reflex layer trước logic manage position hiện tại
- journal/outcome pipeline
  - thêm state quality, policy quality, reflex timing fields
- dashboard reasoning view
  - hiển thị market state, token state, policy mode, change point, reflex action

### 9.3 Flow tích hợp

```text
market data
→ feature extraction
→ state inference
→ meta-policy selection
→ entry / management brain
→ change-point detection
→ protective reflex
→ execution
→ journal / outcome
→ learning artifacts / proposals
```

### 9.4 DB tables mới

- `brain_runtime_state`
- `state_inference_events`
- `policy_mode_events`
- `change_point_events`
- `reflex_actions`
- mở rộng `brain_snapshots`
- mở rộng `brain_decisions`

### 9.5 Migration

- migration thêm bảng event log mới
- backfill `policy_mode = NORMAL` cho dữ liệu cũ nếu cần
- thêm `trace_id` nối cycle → state → policy → decision → outcome

### 9.6 Replay additions

Replay không chỉ decision score mà phải replay:

- inferred states
- chosen policy mode
- detector outputs
- reflex action
- final management decision

### 9.7 Test strategy

#### Unit tests

- infer market/token/position state
- policy switching
- detector aggregation
- reflex priority

#### Scenario tests

- breakout continuation bình thường
- failed breakout nhanh
- BTC-led break khi alt đang lời
- volatility shock trong live trade
- false alarm detector

#### Regression tests

- policy mode không flip quá nhiều
- reflex không spam exit sai
- config reduction không phá safety rules

---

## 10. One Big Upgrade Checklist

### P0

- state inference
- meta-policy layer
- context-break detector
- protective reflex
- persist + replay

### P1

- adaptive policy evaluation
- detector calibration tracking
- dashboard reasoning view
- runtime state quality analytics

### P2

- proposal generation for policy tuning
- approval workflow
- portfolio-wide reflex coordination
- detector ensemble refinement

---

## 11. 4 câu hỏi bắt buộc mà V4 phải trả lời được

### 1. Hệ thống biết gì?

- feature thị trường
- feature token
- feature vị thế
- detector outputs
- portfolio stress

### 2. Hệ thống suy ra state gì?

- market state
- token state
- position state
- context break / shift type

### 3. Hệ thống tự điều chỉnh gì?

- policy mode
- entry strictness
- trail aggressiveness
- scale-in allowance
- no-trade sensitivity

### 4. Hệ thống phản ứng gì khi state đổi đột ngột?

- block add
- downgrade mode
- reduce
- tighten stop
- force exit
- switch `EXIT_ONLY`

---

## Kết luận

V4 không nhằm xóa sạch config, mà nhằm **giảm vai trò của config xuống mức khung an toàn**. Phần “não” phải chuyển từ:

- `threshold-driven`
- `score-reactive`

sang:

- `state-inferred`
- `policy-adaptive`
- `change-point-sensitive`
- `protective-reflex-enabled`

Đó mới là bước tiến thực sự từ một brain tốt sang một hệ thống giao dịch có phong cách của một chuyên gia giỏi.
