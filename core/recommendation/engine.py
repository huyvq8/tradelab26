from __future__ import annotations


class RecommendationEngine:
    def next_steps(self, reflection: dict, open_positions: int) -> list[str]:
        recs: list[str] = []
        metrics = reflection.get("metrics") or {}
        strategy_accuracy = metrics.get("strategy_accuracy") or {}
        win_rate = metrics.get("win_rate", 0.0)
        avg_r = metrics.get("avg_r_multiple", 0.0)
        profit_factor = metrics.get("profit_factor", 0.0)

        # Rule-based from reflection
        if reflection.get("realized_pnl", 0) < 0:
            recs.append(
                "Reduce next-session risk by 25% until two profitable sessions are logged."
            )
        if reflection.get("top_pattern") == "breakout_momentum":
            recs.append(
                "Keep momentum strategies enabled, but require stronger volume confirmation."
            )
        if open_positions >= 3:
            recs.append(
                "Do not add fresh positions until at least one current position is closed."
            )

        # Data-driven: strategy with low win rate but positive expectancy
        for strategy, data in strategy_accuracy.items():
            if not isinstance(data, dict):
                continue
            s_wr = data.get("win_rate", 0)
            s_trades = data.get("trades", 0)
            if s_trades >= 5 and s_wr < 0.4 and profit_factor > 1.0:
                recs.append(
                    f"Strategy '{strategy}' has low win rate ({s_wr:.0%}) but positive PF—keep position size small and respect SL."
                )
            if s_trades >= 5 and s_wr >= 0.6:
                recs.append(
                    f"Strategy '{strategy}' is performing well ({s_wr:.0%} win rate)—continue current rules, avoid overtrading."
                )

        # R-multiple and Sharpe (learning signals, no parameter change)
        if avg_r and avg_r < 0:
            recs.append(
                "Average R-multiple is negative; focus on cutting losers quickly and let winners run."
            )
        if metrics.get("sharpe_simulated", 0) < 0 and metrics.get("total_trades", 0) >= 10:
            recs.append(
                "Simulated Sharpe is negative with enough samples; review entry timing and regime filter."
            )

        # AI-generated plan if available (append, do not replace rules)
        ai_plan = reflection.get("ai_next_day_plan")
        if ai_plan and isinstance(ai_plan, str):
            for line in ai_plan.strip().split("\n"):
                line = line.strip().lstrip("-•* ")
                if line and len(line) > 10:
                    recs.append(line)

        if not recs:
            recs.append(
                "Maintain current risk policy and continue collecting more samples before changing parameters."
            )
        return recs
