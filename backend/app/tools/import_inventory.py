"""Import eines Eingang-Durchgangs: Posten, Fotos und Anzeigen-Entwuerfe.

Zweiter Weg ins Inventar neben der Foto-first-Anlage in der App: Claude sichtet
die Fotos in der Sitzung, legt die freigegebene Tabelle als `manifest.json` ab
und ruft dieses Werkzeug im API-Container auf. Spec:
`docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.

    python -m app.tools.import_inventory /tmp/import           # Probelauf
    python -m app.tools.import_inventory /tmp/import --apply   # schreibt

Wiederholbar: Jeder Posten traegt den Marker `[<mark> <key>]` am Anfang seiner
Notiz. Was es schon gibt, wird uebersprungen -- ein abgebrochener Lauf laesst
sich damit einfach wiederholen.
"""

import argparse
import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.api.routes.inventory import (
    InventoryAdd,
    InventoryPhotoUpload,
    InventoryPhotoUploadRequest,
    _get_item,
    add_inventory_item,
    upload_inventory_photos,
)
from app.api.routes.listings import ListingCreate, create_listing
from app.models.base import async_session
from app.models.inventory import InventoryItem
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingStatus
from app.services.listing_rules import default_price_type

logger = structlog.get_logger()

TITLE_MAX_LENGTH = Listing.__table__.c.title.type.length
_CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class ManifestError(Exception):
    """Das Manifest passt nicht zu dem, was die App annehmen kann."""


class ManifestListing(BaseModel):
    platform: str
    status: str = ListingStatus.DRAFT.value
    title: str | None = None
    body: str | None = None
    platform_category: str | None = None
    price: float | None = None
    price_type: str | None = None
    url: str | None = None
    listed_at: date | None = None


class ManifestItem(BaseModel):
    key: str
    item_type: str = "GENERIC"
    set_number: str | None = None
    set_name: str
    product_group: str | None = None
    theme: str | None = None
    condition: str = "NEW_SEALED"
    quantity: int = 1
    search_query: str | None = None
    buy_date: date
    notes: str = ""
    photos: list[str] = []
    listings: list[ManifestListing] = []


class Manifest(BaseModel):
    mark: str
    items: list[ManifestItem]


@dataclass
class Summary:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    photos: int = 0
    listings: int = 0


def load_manifest(source: Path) -> Manifest:
    path = source / "manifest.json"
    if not path.exists():
        raise ManifestError(f"{path} fehlt")
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ManifestError(f"manifest.json ist unvollstaendig: {exc}") from exc


def _photo_payload(source: Path, item: ManifestItem) -> InventoryPhotoUploadRequest:
    uploads = []
    for name in item.photos:
        path = source / name
        content_type = _CONTENT_TYPES.get(path.suffix.lower())
        if content_type is None:
            raise ManifestError(f"{item.key}: {name} ist kein unterstuetztes Bildformat")
        data = base64.b64encode(path.read_bytes()).decode()
        uploads.append(
            InventoryPhotoUpload(
                filename=name,
                content_type=content_type,
                data_url=f"data:{content_type};base64,{data}",
            )
        )
    return InventoryPhotoUploadRequest(photos=uploads)


def _prepare(source: Path, manifest: Manifest) -> list[tuple[ManifestItem, InventoryAdd]]:
    """Alles pruefen, bevor irgendetwas geschrieben wird: fehlende Fotos oder ein
    Lego-Posten ohne Setnummer sollen nicht erst nach dem halben Lauf auffallen."""
    prepared = []
    for item in manifest.items:
        for name in item.photos:
            if not (source / name).exists():
                raise ManifestError(f"{item.key}: Foto {name} fehlt in {source}")
        for listing in item.listings:
            if listing.status == ListingStatus.ACTIVE.value and not listing.price:
                raise ManifestError(f"{item.key}: aktives Listing ohne Preis")
            if listing.status not in (ListingStatus.DRAFT.value, ListingStatus.ACTIVE.value):
                raise ManifestError(f"{item.key}: Listing-Status {listing.status} wird hier nicht angelegt")
        try:
            add = InventoryAdd(
                item_type=item.item_type,
                set_number=item.set_number,
                set_name=item.set_name,
                product_group=item.product_group,
                search_query=item.search_query,
                theme=item.theme,
                buy_date=item.buy_date,
                condition=item.condition,
                quantity=item.quantity,
                notes=f"[{manifest.mark} {item.key}] {item.notes}".strip(),
            )
        except ValidationError as exc:
            raise ManifestError(f"{item.key}: {exc.errors()[0]['msg']}") from exc
        prepared.append((item, add))
    return prepared


async def _existing_item_id(session, mark: str, key: str) -> int | None:
    result = await session.execute(select(InventoryItem.id).where(InventoryItem.notes.like(f"[{mark} {key}]%")))
    return result.scalar_one_or_none()


def _draft_listing(item_id: int, listing: ManifestListing) -> Listing:
    return Listing(
        item_id=item_id,
        platform=listing.platform,
        status=ListingStatus.DRAFT.value,
        price_type=listing.price_type or default_price_type(listing.platform),
        title=(listing.title or "")[:TITLE_MAX_LENGTH] or None,
        body=listing.body,
        platform_category=listing.platform_category,
        current_price=listing.price,
        check_interval_days=14,
        price_drop_percent=10.0,
    )


async def run(source: Path, session, *, apply: bool) -> Summary:
    manifest = load_manifest(source)
    prepared = _prepare(source, manifest)
    summary = Summary()

    for item, add in prepared:
        item_id = await _existing_item_id(session, manifest.mark, item.key)
        if not apply:
            (summary.skipped if item_id else summary.would_create).append(item.key)
            continue

        if item_id is None:
            item_id = (await add_inventory_item(add, session)).id
            summary.created.append(item.key)
        else:
            summary.skipped.append(item.key)

        stored = await _get_item(item_id, session)
        if item.photos and not stored.photos:
            await upload_inventory_photos(item_id, _photo_payload(source, item), session)
            summary.photos += len(item.photos)

        for listing in item.listings:
            stored = await _get_item(item_id, session)
            if any(x.platform == listing.platform and x.status in OPEN_LISTING_STATUSES for x in stored.listings):
                continue
            if listing.status == ListingStatus.ACTIVE.value:
                await create_listing(
                    item_id,
                    ListingCreate(
                        platform=listing.platform,
                        current_price=listing.price,
                        price_type=listing.price_type,
                        url=listing.url,
                        listed_at=listing.listed_at,
                    ),
                    session,
                )
            else:
                # Die App erzeugt Entwuerfe sonst ueber den KI-Weg; hier kommt der
                # Text aus der Sitzung, geschrieben wird direkt.
                session.add(_draft_listing(item_id, listing))
                await session.commit()
            summary.listings += 1

        logger.info("eingang.imported", key=item.key, item_id=item_id)

    return summary


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Eingang-Durchgang ins Inventar uebernehmen")
    parser.add_argument("source", type=Path, help="Verzeichnis mit manifest.json und Fotos")
    parser.add_argument("--apply", action="store_true", help="wirklich schreiben (sonst nur Probelauf)")
    args = parser.parse_args()

    async with async_session() as session:
        summary = await run(args.source, session, apply=args.apply)

    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(_main())
