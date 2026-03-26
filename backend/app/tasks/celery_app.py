"""Celery application configuration and task scheduling."""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "lego_arbitrage",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.scrape_daily",
        "app.tasks.analyze_new",
        "app.tasks.update_inventory",
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
    # Weekly model retraining (Sunday 03:00)
    "weekly-retrain": {
        "task": "app.tasks.analyze_new.retrain_model",
        "schedule": crontab(minute=0, hour=3, day_of_week=0),
        "options": {"queue": "ml"},
    },
    # Update inventory valuations every 6 hours (offset from scraping)
    "update-inventory": {
        "task": "app.tasks.update_inventory.update_inventory_valuations",
        "schedule": crontab(minute=30, hour="*/6"),
        "options": {"queue": "analysis"},
    },
}
