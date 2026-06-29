"""Pipeline-health watchdog task.

Runs hourly, evaluates the heartbeats of the scheduled tasks and sends a
single consolidated Telegram alert when something is stale or failing.
Re-alerts are throttled per task via ``heartbeat_realert_hours``.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import update

from app.config import settings
from app.models.base import async_session
from app.models.heartbeat import TaskHeartbeat
from app.notifications.telegram_bot import send_pipeline_health_alert
from app.services.heartbeat import evaluate_health, filter_unthrottled, load_heartbeats
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    """Run async code inside sync Celery tasks."""
    return asyncio.run(coro)


@celery_app.task(name="app.tasks.health_check.check_pipeline_health")
def check_pipeline_health() -> dict:
    """Beat-scheduled watchdog over the data pipeline."""
    return _run_async(_check_async())


async def _check_async() -> dict:
    if not settings.heartbeat_enabled:
        return {"enabled": False}

    now = datetime.now(UTC)
    realert_cutoff = now - timedelta(hours=settings.heartbeat_realert_hours)

    # Phase 1: read state and decide who to alert (no writes, no network I/O).
    async with async_session() as session:
        heartbeats = await load_heartbeats(session)
    report = evaluate_health(heartbeats, now)

    if report.healthy:
        logger.info("health.pipeline_ok", monitored=len(report.tasks))
        return {"healthy": True, "monitored": len(report.tasks), "problems": 0}

    by_name = {hb.task_name: hb for hb in heartbeats}
    to_alert = filter_unthrottled(report.problems, by_name, realert_cutoff)

    logger.warning(
        "health.pipeline_degraded",
        problems=[p.task_name for p in report.problems],
        alerting=[p.task_name for p in to_alert],
    )

    # Phase 2: send the notification (network I/O, no DB session held).
    sent = False
    if to_alert:
        payload = [
            {
                "task_name": p.task_name,
                "status": p.status,
                "age_hours": round(p.age_seconds / 3600, 1) if p.age_seconds is not None else None,
                "detail": p.detail,
            }
            for p in to_alert
        ]
        sent = await send_pipeline_health_alert(payload)

    # Phase 3: burn the re-alert window ONLY for tasks we actually notified about.
    # A failed/no-op send leaves last_alerted_at untouched so the next run retries.
    if sent and to_alert:
        alerted_names = [p.task_name for p in to_alert]
        async with async_session() as session:
            await session.execute(
                update(TaskHeartbeat).where(TaskHeartbeat.task_name.in_(alerted_names)).values(last_alerted_at=now)
            )
            await session.commit()

    return {
        "healthy": False,
        "monitored": len(report.tasks),
        "problems": len(report.problems),
        "alerted": len(to_alert) if sent else 0,
        "notification_sent": sent,
    }
