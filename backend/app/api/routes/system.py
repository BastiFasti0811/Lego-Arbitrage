"""System / pipeline-health endpoints for the dashboard."""

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.services.heartbeat import evaluate_health, load_heartbeats

router = APIRouter()


class TaskHealthResponse(BaseModel):
    task_name: str
    status: str
    last_success_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    age_seconds: float | None
    max_age_seconds: int
    detail: str | None


class HealthStatusResponse(BaseModel):
    healthy: bool
    checked_at: datetime
    tasks: list[TaskHealthResponse]


@router.get("/status", response_model=HealthStatusResponse)
async def pipeline_status(session: AsyncSession = Depends(get_session)) -> HealthStatusResponse:
    """Return per-task pipeline health (last run/success vs. expected cadence)."""
    heartbeats = await load_heartbeats(session)
    report = evaluate_health(heartbeats, datetime.now(UTC))
    return HealthStatusResponse(
        healthy=report.healthy,
        checked_at=report.checked_at,
        tasks=[TaskHealthResponse(**asdict(t)) for t in report.tasks],
    )
