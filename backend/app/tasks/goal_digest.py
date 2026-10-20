"""Wochen-Digest am Montag: Abstand zu den drei Zielzahlen (Memory projekt-ziel).

Berichtet die abgeschlossene Kalenderwoche (Mo–So, Berlin) gegen die Woche
davor: gepruefte Deals, neu inserierte und verkaufte Lego-Posten, dazu der
Gesamtstand gegen 30 inseriert / 15 verkauft bis zur Frist. Sendet immer,
auch bei lauter Nullen: eine Woche ohne Deal ist genau die Nachricht.

Laeuft vor dem Montags-Paket (08:00), damit die Zahl zuerst kommt und die
fertigen Inserate danach.
"""

import structlog

from app.models.base import async_session
from app.notifications.delivery import require_delivery
from app.notifications.telegram_bot import send_goal_digest
from app.services.goal_progress import load_goal_digest
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.tasks.goal_digest.send_goal_digest_task")
def send_goal_digest_task() -> dict:
    """Beat-geplant montags 07:50 Berlin."""
    return require_delivery(_run_async(_digest_async()), "Wochen-Digest")


async def _digest_async() -> dict:
    async with async_session() as session:
        digest = await load_goal_digest(session)

    sent = await send_goal_digest(digest)
    reported = digest.reported
    stats = {
        "week": reported.week.label,
        "deals": reported.deals.total,
        "checks": reported.deals.checks,
        "listed_week": reported.sales.listed,
        "sold_week": reported.sales.sold,
        "lego_listed": digest.progress.lego_listed,
        "lego_sold": digest.progress.lego_sold,
    }
    logger.info("goal_digest.done", sent=sent, **stats)
    return {"sent": sent, **stats}
