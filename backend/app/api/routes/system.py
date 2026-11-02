"""System / pipeline-health endpoints for the dashboard."""

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.services.pipeline_health import collect_pipeline_state

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


class SourceStatusResponse(BaseModel):
    """Eine Angebotsquelle: wann zuletzt gescannt, wann zuletzt etwas gefunden."""

    platform: str
    label: str
    # ok | no_yield (gescannt, nichts gesehen) | not_scanned | idle | not_configured
    status: str
    last_scan_at: datetime | None
    last_offer_seen_at: datetime | None
    last_offer_new_at: datetime | None
    offers_seen_24h: int | None
    offers_new_24h: int | None
    detail: str


class HealthStatusResponse(BaseModel):
    healthy: bool
    checked_at: datetime
    tasks: list[TaskHealthResponse]
    sources: list[SourceStatusResponse] = []


@router.get("/status", response_model=HealthStatusResponse)
async def pipeline_status(session: AsyncSession = Depends(get_session)) -> HealthStatusResponse:
    """Pipeline health: every monitored task, plus what the hourly watchdog alerts on.

    ``tasks`` lists each monitored task (last run/success vs. expected cadence)
    followed by the synthetic Nutzarbeit problems (``pipeline.*``: price data,
    valuation coverage, offers per source, Catawiki), so the dashboard shows
    what Telegram would say. ``sources`` has, per offer platform, the last
    successful scan and the last offer seen. ``healthy`` is false as soon as
    either list carries a problem.
    """
    state = await collect_pipeline_state(session, datetime.now(UTC))
    monitored = {task.task_name for task in state.report.tasks}
    synthetic = [problem for problem in state.problems if problem.task_name not in monitored]
    return HealthStatusResponse(
        healthy=not state.problems,
        checked_at=state.checked_at,
        tasks=[TaskHealthResponse(**asdict(task)) for task in [*state.report.tasks, *synthetic]],
        sources=[
            SourceStatusResponse(
                platform=source.platform,
                label=source.label,
                status=source.status,
                last_scan_at=source.last_scan_at,
                last_offer_seen_at=source.last_offer_seen_at,
                last_offer_new_at=source.last_offer_new_at,
                offers_seen_24h=source.offers_seen_24h,
                offers_new_24h=source.offers_new_24h,
                detail=source.detail,
            )
            for source in state.sources
        ],
    )
