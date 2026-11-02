"""eBay-Verkaufsabgleich (Celery Beat, alle 30 Minuten).

Planung und Regeln in `app.services.ebay_listing.sync_sales`; hier nur Laden,
Speichern, Melden. Ohne eingerichtete eBay-Anbindung tut der Task nichts.
"""

import structlog
from telegram import Bot

from app.models.base import async_session
from app.runtime_settings import get_settings_map
from app.services.ebay_api import EbayClient, EbayConfig, EbayError, is_configured
from app.services.ebay_listing import format_sync_message, sync_sales
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="app.tasks.ebay_sync.sync_ebay_sales_task")
def sync_ebay_sales_task() -> dict:
    return _run_async(_sync_async())


async def _notify(text: str) -> bool:
    runtime = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    token, chat_id = runtime.get("telegram_bot_token"), runtime.get("telegram_chat_id")
    if not token or not chat_id:
        return False
    try:
        await Bot(token=token).send_message(chat_id=chat_id, text=text)
        return True
    except Exception as exc:  # noqa: BLE001 — Meldung ist Zusatz, der Abgleich ist schon gespeichert
        logger.error("ebay.sync_notify_failed", error=str(exc))
        return False


async def _sync_async() -> dict:
    if not is_configured():
        return {"skipped": "not_configured"}
    async with async_session() as session:
        try:
            async with EbayClient(EbayConfig.from_settings()) as client:
                result = await sync_sales(session, client)
        except EbayError as exc:
            await session.rollback()
            logger.warning("ebay.sync_failed", error=exc.detail)
            # Als Fehler nach oben: der Pipeline-Health-Watchdog sieht ihn dann.
            raise RuntimeError(exc.detail) from exc
        # Erst speichern, dann melden: faellt Telegram aus, ist der Verkauf trotzdem erfasst.
        await session.commit()
    message = format_sync_message(result)
    sent = await _notify(message) if message else False
    return {
        "orders_seen": result.orders_seen,
        "sold": len(result.sold),
        "partial": len(result.partial),
        "withdrawn": result.withdrawn,
        "errors": len(result.errors),
        "notified": sent,
    }
