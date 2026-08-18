"""Pipeline-health watchdog task.

Runs hourly, evaluates the heartbeats of the scheduled tasks and sends a
single consolidated Telegram alert when something is stale or failing.
Re-alerts are throttled per task via ``heartbeat_realert_hours``.
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.models import PriceRecord, WatchlistItem
from app.models.base import async_session
from app.models.heartbeat import TaskHeartbeat
from app.notifications.telegram_bot import send_pipeline_health_alert
from app.services.heartbeat import (
    evaluate_data_freshness,
    evaluate_health,
    filter_unthrottled,
    load_heartbeats,
)
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


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
        latest_price_at = (await session.execute(select(func.max(PriceRecord.scraped_at)))).scalar_one_or_none()
        active_watchlist = (
            await session.execute(
                select(func.count()).select_from(WatchlistItem).where(WatchlistItem.is_active)
            )
        ).scalar_one()
    report = evaluate_health(heartbeats, now)

    # Nutzarbeit on top of task success: a green pipeline that stops writing
    # price data is exactly the failure mode heartbeats alone cannot see.
    problems = list(report.problems)
    freshness_problem = evaluate_data_freshness(latest_price_at, active_watchlist, now)
    if freshness_problem is not None:
        problems.append(freshness_problem)

    if not problems:
        logger.info("health.pipeline_ok", monitored=len(report.tasks))
        return {"healthy": True, "monitored": len(report.tasks), "problems": 0}

    by_name = {hb.task_name: hb for hb in heartbeats}
    to_alert = filter_unthrottled(problems, by_name, realert_cutoff)

    logger.warning(
        "health.pipeline_degraded",
        problems=[p.task_name for p in problems],
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
        # Upsert instead of update: synthetic problems (data freshness) have no
        # heartbeat row yet — they get one here so re-alert throttling applies.
        alerted_names = [p.task_name for p in to_alert]
        async with async_session() as session:
            stmt = pg_insert(TaskHeartbeat).values(
                [{"task_name": name, "last_alerted_at": now} for name in alerted_names]
            )
            stmt = stmt.on_conflict_do_update(index_elements=["task_name"], set_={"last_alerted_at": now})
            await session.execute(stmt)
            await session.commit()

    return {
        "healthy": False,
        "monitored": len(report.tasks),
        "problems": len(problems),
        "alerted": len(to_alert) if sent else 0,
        "notification_sent": sent,
    }
