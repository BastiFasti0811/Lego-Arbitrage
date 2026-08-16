"""Celery application configuration and task scheduling."""

import asyncio

import structlog
from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_success

from app.config import settings

logger = structlog.get_logger()

celery_app = Celery(
    "lego_arbitrage",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.scrape_daily",
        "app.tasks.analyze_new",
        "app.tasks.auction_watch",
        "app.tasks.catawiki_scan",
        "app.tasks.update_inventory",
        "app.tasks.health_check",
        "app.tasks.weekly_report",
    ],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Berlin",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,  # 10 minutes max per task
    task_soft_time_limit=540,  # Soft limit at 9 minutes
    worker_max_tasks_per_child=50,  # Restart worker after 50 tasks (memory)
    worker_prefetch_multiplier=1,  # One task at a time per worker
)

# ── Scheduled Tasks (Celery Beat) ────────────────────────
celery_app.conf.beat_schedule = {
    # Full scrape every 6 hours
    "scrape-all-sets": {
        "task": "app.tasks.scrape_daily.scrape_all_watched_sets",
        "schedule": crontab(minute=0, hour="*/6"),
        "options": {"queue": "scraping"},
    },
    # Kleinanzeigen-only offer lane over the watchlist (2h cadence, aborts on block)
    "scrape-kleinanzeigen-watched": {
        "task": "app.tasks.scrape_daily.scrape_kleinanzeigen_watched",
        "schedule": crontab(minute=10, hour="*/2"),
        "options": {"queue": "scraping"},
    },
    # Analyze new offers every 30 minutes
    "analyze-new-offers": {
        "task": "app.tasks.analyze_new.analyze_new_offers",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "analysis"},
    },
    # Daily summary at 20:00 Berlin time
    "daily-summary": {
        "task": "app.tasks.analyze_new.send_daily_summary_task",
        "schedule": crontab(minute=0, hour=20),
        "options": {"queue": "notifications"},
    },
    # Refresh UVP/EOL metadata once per day from LEGO.com + BrickMerge
    "refresh-known-set-metadata": {
        "task": "app.tasks.scrape_daily.refresh_known_set_metadata",
        "schedule": crontab(minute=15, hour=4),
        "options": {"queue": "scraping"},
    },
    # Re-evaluate watched auction lots every morning
    "refresh-auction-watchlist": {
        "task": "app.tasks.auction_watch.refresh_auction_watchlist",
        "schedule": crontab(minute=20, hour=8),
        "options": {"queue": "analysis"},
    },
    # Scan configured auction-source categories once per day
    "scan-catawiki-categories": {
        "task": "app.tasks.catawiki_scan.scan_configured_categories",
        "schedule": crontab(minute=40, hour=8),
        "options": {"queue": "scraping"},
    },
    # Weekly Telegram report — dead-man switch for the whole pipeline (Sunday 18:00)
    "weekly-report": {
        "task": "app.tasks.weekly_report.send_weekly_report_task",
        "schedule": crontab(minute=0, hour=18, day_of_week=0),
        "options": {"queue": "notifications"},
    },
    # Update inventory valuations every 6 hours (offset from scraping)
    "update-inventory": {
        "task": "app.tasks.update_inventory.update_inventory_valuations",
        "schedule": crontab(minute=30, hour="*/6"),
        "options": {"queue": "analysis"},
    },
    # Pipeline-health watchdog every hour (alerts on stale/failing tasks)
    "pipeline-health-check": {
        "task": "app.tasks.health_check.check_pipeline_health",
        "schedule": crontab(minute=15),
        "options": {"queue": "analysis"},
    },
}


# ── Task Heartbeat Signals ───────────────────────────────
# Automatically record every task's run/success so the pipeline-health
# watchdog can detect silent failures. Importing the heartbeat service here
# would create no cycle (it never imports celery_app), but we import lazily
# inside the handler to keep worker startup order robust.
def _record(sender, *, success: bool, detail: object | None) -> None:
    try:
        name = getattr(sender, "name", None)
        if not name:
            return
        from app.services.heartbeat import record_heartbeat

        asyncio.run(record_heartbeat(name, success=success, detail=detail))
    except Exception as exc:  # noqa: BLE001 — never let heartbeat tracking break a task
        logger.warning("heartbeat.signal_failed", error=str(exc))


@task_success.connect
def _on_task_success(sender=None, result=None, **kwargs) -> None:
    _record(sender, success=True, detail=result)


@task_failure.connect
def _on_task_failure(sender=None, exception=None, **kwargs) -> None:
    _record(sender, success=False, detail=exception)
