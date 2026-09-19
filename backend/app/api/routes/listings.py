"""Listing-Lifecycle: manuell gepflegte eigene Anzeigen (ADR 0002).

Eigene Router-Datei, weil inventory.py bereits >700 Zeilen traegt.
Kein Import aus inventory.py — sonst Zirkularimport, denn inventory.py
bettet ListingResponse in seine InventoryResponse ein.
"""

from datetime import UTC, date, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIProviderError, get_provider
from app.models import get_session
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingPlatform, ListingStatus
from app.services.listing_rules import (
    apply_price_change,
    compute_next_check,
    default_min_price,
    default_price_type,
    validate_activation,
)

logger = structlog.get_logger()
router = APIRouter()


class ListingCreate(BaseModel):
    platform: str
    current_price: float
    listed_at: date | None = None
    url: str | None = None
    min_price: float | None = None
    price_type: str | None = None
    check_interval_days: int = 14
    price_drop_percent: float = 10.0


class ListingUpdate(BaseModel):
    current_price: float | None = None
    url: str | None = None
    min_price: float | None = None
    status: str | None = None  # nur ACTIVE <-> PAUSED
    price_type: str | None = None
    check_interval_days: int | None = None
    price_drop_percent: float | None = None
    title: str | None = None
    body: str | None = None


class ListingDraftRequest(BaseModel):
    platform: str
    price: float | None = None


class PriceChangeResponse(BaseModel):
    id: int
    changed_at: datetime
    old_price: float
    new_price: float

    model_config = {"from_attributes": True}


class ListingResponse(BaseModel):
    id: int
    platform: str
    status: str
    price_type: str
    title: str | None
    body: str | None
    platform_category: str | None
    listed_at: date | None
    current_price: float | None
    url: str | None
    min_price: float | None
    check_interval_days: int
    price_drop_percent: float
    next_check_at: datetime | None
    suggested_price: float | None
    suggestion_reason: str | None
    suggestion_at: datetime | None
    at_floor: bool
    price_changes: list[PriceChangeResponse]
    created_at: datetime

    model_config = {"from_attributes": True}


def to_listing_response(listing: Listing) -> ListingResponse:
    at_floor = (
        listing.status == ListingStatus.ACTIVE.value
        and listing.min_price is not None
        and listing.current_price is not None
        and listing.current_price <= listing.min_price
    )
    return ListingResponse(
        id=listing.id,
        platform=listing.platform,
        status=listing.status,
        price_type=listing.price_type,
        title=listing.title,
        body=listing.body,
        platform_category=listing.platform_category,
        listed_at=listing.listed_at,
        current_price=listing.current_price,
        url=listing.url,
        min_price=listing.min_price,
        check_interval_days=listing.check_interval_days,
        price_drop_percent=listing.price_drop_percent,
        next_check_at=listing.next_check_at,
        suggested_price=listing.suggested_price,
        suggestion_reason=listing.suggestion_reason,
        suggestion_at=listing.suggestion_at,
        at_floor=at_floor,
        price_changes=[PriceChangeResponse.model_validate(change) for change in listing.price_changes],
        created_at=listing.created_at,
    )


def open_listing_responses(item: InventoryItem) -> list[ListingResponse]:
    return [to_listing_response(x) for x in item.listings if x.status in OPEN_LISTING_STATUSES]


async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(select(InventoryItem).where(InventoryItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


async def _get_listing(item_id: int, listing_id: int, session: AsyncSession) -> Listing:
    result = await session.execute(
        select(Listing).where(Listing.id == listing_id, Listing.item_id == item_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing nicht gefunden")
    return listing


TITLE_MAX_LENGTH = Listing.__table__.c.title.type.length


def _listing_name(item: InventoryItem) -> str:
    """Artikelname fuer den KI-Prompt. Bei Lego gehoeren Marke und Setnummer in
    den Titel -- danach wird gesucht, get_sell_links setzt sie ebenso voran."""
    if item.item_type != InventoryItemType.LEGO.value or not item.set_number or item.set_number in item.set_name:
        return item.set_name
    return f"LEGO {item.set_number} {item.set_name}"


def _reject_unconfirmed_draft(item: InventoryItem) -> None:
    if item.status == InventoryStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Entwurf zuerst bestaetigen")


def _draft_price(item: InventoryItem, listing: Listing | None, body_price: float | None) -> float | None:
    """Preis-Fallback-Kette fuer KI-Anzeigentexte (Draft-Erzeugung und -Refresh).

    Reihenfolge: expliziter Preis aus dem Request-Body, dann der laufende
    Anzeigenpreis eines schon eingestellten Listings (ACTIVE/PAUSED — beim
    Text-Refresh gibt es keinen Body-Preis), dann der Marktpreis der Position,
    dann die Mitte der KI-Preisspanne (nur wenn beide Grenzen bekannt sind),
    dann der 1,5-fache Einkaufspreis. None, wenn keine Quelle greift — der
    Aufrufer antwortet dann mit 400.
    """
    if body_price is not None:
        return body_price
    if listing is not None and listing.status in (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value):
        return listing.current_price
    if item.current_market_price is not None:
        return item.current_market_price
    if item.ai_price_min is not None and item.ai_price_max is not None:
        return (item.ai_price_min + item.ai_price_max) / 2
    if item.buy_price is not None:
        return item.buy_price * 1.5
    return None


@router.get("/{item_id}/listings", response_model=list[ListingResponse])
async def list_listings(item_id: int, session: AsyncSession = Depends(get_session)):
    """Alle Listings inkl. Historie, neueste zuerst."""
    item = await _get_item(item_id, session)
    ordered = sorted(item.listings, key=lambda x: x.created_at, reverse=True)
    return [to_listing_response(x) for x in ordered]


@router.post("/{item_id}/listings/draft", response_model=ListingResponse)
async def draft_listing(item_id: int, data: ListingDraftRequest, session: AsyncSession = Depends(get_session)):
    """KI schreibt den Anzeigentext: legt eine DRAFT-Zeile an oder ersetzt die
    Texte einer schon vorhandenen. Der partial-unique-Index laesst ohnehin nur
    eine offene Zeile je Artikel+Plattform zu (uq_listings_open_per_platform) —
    ist die vorhandene offene Zeile schon ACTIVE/PAUSED, gibt's stattdessen 400."""
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")
    _reject_unconfirmed_draft(item)
    platform = data.platform.strip().upper()
    if platform not in (p.value for p in ListingPlatform):
        raise HTTPException(status_code=400, detail=f"Unbekannte Plattform: {platform}")
    existing = next(
        (x for x in item.listings if x.platform == platform and x.status in OPEN_LISTING_STATUSES), None
    )
    if existing is not None and existing.status != ListingStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Schon eingestellt — Text-Refresh nutzen")

    price = _draft_price(item, existing, data.price)
    if price is None:
        raise HTTPException(status_code=400, detail="Kein Preis ermittelbar — bitte Preis angeben")

    price_type = existing.price_type if existing is not None else default_price_type(platform)
    try:
        provider = get_provider()
        text = await provider.write_listing(
            name=_listing_name(item),
            condition=item.condition,
            notes=item.notes,
            platform=platform,
            price=price,
            price_type=price_type,
            quantity=item.quantity,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc

    if existing is not None:
        listing = existing
        listing.title = text.title[:TITLE_MAX_LENGTH]
        listing.body = text.body
        listing.platform_category = text.platform_category
    else:
        listing = Listing(
            item_id=item.id,
            platform=platform,
            status=ListingStatus.DRAFT.value,
            price_type=price_type,
            title=text.title[:TITLE_MAX_LENGTH],
            body=text.body,
            platform_category=text.platform_category,
            check_interval_days=14,
            price_drop_percent=10.0,
        )
        session.add(listing)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Es gibt schon ein offenes Listing auf {platform}") from None
    await session.refresh(listing)
    logger.info("listing.draft_generated", item_id=item.id, platform=platform, overwritten=existing is not None)
    return to_listing_response(listing)


@router.post("/{item_id}/listings/{listing_id}/refresh-text", response_model=ListingResponse)
async def refresh_listing_text(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """KI schreibt Titel/Body neu — nur fuer offene Listings. Preis kommt vom
    laufenden Listing (ACTIVE/PAUSED) oder sonst aus derselben Fallback-Kette
    wie beim Draft-Erzeugen (DRAFT hat noch keinen eigenen Anzeigenpreis)."""
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Beendete Listings sind Historie und unveraenderlich")
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")

    price = _draft_price(item, listing, None)
    if price is None:
        raise HTTPException(status_code=400, detail="Kein Preis ermittelbar — bitte Preis angeben")

    try:
        provider = get_provider()
        text = await provider.write_listing(
            name=_listing_name(item),
            condition=item.condition,
            notes=item.notes,
            platform=listing.platform,
            price=price,
            price_type=listing.price_type,
            quantity=item.quantity,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc

    listing.title = text.title[:TITLE_MAX_LENGTH]
    listing.body = text.body
    listing.platform_category = text.platform_category
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.text_refreshed", item_id=item_id, listing_id=listing_id, platform=listing.platform)
    return to_listing_response(listing)


@router.post("/{item_id}/listings", response_model=ListingResponse)
async def create_listing(item_id: int, data: ListingCreate, session: AsyncSession = Depends(get_session)):
    """Als eingestellt markieren: Mensch hat die Anzeige angelegt, wir merken sie.

    Gibt es dafuer schon eine offene DRAFT-Zeile (KI-Text vorbereitet), wird
    SIE aktiviert statt eine zweite Zeile anzulegen — der partial-unique-Index
    liesse das ohnehin nicht zu. Titel/Body/platform_category bleiben dabei
    unangetastet. Bei ACTIVE/PAUSED bleibt es bei 400."""
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")
    _reject_unconfirmed_draft(item)
    platform = data.platform.strip().upper()
    min_price = data.min_price if data.min_price is not None else default_min_price(data.current_price)
    error = validate_activation(platform, data.current_price, min_price)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if data.check_interval_days < 1:
        raise HTTPException(status_code=400, detail="check_interval_days muss mindestens 1 sein")
    if not 0 <= data.price_drop_percent < 100:
        raise HTTPException(status_code=400, detail="price_drop_percent muss zwischen 0 und 99 liegen")
    existing = next(
        (x for x in item.listings if x.platform == platform and x.status in OPEN_LISTING_STATUSES), None
    )
    if existing is not None and existing.status != ListingStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail=f"Es gibt schon ein offenes Listing auf {platform}")

    listed_at = data.listed_at or date.today()
    if existing is not None:
        listing = existing
        listing.status = ListingStatus.ACTIVE.value
        listing.price_type = data.price_type or default_price_type(platform)
        listing.listed_at = listed_at
        listing.current_price = data.current_price
        listing.url = (data.url or "").strip() or None
        listing.min_price = min_price
        listing.check_interval_days = data.check_interval_days
        listing.price_drop_percent = data.price_drop_percent
        listing.next_check_at = compute_next_check(listed_at, data.check_interval_days)
    else:
        listing = Listing(
            item_id=item.id,
            platform=platform,
            status=ListingStatus.ACTIVE.value,
            price_type=(data.price_type or default_price_type(platform)),
            listed_at=listed_at,
            current_price=data.current_price,
            url=(data.url or "").strip() or None,
            min_price=min_price,
            check_interval_days=data.check_interval_days,
            price_drop_percent=data.price_drop_percent,
            next_check_at=compute_next_check(listed_at, data.check_interval_days),
        )
        session.add(listing)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Es gibt schon ein offenes Listing auf {platform}") from None
    await session.refresh(listing)
    logger.info(
        "listing.activated",
        item_id=item.id,
        platform=platform,
        price=data.current_price,
        reactivated=existing is not None,
    )
    return to_listing_response(listing)


@router.patch("/{item_id}/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(
    item_id: int, listing_id: int, data: ListingUpdate, session: AsyncSession = Depends(get_session)
):
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Beendete Listings sind Historie und unveraenderlich")

    target_price = data.current_price if data.current_price is not None else listing.current_price
    target_min = data.min_price if data.min_price is not None else listing.min_price
    if data.current_price is not None and (target_price is None or target_price <= 0):
        raise HTTPException(status_code=400, detail="Preis muss groesser 0 sein")
    if data.min_price is not None and (target_min is None or target_min <= 0):
        raise HTTPException(status_code=400, detail="Schmerzgrenze muss groesser 0 sein")
    price_or_min_touched = data.current_price is not None or data.min_price is not None
    if price_or_min_touched and target_min is not None and target_price is not None and target_min > target_price:
        raise HTTPException(status_code=400, detail="Schmerzgrenze liegt ueber dem Preis")
    if data.check_interval_days is not None and data.check_interval_days < 1:
        raise HTTPException(status_code=400, detail="check_interval_days muss mindestens 1 sein")
    if data.price_drop_percent is not None and not 0 <= data.price_drop_percent < 100:
        raise HTTPException(status_code=400, detail="price_drop_percent muss zwischen 0 und 99 liegen")

    if data.status is not None:
        allowed = {ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value}
        if data.status not in allowed or listing.status not in allowed:
            raise HTTPException(status_code=400, detail="Nur Wechsel zwischen ACTIVE und PAUSED erlaubt")
        listing.status = data.status

    for field in ("url", "min_price", "price_type", "check_interval_days", "price_drop_percent", "title", "body"):
        value = getattr(data, field)
        if value is not None:
            setattr(listing, field, value)

    if data.current_price is not None and listing.status == ListingStatus.DRAFT.value:
        # Noch nicht eingestellt: kein alter Anzeigenpreis, also keine Preisaenderung
        # und kein Check-Termin -- beides beginnt erst mit der Aktivierung.
        listing.current_price = data.current_price
    elif data.current_price is not None:
        change = apply_price_change(listing, data.current_price, datetime.now(UTC))
        if change is not None:
            session.add(change)

    await session.commit()
    await session.refresh(listing)
    return to_listing_response(listing)


@router.post("/{item_id}/listings/{listing_id}/end", response_model=ListingResponse)
async def end_listing(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """Anzeige geloescht/abgelaufen — Zeile bleibt als Historie (ENDED)."""
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Listing ist bereits beendet")
    listing.status = ListingStatus.ENDED.value
    await session.commit()
    await session.refresh(listing)
    return to_listing_response(listing)


@router.delete("/{item_id}/listings/{listing_id}")
async def delete_listing(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """Fuer Fehleingaben — loescht die Zeile samt Preis-Historie endgueltig."""
    listing = await _get_listing(item_id, listing_id, session)
    await session.delete(listing)
    await session.commit()
    return {"status": "deleted", "id": listing_id}
