# Trading Lab Pro — Brain V4 P2 + Thesis-Aware Trade Management (Merged V8)

## 1. Executive summary

Tài liệu này hợp nhất hai hướng nâng cấp:

1. **Brain V4 P2**: controlled learning + portfolio-wide coordination.
2. **Thesis-aware trade management**: quản lý lệnh theo đúng giả thuyết giao dịch và evolution của thị trường sau entry.

Mục tiêu của bản merged này là đưa hệ thống từ mức:

- biết state / change-point / policy / reflex ở cấp symbol
- có log / replay / reasoning cơ bản

lên mức:

- **chấm được chất lượng quyết định sau outcome**
- **học có kiểm soát, có governance, có approval / rollback**
- **điều phối rủi ro ở cấp danh mục**
- **quản lý lệnh theo thesis thay vì chỉ signal + SL/TP tĩnh**
- **nhận diện sớm dấu hiệu cho thấy vùng chiến lược có thể thay đổi**
- **phản ứng ngay theo cấp độ nguy hiểm**

Phần thesis-aware không phải một layer tách rời khỏi P2. Nó là một input cốt lõi cho:

- evaluation quality
- learning artifacts
- policy / proposal generation
- portfolio coordination
- replay / approval evidence

Nói ngắn gọn:

- **P1** giúp bot biết “đang thấy gì” và “đang phản ứng gì”.
- **P2 merged** giúp bot biết “điều đó tốt hay xấu”, “thesis còn sống hay đã hỏng”, “có nên thay đổi cách cấp vốn / mode / execution hay không”.

---

## 2. Gap from P1

### 2.1 P1 đã có gì

P1 đã có nền tảng adaptive operating layer khá tốt:

- state inference
- change-point detection
- meta-policy / mode switching
- reflex layer
- runtime state
- persistence events
- decision trace linkage
- policy_apply vào entry / sizing / scale-in / proactive exit
- API / dashboard reasoning cơ bản
- replay cơ bản

### 2.2 P1 còn thiếu gì để thành learning brain

P1 hiện chủ yếu biết:

- event nào đã xảy ra
- decision nào đã được chọn
- reflex nào đã được kích hoạt
- policy mode nào đã đổi

Nhưng chưa biết đủ rõ:

- event đó **có tốt hay xấu**
- inference đó **có đúng với diễn biến sau đó không**
- reflex đó **cứu lệnh hay làm mất lợi nhuận**
- policy mode đó **có phù hợp với regime / stress / BTC context không**
- thesis của trade **còn đúng hay đã hỏng trước khi SL chạm**
- change-point đó là **false alarm** hay **context break thật**

### 2.3 P1 còn thiếu gì để thành portfolio brain

P1 mạnh ở cấp symbol / tick, nhưng còn yếu ở cấp danh mục:

- chưa có portfolio state inference rõ ràng
- chưa đo cluster / correlation risk đủ thực dụng
- chưa có block / de-risk ở cấp cluster
- chưa có portfolio reflex đủ mạnh khi nhiều symbol cùng shock
- chưa biết khi nào danh mục đang “còn slot nhưng không còn edge”

### 2.4 P1 còn thiếu gì để thành thesis-aware system

Hiện hệ thống vẫn nghiêng về mô hình:

`signal -> entry -> generic review -> SL/TP/proactive`

Thiếu các tầng sau:

- mỗi lệnh chưa có **trade thesis** rõ ràng
- chưa đánh giá liên tục **các nến sau entry** còn ủng hộ thesis hay không
- chưa có state machine:
  - NORMAL
  - WARNING
  - DANGER
  - INVALID
- chưa đảm bảo cách quản lý lệnh **tương đồng với chiến lược và bối cảnh token tại thời điểm đó**
- chưa có cơ chế nhận diện sớm việc **vùng chiến lược đang đổi** hoặc **xác suất cao sắp đổi**

### 2.5 Kết luận gap

P2 merged phải lấp 3 gap cùng lúc:

1. **Evaluation gap**: event có tốt hay xấu.
2. **Learning governance gap**: học từ outcome nhưng không tự phá production.
3. **Execution-thesis gap**: cách quản lý lệnh phải bám đúng thesis và phát hiện sớm khi thesis / strategic zone bị đe dọa.

---

## 3. P2 architecture overview

### 3.1 Lớp mới trong P2 merged

Thiết kế tối thiểu các module sau:

- `core/brain/evaluation.py`
- `core/brain/learning_artifacts.py`
- `core/brain/proposals.py`
- `core/brain/approval.py`
- `core/brain/versioning.py`
- `core/brain/portfolio_state.py`
- `core/brain/portfolio_clusters.py`
- `core/brain/portfolio_reflex.py`
- `core/profit/thesis_profiles.py`
- `core/profit/thesis_monitor.py`
- `core/profit/thesis_actions.py`
- `core/profit/thesis_metrics.py`

### 3.2 Config mới

- `config/thesis_management.v1.json`
- `config/brain_learning.v1.json`
- `config/portfolio_brain.v1.json`
- `config/proposal_governance.v1.json`

### 3.3 Luồng dữ liệu tổng quát

1. **Tick / cycle**:
   - build market / symbol / portfolio context
   - infer state / detect change-point / choose policy
   - evaluate entry candidates
   - monitor open trades bằng thesis-aware layer
   - apply symbol reflex + portfolio reflex

2. **Khi có action / outcome**:
   - tạo immediate evaluation
   - ghi thesis state transition
   - persist decision / reflex / portfolio events

3. **Khi trade đóng hoặc sau N bars**:
   - delayed evaluation
   - quality scoring
   - learning artifact generation

4. **Cuối ngày / batch reflection**:
   - aggregate evaluation
   - detect repeat patterns
   - proposal candidate generation
   - governance / approval queue

5. **Replay / simulation**:
   - so sánh config cũ / mới
   - đo impact của proposal
   - xác nhận proposal trước apply

### 3.4 Nguyên tắc thiết kế bắt buộc

- không thêm strategy mới
- không cho LLM tự vào lệnh
- không cho hệ thống tự sửa production config vô điều kiện
- mọi score / proposal / reflex phải link được với trace / trade / outcome / config version
- mọi trade management phải đi qua logic **thesis fit**

---

## 4. Evaluation layer design

### 4.1 Nhóm quality score bắt buộc

#### A. State inference quality

Đánh giá:

- market state inferred có hợp lý với diễn biến sau đó không
- token state inferred có đúng pha không
- position state inferred có phản ánh đúng thesis health không

**Input**:
- state inference event
- N bars sau inference
- BTC / cluster context
- thesis state transitions nếu có position

**Output**:
- `state_inference_quality_score` (0–1)
- `state_inference_quality_label` (`good`, `mixed`, `poor`)
- `state_inference_reason_codes`

**Immediate evaluation**:
- consistency nội bộ giữa inferred state và context

**Delayed evaluation**:
- sau 3 / 6 / 12 bars hoặc khi trade đóng
- so inferred state với realized market path

**Persist**:
- `decision_evaluations`
- link bằng `decision_trace_id`, `market_decision_trace_id`, `brain_cycle_id`

#### B. Change-point quality

Đánh giá:

- detector phát hiện sớm hay trễ
- false alarm hay real context break
- urgency có hợp lý không

**Input**:
- change-point event
- trước / sau N bars
- subsequent reflex / policy actions
- realized damage / damage prevented

**Output**:
- `change_point_quality_score`
- `timeliness_score`
- `false_alarm_flag`
- `missed_break_flag`

**Persist**:
- `change_point_evaluations`

#### C. Policy choice quality

Đánh giá:

- policy mode được chọn có hợp regime / stress / BTC / portfolio context không
- có quá defensive hay quá aggressive không

**Input**:
- previous mode / new mode
- regime, state, portfolio stress, thesis warning distribution
- outcome window sau policy switch

**Output**:
- `policy_choice_quality_score`
- `over_defensive_score`
- `over_aggressive_score`

**Persist**:
- `policy_evaluations`

#### D. Reflex timing quality

Đánh giá:

- reflex cứu được lệnh hay thoát non
- force exit có ngăn lỗ lớn hay cắt đúng đáy
- partial reduce có giảm damage hay chỉ giảm profit không cần thiết
- thesis-aware action có đúng cấp độ nguy hiểm hay không

**Input**:
- reflex event
- thesis state trước / sau reflex
- adverse / favorable excursion sau reflex
- exit reason / remaining path

**Output**:
- `reflex_timing_quality_score`
- `damage_prevented_score`
- `false_alarm_cost`
- `missed_opportunity_cost`

**Persist**:
- `reflex_evaluations`

#### E. Outcome quality

Bắt buộc có:

- `decision_quality_score`
- `follow_plan_score`
- `edge_realization_score`
- `damage_prevented_score`
- `missed_opportunity_cost`
- `false_alarm_cost`
- `thesis_management_quality_score`

**Input**:
- trade plan / thesis metadata / actual outcome
- realized R / MAE / MFE / hold time
- thesis transitions during trade
- reflex / proactive / portfolio overrides

**Output**:
- consolidated evaluation row

**Persist**:
- `decision_evaluations` + link tới trade / outcome / position

---

### 4.2 Thesis-aware evaluation layer

Phần này là bổ sung bắt buộc cho P2 merged.

#### 4.2.1 Thesis metadata phải gắn vào trade khi mở lệnh

Tối thiểu lưu:

- `thesis_type`
- `thesis_version`
- `thesis_metadata_json`
- `thesis_state`
- `thesis_last_score`
- `thesis_last_reason`
- `thesis_warning_count`
- `thesis_danger_count`

#### 4.2.2 Thesis state machine

Mỗi trade phải được monitor liên tục theo 4 trạng thái:

- `NORMAL`
- `WARNING`
- `DANGER`
- `INVALID`

#### 4.2.3 Thesis quality score

Tạo score riêng:

- `thesis_fit_score`: setup và management có phù hợp thesis không
- `thesis_survival_score`: thesis sống được bao lâu trước khi invalid
- `warning_response_quality`: warning có được xử lý đúng không
- `danger_response_quality`: danger có bị phản ứng chậm không
- `invalidation_response_latency_bars`: chậm bao nhiêu bars từ invalid tới action
- `strategic_zone_shift_detection_score`: hệ thống có nhận ra vùng chiến lược đang đổi không

#### 4.2.4 Strategic zone shift / danger forecasting

Đây là phần user yêu cầu phải rõ.

Hệ thống phải nhận diện không chỉ “đã invalid” mà cả **xác suất cao vùng chiến lược sắp thay đổi**.

Tạo khái niệm mới:

- `zone_shift_risk_score` (0–1)
- `zone_shift_risk_level` (`low`, `elevated`, `high`, `critical`)
- `zone_shift_reason_codes`

Ví dụ với breakout long, dấu hiệu vùng chiến lược sắp đổi:

- follow-through chậm hơn kỳ vọng quá số bars cho phép
- volume hỗ trợ giảm nhanh
- nhiều upper wick liên tiếp
- close quay lại sát breakout zone
- BTC / cluster context chuyển xấu cùng lúc
- adverse expansion xuất hiện nhưng chưa chạm invalidation

Ví dụ với mean reversion long:

- sell pressure không chậm lại
- bounce yếu bất thường
- breakdown tiếp với volume tăng
- cluster cùng loại đang shock

**Nguyên tắc**:
- `zone_shift_risk_score` cao chưa đồng nghĩa invalid ngay
- nhưng phải cho phép hệ thống:
  - reduce size sớm
  - tighten SL theo profile của thesis
  - block scale-in
  - escalate policy / portfolio stress

---

### 4.3 Immediate vs delayed evaluation

#### Immediate evaluation

Chạy:

- ngay sau entry
- ngay sau reflex / partial / close / policy switch
- ngay sau thesis state transition lớn (`WARNING->DANGER`, `DANGER->INVALID`)

Mục tiêu:

- log sự kiện
- chấm chất lượng phản ứng gần tức thời
- ghi lại bối cảnh chưa bị mất

#### Delayed evaluation

Chạy:

- sau N bars
- khi trade đóng
- cuối ngày

Mục tiêu:

- đo đúng outcome quality
- đo missed opportunity / false alarm
- đo state / policy / reflex quality theo realized path

---

## 5. Learning artifacts + proposal governance

### 5.1 LearningArtifact bắt buộc

Bảng / object `LearningArtifact` phải chứa tối thiểu:

- `artifact_id`
- `decision_trace_id`
- `market_decision_trace_id`
- `brain_cycle_id`
- `symbol`
- `position_id`
- `trade_id`
- `outcome_id`
- `strategy`
- `regime`
- `side`
- `loss_category` hoặc `win_pattern`
- `expected_vs_actual`
- `state_inference_quality`
- `change_point_quality`
- `policy_choice_quality`
- `reflex_timing_quality`
- `thesis_management_quality`
- `zone_shift_detection_quality`
- `key_lesson_summary`
- `confidence_level`
- `proposal_candidate_flag`

### 5.2 Loss / win taxonomy phải thêm thesis-aware labels

Ví dụ loss categories:

- `late_breakout_entry`
- `fake_breakout_follow_through_fail`
- `mean_reversion_caught_falling_knife`
- `warning_not_acted`
- `danger_acted_too_late`
- `invalid_but_waited_for_sl`
- `portfolio_stress_ignored`
- `cluster_shock_not_respected`

Ví dụ win patterns:

- `clean_breakout_follow_through`
- `breakout_retest_hold_success`
- `mean_reversion_bounce_to_equilibrium`
- `sweep_reversal_confirmed`
- `danger_reduce_saved_trade`
- `portfolio_reflex_prevented_damage`

### 5.3 Proposal engine

Proposal engine có thể sinh proposal cho:

- detector sensitivity adjustment
- policy switch tuning
- playbook refinement
- size multiplier tuning
- no-trade sensitivity tuning
- partial / trail / proactive behavior tuning
- thesis profile tuning
- zone-shift threshold tuning
- warning / danger action mapping tuning

### 5.4 Proposal guardrails

Mỗi proposal phải qua guardrail:

- minimum sample size
- minimum confidence
- stability window
- replay / backtest evidence
- impact radius
- risk class
- no hard-safety violation

### 5.5 Proposal classes

- **Class A**: logging / journal only
- **Class B**: paper-mode only suggestion
- **Class C**: needs human approval
- **Class D**: forbidden auto-change

Mapping gợi ý:

- thay đổi threshold dashboard-only -> Class A/B
- thay đổi thesis warning threshold nhỏ -> Class B/C
- thay đổi size / policy / portfolio de-risk -> Class C
- thay đổi hard risk limits / production auto-execution logic -> Class D

### 5.6 Governance fields

Mỗi proposal phải có:

- `proposal_id`
- `created_at`
- `proposal_type`
- `risk_class`
- `evidence_summary`
- `supporting_traces`
- `metrics_impacted`
- `expected_benefit`
- `risk_assessment`
- `approval_status`
- `approved_by`
- `rejected_by`
- `applied_config_version`
- `rollback_reference`

### 5.7 Thesis-aware proposal examples

Ví dụ proposal hợp lệ:

- “Tăng ngưỡng `no_follow_through` sensitivity cho breakout trong `high_momentum` khi `upper_wick_rejection_rate` > X và replay cho thấy giảm fast-SL mà không làm giảm profit factor quá mức.”
- “Bật reduce 30% ở `DANGER` thay vì chỉ tighten SL cho `mean_reversion_bounce` khi `adverse_expansion_against_trade` xuất hiện trong 2 bars đầu.”
- “Cấm scale-in với thesis `breakout_continuation` khi `zone_shift_risk_score >= 0.7`.”

---

## 6. Portfolio-wide intelligence design

### 6.1 Portfolio state inference

Suy ra trạng thái danh mục:

- `HEALTHY`
- `STRETCHED`
- `OVEREXPOSED`
- `DEFENSIVE`
- `DE_RISKING`
- `SHOCK_RESPONSE`

**Input**:
- total exposure
- net long / short bias
- sector / cluster exposure
- BTC linkage
- correlation stress
- number of reflex events
- unrealized pnl distribution
- concentration risk
- distribution của thesis states (`WARNING`, `DANGER`, `INVALID`)
- distribution của `zone_shift_risk_score`

**Output**:
- `portfolio_state`
- `portfolio_stress_score`
- `portfolio_reason_codes`

### 6.2 Correlation / cluster risk model

Không cần quá phức tạp nhưng phải đủ dùng:

- nhóm token theo BTC-beta / sector / behavior cluster / recent co-move
- phát hiện khi đang overstack cùng một cluster
- cấm mở thêm nếu cluster risk quá cao
- tăng stress nếu nhiều trade trong cùng cluster đồng thời vào `WARNING/DANGER`

### 6.3 Portfolio policy coordination

Portfolio brain phải có quyền:

- giảm size toàn cục
- block entry vào cluster
- chuyển mode toàn danh mục sang `CAPITAL_PRESERVATION` / `EXIT_ONLY`
- kích hoạt de-risk khi nhiều context-break cùng lúc
- override cho phép / không cho phép scale-in ở cấp cluster / portfolio

### 6.4 Portfolio reflex

Thiết kế reflex ở cấp danh mục:

- nhiều symbol cùng shock
- BTC break kéo alt invalidation
- drawdown tăng nhanh
- exposure tập trung sai hướng
- correlation spike
- nhiều trades cùng có `zone_shift_risk_level = critical`

**Output**:
- `portfolio_reflex_action`
- `affected_symbols`
- `urgency`
- `reason_codes`

### 6.5 Portfolio-level metrics

Ít nhất gồm:

- `portfolio_stress_score`
- `correlation_stress_score`
- `concentration_score`
- `defensive_mode_frequency`
- `de_risk_effectiveness`
- `cluster_block_saves`
- `portfolio_damage_prevented`
- `portfolio_zone_shift_pressure`
- `portfolio_thesis_instability_rate`

### 6.6 Liên kết portfolio brain với thesis-aware layer

Đây là điểm bắt buộc của bản merged.

Portfolio brain không chỉ nhìn exposure / PnL. Nó phải nhìn cả:

- tỷ lệ open trades đang ở `WARNING`
- tỷ lệ open trades đang ở `DANGER`
- số invalidation mới xảy ra trong X phút
- cluster nào đang có `zone_shift_risk_score` tăng đồng loạt

Ví dụ:

- nếu 4 alt cùng cluster đồng thời chuyển `WARNING -> DANGER`, portfolio brain có thể block entry cluster đó ngay cả khi chưa có lệnh nào chạm SL.
- nếu BTC break quan trọng và nhiều breakout long mất follow-through cùng lúc, kích hoạt `SHOCK_RESPONSE` ở cấp danh mục.

---

## 7. DB schema + persistence plan

### 7.1 Evaluation tables

#### `decision_evaluations`

Key columns:

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `market_decision_trace_id`
- `trade_id`
- `position_id`
- `symbol`
- `evaluation_phase` (`immediate`, `delayed`, `eod`)
- `decision_quality_score`
- `follow_plan_score`
- `edge_realization_score`
- `thesis_management_quality_score`
- `zone_shift_detection_score`
- `damage_prevented_score`
- `missed_opportunity_cost`
- `false_alarm_cost`
- `payload_json`
- `created_at`

Indexes:

- `decision_trace_id`
- `market_decision_trace_id`
- `trade_id`
- `(symbol, created_at)`

#### `change_point_evaluations`

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `symbol`
- `cp_event_id`
- `timeliness_score`
- `change_point_quality_score`
- `false_alarm_flag`
- `payload_json`
- `created_at`

#### `policy_evaluations`

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `policy_event_id`
- `previous_mode`
- `new_mode`
- `policy_choice_quality_score`
- `over_defensive_score`
- `over_aggressive_score`
- `payload_json`
- `created_at`

#### `reflex_evaluations`

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `reflex_event_id`
- `trade_id`
- `reflex_timing_quality_score`
- `damage_prevented_score`
- `false_alarm_cost`
- `missed_opportunity_cost`
- `payload_json`
- `created_at`

### 7.2 Thesis-specific tables / fields

#### Extend `trades` hoặc `positions`

Thêm field tối thiểu:

- `thesis_type`
- `thesis_version`
- `thesis_metadata_json`
- `thesis_state`
- `thesis_last_score`
- `thesis_last_reason`
- `thesis_warning_count`
- `thesis_danger_count`
- `zone_shift_risk_score`
- `zone_shift_risk_level`

#### `thesis_state_events`

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `trade_id`
- `position_id`
- `symbol`
- `previous_state`
- `new_state`
- `thesis_score`
- `zone_shift_risk_score`
- `zone_shift_risk_level`
- `reason_codes_json`
- `snapshot_json`
- `created_at`

#### `thesis_action_events`

- `id`
- `brain_cycle_id`
- `decision_trace_id`
- `trade_id`
- `action_type` (`hold`, `reduce`, `tighten_sl`, `extend_hold`, `force_close`, `block_scale_in`)
- `action_strength`
- `reason_codes_json`
- `source` (`thesis_monitor`, `merged_with_policy`, `portfolio_override`)
- `created_at`

### 7.3 Learning / proposal tables

- `learning_artifacts`
- `brain_proposals`
- `proposal_evidence_links`
- `proposal_reviews`
- `applied_config_versions`
- `rollback_events`

### 7.4 Portfolio tables

- `portfolio_state_events`
- `portfolio_reflex_events`
- `cluster_exposure_events`

### 7.5 JSON payload allowed

Cho phép JSON payload với:

- metrics snapshot
- recent bar summary
- thesis-specific signals
- cluster membership snapshot
- replay evidence summary
- approval notes

---

## 8. API / dashboard expansion

### 8.1 API

Endpoint tối thiểu:

- `/brain/v4/evaluations/latest`
- `/brain/v4/proposals`
- `/brain/v4/proposals/{id}`
- `/brain/v4/portfolio/latest`
- `/brain/v4/portfolio/history`
- `/brain/v4/clusters`
- `/brain/v4/config/versions`
- `/brain/v4/rollback/history`
- `/brain/v4/thesis/open`
- `/brain/v4/thesis/history`
- `/brain/v4/thesis/trade/{id}`

### 8.2 Dashboard sections

Bắt buộc có:

- Decision Quality
- Reflex Quality
- Proposal Review Queue
- Portfolio Stress
- Cluster Risk
- Config Versions / Rollback
- Damage Prevented vs Missed Opportunity
- Thesis Health of Open Trades
- Zone Shift Radar

### 8.3 Mỗi section phải có

#### Decision Quality
- source: `decision_evaluations`
- cards: avg decision quality, thesis quality, false alarm cost
- filters: date, symbol, strategy, regime, thesis type
- drilldown: evaluation -> trade -> trace bundle

#### Reflex Quality
- source: `reflex_evaluations`
- cards: damage prevented, premature exit cost, invalidation response latency
- drilldown: reflex event -> before/after path

#### Thesis Health of Open Trades
- source: `trades`, `thesis_state_events`
- cards: NORMAL / WARNING / DANGER / INVALID counts
- table: symbol, strategy, thesis type, current thesis state, zone shift risk, last reasons
- action trace: last thesis action, last portfolio override

#### Zone Shift Radar
- source: `thesis_state_events`, `portfolio_state_events`, `cluster_exposure_events`
- cards: symbols with elevated/high/critical zone-shift risk
- cluster heatmap: cluster pressure
- drilldown: symbol -> recent bars summary -> reason codes -> affected trades

---

## 9. Replay / simulation extension

### 9.1 Replay use cases

- đánh giá detector quá nhạy / quá chậm
- so sánh policy mode A vs B
- so sánh before/after proposal
- đo damage prevented
- đo false alarm cost
- paper-only portfolio reflex simulation
- so sánh thesis profile cũ vs mới
- kiểm tra zone-shift thresholds có giảm fast-SL không

### 9.2 Side-by-side comparison

Phải hỗ trợ:

- old config vs new config
- old detector baseline vs tuned baseline
- old portfolio policy vs new portfolio policy
- old thesis profile vs tuned thesis profile

### 9.3 Acceptance for proposal

Proposal chỉ đủ bằng chứng nếu replay / simulation chứng minh:

- không phá hard safety
- cải thiện metric mục tiêu
- không làm xấu metric phụ quá mức
- ổn định trong nhiều sample
- không tăng `invalid_but_waited_for_sl` hoặc `warning_not_acted`

### 9.4 Thesis-aware replay metrics

Bắt buộc đo thêm:

- `warning_to_danger_transition_rate`
- `danger_to_invalid_rate`
- `avg_invalidation_response_latency_bars`
- `fast_sl_rate_after_warning`
- `zone_shift_early_detection_rate`
- `thesis_preserved_profit_delta`

---

## 10. Integration plan in current repo

### 10.1 Module mới đề xuất

- `core/brain/evaluation.py`
- `core/brain/learning_artifacts.py`
- `core/brain/proposals.py`
- `core/brain/approval.py`
- `core/brain/versioning.py`
- `core/brain/portfolio_state.py`
- `core/brain/portfolio_reflex.py`
- `core/brain/portfolio_clusters.py`
- `core/profit/thesis_profiles.py`
- `core/profit/thesis_monitor.py`
- `core/profit/thesis_actions.py`
- `core/profit/thesis_metrics.py`

### 10.2 Existing modules cần sửa

Phải chỉ rõ chèn vào:

- `SimulationCycle.run`
- `review_positions_and_act`
- journal / outcome persistence
- `replay.py`
- `apps/api/server.py`
- `apps/dashboard/app.py`
- worker runner / daily jobs / reflection jobs

### 10.3 Integration timing

#### Mỗi tick
- update portfolio state
- evaluate open-trade thesis state
- apply thesis actions
- merge với symbol reflex / policy / portfolio override

#### Khi mở lệnh
- attach thesis metadata
- persist thesis profile version

#### Khi có state transition hoặc action
- persist `thesis_state_events`, `thesis_action_events`

#### Khi đóng lệnh
- compute delayed evaluation
- build learning artifact

#### Cuối ngày / batch reflection
- aggregate learning artifacts
- create proposals
- update dashboard summaries

### 10.4 Merge order trong `review_positions_and_act(...)`

Thứ tự khuyến nghị:

1. load trade context
2. build recent klines snapshot
3. evaluate thesis state
4. evaluate proactive / SL/TP / existing review rules
5. evaluate symbol reflex
6. evaluate portfolio override
7. merge actions with precedence rules
8. choose final action
9. persist traces / thesis events / evaluations

### 10.5 Precedence rules

Đề xuất:

- `INVALID` thesis có thể force close trừ khi hard exchange constraint ngăn cản
- `portfolio_reflex EXIT_ONLY` override mọi thesis `NORMAL`
- `DANGER` + portfolio `DEFENSIVE` => ưu tiên reduce / close
- `WARNING` không tự close nếu thesis profile cho phép thở, trừ khi zone-shift risk lên `critical`

---

## 11. Test plan

### 11.1 Evaluation tests

- score được tính đúng
- immediate vs delayed evaluation đúng
- linkage với outcome đúng
- thesis quality / zone-shift score được persist đúng

### 11.2 Proposal tests

- proposal không sinh khi sample thiếu
- proposal sinh đúng khi đủ evidence
- class / risk đúng
- approval path đúng
- rollback path đúng

### 11.3 Portfolio tests

- overexposure được phát hiện đúng
- cluster block hoạt động đúng
- portfolio reflex kích hoạt đúng khi shock
- market-wide de-risk không đánh nhau với symbol policy
- cluster danger escalation hoạt động đúng

### 11.4 Replay / simulation tests

- same input same evaluation
- compare config A/B đúng
- proposal evidence reproducible
- thesis profile replay reproducible
- zone-shift detection deterministic trên cùng dữ liệu

### 11.5 API / dashboard tests

- endpoint trả đủ field
- review queue hiển thị đúng
- config version history đúng
- portfolio history đúng
- thesis health panel đúng
- zone shift radar đúng

### 11.6 Strategy-thesis fit tests

- breakout được quản lý theo breakout profile
- mean reversion được quản lý theo mean reversion profile
- liquidity sweep reversal được quản lý theo reversal profile
- generic rules không được ghi đè sai thesis profile nếu không có risk override

### 11.7 Danger / invalidation handling tests

- `WARNING` không bị overreact quá sớm
- `DANGER` có hành động giảm rủi ro đúng
- `INVALID` không bị chờ tới SL nếu rule cho phép close sớm
- invalidation response latency được log đúng

---

## 12. Acceptance checklist

P2 merged chỉ được coi là DONE khi:

- hệ thống không chỉ log event mà chấm được quality của state / policy / reflex / outcome
- có learning artifact link đầy đủ tới trace + outcome
- proposal engine có guardrail rõ và không tự apply bừa
- có approval + versioning + rollback
- có portfolio state inference + cluster risk + portfolio reflex
- dashboard nhìn thấy được chất lượng quyết định, proposal queue, stress danh mục
- replay phục vụ được approval và regression
- mỗi trade có thesis metadata và thesis state machine
- hệ thống nhận diện được dấu hiệu vùng chiến lược đang đổi hoặc có xác suất cao sắp đổi
- có `zone_shift_risk_score` và action mapping theo cấp độ nguy hiểm
- cách quản lý lệnh tương đồng với chiến lược và bối cảnh token tại thời điểm đó
- có test cho evaluation, proposal, portfolio, replay, API, thesis-aware management

---

## Output format cho Cursor

Khi triển khai từ tài liệu này, output spec / implementation plan phải giữ format sau:

1. Executive summary
2. Gap from P1
3. P2 architecture overview
4. Evaluation layer design
5. Learning artifacts + proposal governance
6. Portfolio-wide intelligence design
7. DB schema + persistence plan
8. API / dashboard expansion
9. Replay / simulation extension
10. Integration plan in current repo
11. Test plan
12. Acceptance checklist

Viết như spec để dev implement ngay.
Không lan man.
Không thêm strategy mới.
Không cho LLM tự vào lệnh.
Không cho hệ thống tự sửa production config vô điều kiện.
