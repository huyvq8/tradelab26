from datetime import datetime, date
from sqlalchemy import Integer, String, DateTime, Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, index=True, unique=True)
    headline: Mapped[str] = mapped_column(String(200))
    summary_markdown: Mapped[str] = mapped_column(Text)
    recommendations_markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
