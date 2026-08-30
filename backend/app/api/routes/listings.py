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

from app.models import get_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingStatus
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


@router.get("/{item_id}/listings", response_model=list[ListingResponse])
async def list_listings(item_id: int, session: AsyncSession = Depends(get_session)):
    """Alle Listings inkl. Historie, neueste zuerst."""
    item = await _get_item(item_id, session)
    ordered = sorted(item.listings, key=lambda x: x.created_at, reverse=True)
    return [to_listing_response(x) for x in ordered]


@router.post("/{item_id}/listings", response_model=ListingResponse)
async def create_listing(item_id: int, data: ListingCreate, session: AsyncSession = Depends(get_session)):
    """Als eingestellt markieren: Mensch hat die Anzeige angelegt, wir merken sie."""
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")
    platform = data.platform.strip().upper()
    min_price = data.min_price if data.min_price is not None else default_min_price(data.current_price)
    error = validate_activation(platform, data.current_price, min_price)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if data.check_interval_days < 1:
        raise HTTPException(status_code=400, detail="check_interval_days muss mindestens 1 sein")
    if not 0 <= data.price_drop_percent < 100:
        raise HTTPException(status_code=400, detail="price_drop_percent muss zwischen 0 und 99 liegen")
    if any(x.platform == platform and x.status in OPEN_LISTING_STATUSES for x in item.listings):
        raise HTTPException(status_code=400, detail=f"Es gibt schon ein offenes Listing auf {platform}")

    listed_at = data.listed_at or date.today()
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
    logger.info("listing.activated", item_id=item.id, platform=platform, price=data.current_price)
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

    if data.current_price is not None:
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
