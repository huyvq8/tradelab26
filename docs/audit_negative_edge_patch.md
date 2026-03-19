# Audit: Negative edge (Trading Lab Pro) + patch summary

**Context (observed):** Win rate ~32.6%, PF ~0.92, expectancy negative; `trend_following` / `breakout_momentum` weak on SIREN long; many SL &lt; 5 min; avg win ~1.17% vs TP plan ~10%; short hold times.

---

## 1) Signal generation

| Issue | Observed evidence | Likely code | Root cause | Fix (shipped) | How to test |
|-------|-------------------|-------------|------------|---------------|-------------|
| Signals are **regime + %24h only** — no structure | High trade count, poor PF on momentum names | `core/strategies/implementations.py` | No candle/volatility context; static SL/TP % | **ATR + cap** on SL/TP after signal (`signal_level_adjust` + `config/entry_timing.v1.json`) | Unit `test_signal_level_adjust_caps_tp`; inspect `signals_fired` SL/TP distances |
| **Breakout** fires on extended daily move | SL hit in minutes | Same + `BreakoutMomentumStrategy` | Entry = “already moved” | **Entry timing**: extended 1h bar + chase-top filter for `trend_following` / `breakout_momentum` | `evaluate_entry_timing` + logs `ENTRY_EXTENDED_*` / `ENTRY_CHASE_TOP` |
| No **symbol+strategy** edge filter | Combo SIREN + breakout 11% WR | Only global strategy weights | Weights aggregated by strategy name, not symbol | **`combo_performance`** in `profit.active.json` + `compute_combo_multipliers` | DB with closed trades; dashboard weights; block forces `COMBO_BLOCKED_EDGE` |

---

## 2) Entry timing

| Issue | Evidence | Code | Root cause | Fix | Test |
|-------|----------|------|------------|-----|------|
| Immediate entry on extended candle | Whipsaw / fast SL | Cycle used raw `strategy.evaluate` | No pullback discipline | `core/signals/entry_timing.py` + pipeline in `cycle.py` | Reject codes in `decision_log.jsonl` |
| No cooldown → spam entries | Many trades, short holds | N/A | Missing per-symbol throttle | `storage/entry_cooldown.json` via `record_entry_opened` | `test_entry_timing_cooldown_reject` |
| Reject reasons opaque | Hard to tune | `rejected_signals` strings only | Unstructured | `reason_code` + `meta` on rejects; `log_decision()` | `test_log_rejected_has_reason_code` |

---

## 3) Risk engine

| Issue | Evidence | Code | Root cause | Fix | Test |
|-------|----------|------|------------|-----|------|
| Size OK but **R defined on tight SL** then TP unreachable | Short holds, small wins | `core/risk/engine.py` | Risk math correct; SL/TP from strategy were inconsistent with reality | **Tighter TP cap + ATR SL** improves R vs hold time | Backtest / forward: avg hold vs TP distance |
| No combo throttle | Bad combos keep trading | `RiskEngine.assess` | Out of scope for risk-only | **Combo multiplier** applied **before** open (size × mult; 0 = block) | `get_combo_multiplier` tests |

---

## 4) Exit engine

| Issue | Evidence | Code | Root cause | Fix | Test |
|-------|----------|------|------------|-----|------|
| TP1/TP2 too far vs realized ~1% | PF &lt; 1 | `proactive_exit` + static strategy TP | Mismatch plan vs behavior | Lower **tp1/tp2** defaults; **partial at 1R** (`partial_tp_1r`) | Tune `profit.active.json`; watch `PARTIAL_TP` actions in logs |
| SL moved to breakeven too early? | Stopped out fast | `proactive_exit_engine` `MOVE_SL` | Lock 1R as soon as in profit-protection | **`min_hold_minutes_before_move_sl`** (default 20) | Compare SL update timestamps vs `opened_at` |
| Profit protection too easy / too hard | — | `activation_r` / `roi` | Thresholds | Raised **activation_r** to 2.0; ROI 35% | Monitor `in_profit_protection_mode` in explanations |

---

## 5) Learning / feedback loop

| Issue | Evidence | Code | Root cause | Fix | Test |
|-------|----------|------|------------|-----|------|
| Weights don’t see **per-symbol** toxicity | SIREN long dominates losses | `strategy_weight_engine` (strategy-only) | Missing dimensions | **Combo multipliers** from closed trades | SQL: group `strategy_name`, `symbol` |
| No machine-readable audit trail | Hard to regress | Worker + JSON log | Partial logging | **`data/decision_log.jsonl`** (`log_decision`) | Grep `entry_rejected` / `entry_opened` |

---

## Patch map (files)

| Area | Files |
|------|--------|
| Entry timing + ATR caps | `config/entry_timing.v1.json`, `core/signals/entry_timing.py`, `core/profit/signal_level_adjust.py`, `core/orchestration/cycle.py` |
| Combo / allocation | `config/profit.active.json` (`combo_performance`), `core/profit/strategy_weight_engine.py` |
| Exit | `config/profit.active.json` (`proactive_exit`), `core/profit/proactive_exit_engine.py` |
| Observability | `core/rejected_signals_log.py`, `core/observability/decision_log.py`, `apps/worker/runner.py` |
| Tests | `tests/test_entry_edge_pipeline.py` |
| Docs | This file |

---

## Before / after — how to compare (Definition of Done)

1. **SL &lt; 5 min:** SQL on `trades` + `positions.opened_at` vs close time (or journal). Expect fewer after cooldown + extended-candle filter.
2. **Spam / negative combo:** Count opens where `reason_code` would have been `COMBO_BLOCKED_EDGE` (dry-run: temporarily set `block_pf_below` high).
3. **TP vs hold:** Distribution of `(tp-entry)/entry` on new opens should cluster near **`max_tp_pct`** (default 4%) and ATR, not 10%+.
4. **Regression:** Run worker + dashboard smoke; Binance sync path untouched (only cycle filters + SL/TP values).
5. **Artifacts:** `data/decision_log.jsonl`, `storage/entry_cooldown.json`, extended `blocked_signals.json` rows with `reason_code`.

---

## Config knobs (quick reference)

- **Entry:** `config/entry_timing.v1.json` — `extended_candle`, `pullback`, `cooldown.seconds_between_entries_per_symbol`, `signal_levels.max_tp_pct`.
- **Combo:** `profit.active.json` → `combo_performance.*`.
- **Exit:** `profit.active.json` → `proactive_exit.*` (`partial_1r_*`, `min_hold_minutes_before_move_sl`, `tp1_pct_from_entry`, `profit_protection_activation_r`).

---

*Generated as part of systematic negative-edge remediation; tune thresholds on paper before live.*

---

## Phase 2 (verify + refactor + experiments)

- **Measurement:** `scripts/validate_edge_patch_report.py` → `reports/edge_validation_*.md` + JSON; `--record-experiment` ghi `storage/experiments/results_*.jsonl`.
- **Acceptance:** `docs/negative_edge_acceptance.md`
- **A/B configs:** `config/experiments/README.md`, `ENTRY_TIMING_CONFIG`, `PROFIT_ACTIVE_OVERLAY`, `EDGE_EXPERIMENT`
- **Signal architecture:** `core/strategies/signal_structure.py` + `implementations.py` (structural levels + `levels_from_structure`); cycle bỏ post-adjust ATR khi flag bật.
