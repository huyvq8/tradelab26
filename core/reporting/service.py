from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.portfolio.models import Trade, Position
from core.reporting.models import DailyReport
from core.reflection.engine import ReflectionEngine
from core.recommendation.engine import RecommendationEngine


class DailyReportService:
    def generate(self, db: Session, target_date: date, portfolio_id: int | None = None) -> DailyReport:
        reflection = ReflectionEngine().build_daily_reflection(db, target_date, portfolio_id=portfolio_id)
        open_positions = len(
            list(db.scalars(select(Position).where(Position.is_open == True)))
        )
        recs = RecommendationEngine().next_steps(reflection, open_positions)
        trades = list(db.scalars(select(Trade)))
        realized = round(sum(t.pnl_usd for t in trades if t.action == "close"), 2)
        headline = f"Daily review for {target_date}: realized PnL {realized} USD"
        summary = f"""# Daily Summary\n\n- Date: {target_date}\n- Realized PnL: {realized} USD\n- Journal entries: {reflection['journal_count']}\n- Most active strategy: {reflection['top_pattern']}\n"""
        if reflection.get("ai_summary"):
            summary += "\n\n## AI Reflection\n\n" + reflection["ai_summary"] + "\n"
        if reflection.get("ai_next_day_plan"):
            summary += "\n\n## Next day plan (AI)\n\n" + reflection["ai_next_day_plan"] + "\n"
        recommendations = "# Recommendations\n\n" + "\n".join(f"- {r}" for r in recs)
        report = db.scalar(
            select(DailyReport).where(DailyReport.report_date == target_date)
        )
        if report is None:
            report = DailyReport(
                report_date=target_date,
                headline=headline,
                summary_markdown=summary,
                recommendations_markdown=recommendations,
            )
            db.add(report)
        else:
            report.headline = headline
            report.summary_markdown = summary
            report.recommendations_markdown = recommendations
        db.flush()
        return report
