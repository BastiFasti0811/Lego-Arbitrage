"""Task heartbeat model — records last run/success of Celery tasks.

Used by the pipeline-health watchdog so a silently failing scraper or
analysis task surfaces as an alert instead of going unnoticed.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskHeartbeat(Base):
    __tablename__ = "task_heartbeats"

    task_name: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(20), server_default="unknown", default="unknown", nullable=False)
    last_detail: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<TaskHeartbeat {self.task_name} status={self.last_status}>"
