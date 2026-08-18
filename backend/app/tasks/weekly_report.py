"""Weekly Telegram report — the dead-man switch for the whole pipeline.

Always sends, even when every number is zero: a report full of zeros (or a
missing report) is the signal that the pipeline died silently.
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select

from app.engine.decision_engine import Recommendation
from app.models import Offer, PriceRecord, WatchlistItem
from app.models.base import async_session
from app.notifications.telegram_bot import send_weekly_report
from app.services.heartbeat import evaluate_data_freshness, evaluate_health, load_heartbeats
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.tasks.weekly_report.send_weekly_report_task")
def send_weekly_report_task() -> dict:
    """Beat-scheduled weekly report (Sunday 18:00 Berlin)."""
    return _run_async(_report_async())


async def _report_async() -> dict:
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)

    async with async_session() as session:
        prices_7d = (
            await session.execute(
                select(func.count()).select_from(PriceRecord).where(PriceRecord.scraped_at >= week_ago)
            )
        ).scalar_one()
        offers_7d = (
            await session.execute(
                select(func.count()).select_from(Offer).where(Offer.discovered_at >= week_ago)
            )
        ).scalar_one()
        go_7d = (
            await session.execute(
                select(func.count())
                .select_from(Offer)
                .where(
                    Offer.discovered_at >= week_ago,
                    Offer.recommendation.in_([Recommendation.GO_STAR, Recommendation.GO]),
                )
            )
        ).scalar_one()
        watchlist_active = (
            await session.execute(
                select(func.count()).select_from(WatchlistItem).where(WatchlistItem.is_active)
            )
        ).scalar_one()
        latest_price_at = (await session.execute(select(func.max(PriceRecord.scraped_at)))).scalar_one_or_none()
        heartbeats = await load_heartbeats(session)

    report = evaluate_health(heartbeats, now)
    problems = [p.task_name for p in report.problems]
    freshness = evaluate_data_freshness(latest_price_at, watchlist_active, now)
    if freshness is not None:
        problems.append(freshness.task_name)

    stats = {
        "prices_7d": prices_7d,
        "offers_7d": offers_7d,
        "go_7d": go_7d,
        "watchlist_active": watchlist_active,
        "problems": problems,
    }
    sent = await send_weekly_report(stats)
    logger.info(
        "weekly_report.done",
        sent=sent,
        prices_7d=prices_7d,
        offers_7d=offers_7d,
        go_7d=go_7d,
        watchlist_active=watchlist_active,
        problems=len(problems),
    )
    return {"sent": sent, **stats}
