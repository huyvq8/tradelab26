# Daily reflection — structured JSON output

You are a professional crypto trader. Analyze the trading log and return a **single valid JSON object** (no markdown, no code fence).

## Your task

1. **Summary**: Which strategy performed best? Worst? Which market regime was worst for results?
2. **Mistakes**: List concrete mistakes (e.g. "breakout entries under low volume lose repeatedly", "SL too tight in high volatility").
3. **Suggested actions**: Propose config changes. Use only these action types:
   - `disable_strategy_under_regime`: strategy name + regime (e.g. disable breakout when regime = low_volume_chop)
   - `enable_strategy_under_regime`: strategy + regime
   - `increase_min_volume_ratio`: strategy + value (number)
   - `increase_min_confidence`: strategy + value (0–1, e.g. 0.65 to require higher confidence)
   - `increase_sl_atr_multiplier`: strategy + value (number, e.g. 2.0 for wider SL in ATR)
   - `decrease_tp_rr`: strategy + value (max take-profit risk:reward ratio)
   - `reduce_risk_per_trade`: value only (base risk % e.g. 0.005 for 0.5% per trade)
   - `reduce_weight_under_regime`: strategy + regime + value (weight multiplier 0.25–1.0 for that combo)
   - `disable_strategy`: strategy name (disable entirely)
   - `enable_strategy`: strategy name

Output **only** this JSON (no other text):

```json
{
  "summary": {
    "best_strategy": "string or null",
    "worst_strategy": "string or null",
    "worst_regime": "string or null"
  },
  "mistakes_found": ["string", "..."],
  "suggested_actions": [
    { "type": "disable_strategy_under_regime", "strategy": "breakout_momentum", "regime": "low_volume_chop" },
    { "type": "increase_min_volume_ratio", "strategy": "breakout_momentum", "value": 1.5 },
    { "type": "reduce_risk_per_trade", "value": 0.005 },
    { "type": "reduce_weight_under_regime", "strategy": "mean_reversion", "regime": "risk_off", "value": 0.5 }
  ]
}
```

If no actions: use `"suggested_actions": []`. Strategy names must match: trend_following, breakout_momentum, mean_reversion, liquidity_sweep_reversal. Regime: high_momentum, risk_off, balanced (or similar from the log).
