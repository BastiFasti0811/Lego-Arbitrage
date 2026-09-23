"""Import eines Eingang-Durchgangs: Posten, Fotos und Anzeigen-Entwuerfe.

Zweiter Weg ins Inventar neben der Foto-first-Anlage in der App: Claude sichtet
die Fotos in der Sitzung, legt die freigegebene Tabelle als `manifest.json` ab
und ruft dieses Werkzeug im API-Container auf. Spec:
`docs/superpowers/specs/2026-09-20-eingang-workflow-design.md`.

    python -m app.tools.import_inventory /tmp/import           # Probelauf
    python -m app.tools.import_inventory /tmp/import --apply   # schreibt

Wiederholbar: Jeder Posten traegt den Marker `[<mark> <key>]` in seiner
Notiz (auch nach dem Bearbeiten in der App). Was es schon gibt, wird
uebersprungen -- ein abgebrochener Lauf laesst sich einfach wiederholen.
"""

import argparse
import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.routes.inventory import (
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_ITEM,
    InventoryAdd,
    InventoryPhotoUpload,
    InventoryPhotoUploadRequest,
    _get_item,
    add_inventory_item,
    upload_inventory_photos,
)
from app.api.routes.listings import ListingCreate, create_listing
from app.models.base import async_session
from app.models.inventory import InventoryItem, InventoryItemType
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingPlatform, ListingStatus
from app.models.offer import OfferCondition
from app.services.listing_rules import default_price_type

logger = structlog.get_logger()

_COLUMNS = InventoryItem.__table__.c
_LISTING_COLUMNS = Listing.__table__.c
TITLE_MAX_LENGTH = _LISTING_COLUMNS.title.type.length
# Deckungsgleich mit ALLOWED_IMAGE_TYPES der Upload-Route.
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Was auf PostgreSQL an der Spaltenlaenge scheitern wuerde, faellt auf SQLite
# still durch -- also vorher pruefen, nicht erst mitten im Lauf.
_ITEM_LIMITS = {
    "set_name": _COLUMNS.set_name.type.length,
    "set_number": _COLUMNS.set_number.type.length,
    "product_group": _COLUMNS.product_group.type.length,
    "theme": _COLUMNS.theme.type.length,
    "search_query": _COLUMNS.search_query.type.length,
    "condition": _COLUMNS.condition.type.length,
}
_LISTING_LIMITS = {
    "title": TITLE_MAX_LENGTH,
    "platform_category": _LISTING_COLUMNS.platform_category.type.length,
}
_ACTIVATABLE = (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value)
_LEGO_OVERWRITTEN = ("product_group", "search_query")


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
    quantity: int = Field(default=1, ge=1)
    search_query: str | None = None
    buy_date: date
    # Dachbodenfunde kennen meist keinen Kaufpreis -- optional, im Unterschied
    # zu einer Rechnungs-Nachbildung (z. B. Altbestands-Import aus einer Tabelle).
    buy_price: float | None = None
    buy_shipping: float = 0.0
    buy_platform: str | None = Field(default=None, max_length=100)
    buy_url: str | None = None
    storage_location: str | None = Field(default=None, max_length=200)
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
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError("manifest.json muss ein Objekt mit 'mark' und 'items' sein")
    items = []
    # Posten einzeln validieren, damit die Meldung den Schluessel nennt und
    # nicht nur "items.7.quantity".
    for entry in raw.get("items", []):
        try:
            items.append(ManifestItem.model_validate(entry))
        except ValidationError as exc:
            detail = exc.errors()[0]
            raise ManifestError(f"{entry.get('key', '?')}: {detail['loc'][-1]} {detail['msg']}") from exc
    try:
        return Manifest(mark=raw["mark"], items=items)
    except (KeyError, ValidationError) as exc:
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


def _check_photos(source: Path, item: ManifestItem) -> None:
    if len(item.photos) > MAX_PHOTOS_PER_ITEM:
        raise ManifestError(f"{item.key}: {len(item.photos)} Fotos, erlaubt sind {MAX_PHOTOS_PER_ITEM}")
    for name in item.photos:
        path = source / name
        if not path.exists():
            raise ManifestError(f"{item.key}: Foto {name} fehlt in {source}")
        if path.suffix.lower() not in _CONTENT_TYPES:
            raise ManifestError(f"{item.key}: {name} ist kein unterstuetztes Bildformat")
        if path.stat().st_size > MAX_PHOTO_BYTES:
            raise ManifestError(f"{item.key}: {name} ist groesser als {MAX_PHOTO_BYTES // (1024 * 1024)} MB")


def _check_listings(item: ManifestItem) -> None:
    """Normalisiert die Plattform und prueft, was die App sonst erst beim
    Schreiben bemerken wuerde -- DRAFT-Zeilen gehen an create_listing vorbei."""
    platforms = set()
    for listing in item.listings:
        listing.platform = listing.platform.strip().upper()
        if listing.platform not in (p.value for p in ListingPlatform):
            raise ManifestError(f"{item.key}: unbekannte Plattform {listing.platform}")
        if listing.platform in platforms:
            raise ManifestError(f"{item.key}: zwei offene Listings auf {listing.platform}")
        platforms.add(listing.platform)
        if listing.status not in (ListingStatus.DRAFT.value, ListingStatus.ACTIVE.value):
            raise ManifestError(f"{item.key}: Listing-Status {listing.status} wird hier nicht angelegt")
        if listing.status == ListingStatus.ACTIVE.value and not (listing.price and listing.price > 0):
            raise ManifestError(f"{item.key}: aktives Listing ohne Preis")
        for name, limit in _LISTING_LIMITS.items():
            value = getattr(listing, name)
            if value and len(value) > limit:
                raise ManifestError(f"{item.key}: {name} hat {len(value)} Zeichen, erlaubt sind {limit}")


def _prepare(source: Path, manifest: Manifest) -> list[tuple[ManifestItem, InventoryAdd]]:
    """Alles pruefen, bevor irgendetwas geschrieben wird: ein Lego-Posten ohne
    Setnummer, ein zu langer Name oder ein fehlendes Foto sollen nicht erst nach
    dem halben Lauf auffallen -- die Routen committen einzeln."""
    prepared = []
    seen_keys = set()
    for item in manifest.items:
        if item.key in seen_keys:
            raise ManifestError(f"{item.key}: Schluessel kommt zweimal vor")
        seen_keys.add(item.key)
        if item.condition not in (c.value for c in OfferCondition):
            raise ManifestError(f"{item.key}: unbekannter Zustand {item.condition}")
        for name, limit in _ITEM_LIMITS.items():
            # product_group und search_query setzt InventoryAdd bei Lego selbst.
            if item.item_type == InventoryItemType.LEGO.value and name in _LEGO_OVERWRITTEN:
                continue
            value = getattr(item, name)
            if value and len(value) > limit:
                raise ManifestError(f"{item.key}: {name} hat {len(value)} Zeichen, erlaubt sind {limit}")
        _check_photos(source, item)
        _check_listings(item)
        try:
            add = InventoryAdd(
                item_type=item.item_type,
                set_number=item.set_number,
                set_name=item.set_name,
                product_group=item.product_group,
                search_query=item.search_query,
                theme=item.theme,
                buy_date=item.buy_date,
                buy_price=item.buy_price,
                buy_shipping=item.buy_shipping,
                buy_platform=item.buy_platform,
                buy_url=item.buy_url,
                storage_location=item.storage_location,
                condition=item.condition,
                quantity=item.quantity,
                notes=f"[{manifest.mark} {item.key}] {item.notes}".strip(),
            )
        except ValidationError as exc:
            raise ManifestError(f"{item.key}: {exc.errors()[0]['msg']}") from exc
        prepared.append((item, add))
    return prepared


async def _existing_item_id(session, mark: str, key: str) -> int | None:
    """Sucht den Marker irgendwo in der Notiz, nicht nur am Anfang -- in der App
    bearbeitete Notizen sollen den Wiederholungslauf nicht aushebeln. autoescape
    entschaerft `%` und `_` aus Marke und Schluessel."""
    marker = f"[{mark} {key}]"
    result = await session.execute(
        select(InventoryItem.id).where(InventoryItem.notes.contains(marker, autoescape=True))
    )
    ids = result.scalars().all()
    if len(ids) > 1:
        raise ManifestError(f"{key}: Marker steckt an mehreren Posten ({ids}) -- bitte von Hand aufloesen")
    return ids[0] if ids else None


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
            open_on_platform = [
                x for x in stored.listings
                if x.platform == listing.platform and x.status in OPEN_LISTING_STATUSES
            ]
            # Eine offene DRAFT-Zeile ist kein Grund zu ueberspringen, wenn das
            # Manifest die inzwischen eingestellte Anzeige meldet: create_listing
            # aktiviert genau diese Zeile und behaelt den vorbereiteten Text.
            already_live = any(x.status in _ACTIVATABLE for x in open_on_platform)
            if already_live or (open_on_platform and listing.status == ListingStatus.DRAFT.value):
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
