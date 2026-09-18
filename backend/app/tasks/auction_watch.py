"""Re-evaluate watched auction lots on a schedule."""

from datetime import UTC, datetime, timedelta

import structlog
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.models import AuctionWatchItem, LegoSet
from app.models.base import async_session
from app.notifications.telegram_bot import send_auction_watch_alert
from app.services.auction_tracking import refresh_watch_item
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.tasks.auction_watch.refresh_auction_watchlist")
def refresh_auction_watchlist() -> dict:
    return _run_async(_refresh_auction_watchlist_async())


async def _refresh_auction_watchlist_async() -> dict:
    summary = {"checked": 0, "under_limit": 0, "alerts_sent": 0, "errors": 0}

    async with async_session() as session:
        result = await session.execute(
            select(AuctionWatchItem, LegoSet)
            .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
            .where(AuctionWatchItem.is_active, AuctionWatchItem.status != "ENDED")
        )

        for item, lego_set in result.all():
            try:
                can_bid = await refresh_watch_item(item, lego_set)
                summary["checked"] += 1

                if can_bid:
                    summary["under_limit"] += 1
                    should_alert = (
                        item.last_alerted_at is None
                        or item.last_alerted_at < datetime.now(UTC) - timedelta(hours=20)
                    )
                    if should_alert:
                        sent = await send_auction_watch_alert(item, lego_set.set_number, lego_set.set_name)
                        if sent:
                            item.last_alerted_at = datetime.now(UTC)
                            summary["alerts_sent"] += 1
            except SoftTimeLimitExceeded:
                await session.commit()
                raise
            except Exception as exc:
                summary["errors"] += 1
                logger.error("auction_watch.refresh_failed", item_id=item.id, error=str(exc))

        await session.commit()

    logger.info("auction_watch.refresh_complete", **summary)
    if summary["errors"]:
        raise RuntimeError(f"Auktionspruefung teilweise fehlgeschlagen: {summary}")
    return summary
