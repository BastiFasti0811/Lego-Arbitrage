"""Montags-Paket: fuenf fertige Inserate der Woche per Telegram.

Sebastian stellt jede Anzeige selbst ein (ADR 0002). Das Paket nimmt ihm die
Vorarbeit ab: Es waehlt fuenf noch nicht inserierte Lego-Posten, legt fuer jeden
den Kleinanzeigen-Entwurf an (derselbe Weg wie "Inserat vorbereiten" in der App)
und schickt Fotos, Titel und Text so, dass er sie vom Handy aus uebernehmen kann.
Ist ein Posten bis zum naechsten Montag nicht eingestellt, kommt er wieder.
"""

from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.listing import ListingPlatform, ListingStatus
from app.notifications.delivery import require_delivery
from app.notifications.telegram_bot import send_listing_pack
from app.services.listing_prepare import ListingPrepareError, draft_price, prepare_listing_draft
from app.tasks.async_runner import run_async as _run_async
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()

PACK_SIZE = 5
PACK_PLATFORM = ListingPlatform.KLEINANZEIGEN.value
# Dieselbe Ablage wie app.api.routes.inventory.PHOTO_STORAGE_ROOT; der Task
# importiert das Routen-Modul nicht, nur um einen Pfad zu lesen.
PHOTO_STORAGE_ROOT = Path(settings.media_root) / "inventory_photos"
_RUNNING = (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value)


@dataclass
class PackEntry:
    item_id: int
    label: str
    title: str
    body: str
    price: float
    price_type: str
    text_source: str
    storage_location: str | None = None
    photo_paths: list[Path] = field(default_factory=list)


def is_unlisted(item: InventoryItem) -> bool:
    """Keine laufende Anzeige auf irgendeiner Plattform. Ein Entwurf zaehlt nicht."""
    return not any(listing.status in _RUNNING for listing in item.listings)


def pick_pack_candidates(items, limit: int = PACK_SIZE) -> list[InventoryItem]:
    """Fotos zuerst (ohne Foto laesst sich nichts einstellen), dann der hoechste
    Preisvorschlag: bei gleicher Arbeit bringt der teurere Posten mehr."""
    eligible = [
        item
        for item in items
        if item.status == InventoryStatus.HOLDING.value
        and item.item_type == InventoryItemType.LEGO.value
        and is_unlisted(item)
        and draft_price(item, None, None) is not None
    ]
    eligible.sort(key=lambda item: (0 if item.photos else 1, -(draft_price(item, None, None) or 0), item.id))
    return eligible[:limit]


def _photo_paths(item: InventoryItem) -> list[Path]:
    paths = [PHOTO_STORAGE_ROOT / str(item.id) / photo.filename for photo in item.photos]
    return [path for path in paths if path.exists()]


@celery_app.task(name="app.tasks.listing_pack.send_listing_pack_task")
def send_listing_pack_task() -> dict:
    """Beat-geplant montags 08:00 Berlin."""
    return require_delivery(_run_async(_pack_async()), "Montags-Inserate")


async def _pack_async() -> dict:
    entries: list[PackEntry] = []
    async with async_session() as session:
        result = await session.execute(
            select(InventoryItem).where(
                InventoryItem.status == InventoryStatus.HOLDING.value,
                InventoryItem.item_type == InventoryItemType.LEGO.value,
            )
        )
        items = result.scalars().all()
        unlisted_total = sum(1 for item in items if is_unlisted(item))

        for item in pick_pack_candidates(items):
            try:
                prepared = await prepare_listing_draft(item, PACK_PLATFORM)
            except ListingPrepareError as exc:
                logger.info("listing_pack.skipped", item_id=item.id, reason=exc.detail)
                continue
            if prepared.created:
                session.add(prepared.listing)
            try:
                await session.commit()
            except IntegrityError:
                # Parallel in der App vorbereitet. Der Rollback laesst alle
                # geladenen Zeilen verfallen (Nachladen geht async nicht), also
                # hier aufhoeren und mit dem Bisherigen senden.
                await session.rollback()
                logger.info("listing_pack.race", item_id=item.id)
                break
            listing = prepared.listing
            entries.append(
                PackEntry(
                    item_id=item.id,
                    label=f"{item.set_number} {item.set_name}" if item.set_number else item.set_name,
                    title=listing.title or "",
                    body=listing.body or "",
                    price=listing.current_price,
                    price_type=listing.price_type,
                    text_source=prepared.text_source.value,
                    storage_location=item.storage_location,
                    photo_paths=_photo_paths(item),
                )
            )

    sent = await send_listing_pack(entries, unlisted_total=unlisted_total)
    logger.info("listing_pack.done", sent=sent, items=len(entries), unlisted_total=unlisted_total)
    return {"sent": sent, "items": len(entries), "unlisted_total": unlisted_total}
