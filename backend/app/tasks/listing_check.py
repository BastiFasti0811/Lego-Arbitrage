"""Tages-Check der eigenen Listings (Celery Beat, taeglich 08:05 Berlin).

Planung in `app.services.listing_check`; hier nur Laden, Speichern, Senden.
Es gibt kein haeufigeres Scanning fuer Listings (CONTEXT.md, „Tages-Check“).
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.notifications.delivery import require_delivery
from app.notifications.telegram_bot import send_listing_check
from app.services.listing_check import plan_listing_check, revert_floor_hints
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.tasks.listing_check.run_listing_check_task")
def run_listing_check_task() -> dict:
    """Beat-geplant taeglich 08:05 Berlin. Ohne faellige Punkte geht keine
    Nachricht raus; dann ist "nicht gesendet" auch kein Zustellfehler."""
    result = _run_async(_check_async())
    if result["needs_message"]:
        return require_delivery(result, "Listing-Tages-Check")
    return result


async def _check_async(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    async with async_session() as session:
        rows = await session.execute(
            select(InventoryItem).where(
                InventoryItem.status.in_((InventoryStatus.HOLDING.value, InventoryStatus.SOLD.value))
            )
        )
        items = rows.scalars().all()
        plan = plan_listing_check(items, now)
        # Vorschlaege zuerst sichern: faellt Telegram aus, stehen sie trotzdem
        # in der App und kommen morgen als "weiter offen" erneut.
        await session.commit()

        sent = False
        if plan.has_news:
            sent = await send_listing_check(plan)
            if not sent and plan.floor_listings:
                revert_floor_hints(plan)
                await session.commit()

    result = {
        "sent": sent,
        "needs_message": plan.has_news,
        "checked": plan.checked,
        "new_suggestions": plan.new_suggestions,
        "open_suggestions": len(plan.suggestions),
        "floor_hints": len(plan.floor_hints),
        "sold_leftovers": len(plan.sold_leftovers),
        "snoozed": plan.snoozed,
    }
    logger.info("listing_check.done", **result)
    return result
