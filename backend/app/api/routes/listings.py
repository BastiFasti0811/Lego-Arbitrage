"""Listing-Lifecycle: manuell gepflegte eigene Anzeigen (ADR 0002).

Eigene Router-Datei, weil inventory.py bereits >700 Zeilen traegt.
Kein Import aus inventory.py — sonst Zirkularimport, denn inventory.py
bettet ListingResponse in seine InventoryResponse ein.
"""

from datetime import UTC, date, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIProviderError
from app.models import get_session
from app.models.inventory import InventoryItem, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingStatus
from app.services.listing_check import days_online, item_label
from app.services.listing_prepare import (
    ListingPrepareError,
    apply_text,
    draft_price,
    prepare_listing_draft,
    validate_listing_url,
    write_text,
)
from app.services.listing_rules import (
    apply_price_change,
    compute_next_check,
    default_min_price,
    default_price_type,
    dismiss_suggestion,
    is_at_floor,
    is_snoozed,
    snooze_suggestion,
    validate_activation,
)

logger = structlog.get_logger()
router = APIRouter()
# Uebersicht "Faellige Anpassungen" ueber alle Posten, gemountet unter /api/listings.
overview_router = APIRouter()


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


class ListingPrepareRequest(BaseModel):
    platform: str
    price: float | None = None


class ListingActivateRequest(BaseModel):
    """Nach dem Einstellen: nur die Adresse ist Pflicht, der Rest kommt vom Entwurf."""

    url: str = Field(min_length=1, max_length=2000)
    current_price: float | None = None
    listed_at: date | None = None
    min_price: float | None = None


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
    snoozed_until: datetime | None
    at_floor: bool
    price_changes: list[PriceChangeResponse]
    created_at: datetime

    model_config = {"from_attributes": True}


class PreparedListingResponse(BaseModel):
    listing: ListingResponse
    # "KI", "VORLAGE" (KI ausgefallen) oder "ENTWURF" (vorhandener Text)
    text_source: str
    warning: str | None = None


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
        snoozed_until=listing.snoozed_until,
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


def _reject_unconfirmed_draft(item: InventoryItem) -> None:
    if item.status == InventoryStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Entwurf zuerst bestaetigen")


@router.get("/{item_id}/listings", response_model=list[ListingResponse])
async def list_listings(item_id: int, session: AsyncSession = Depends(get_session)):
    """Alle Listings inkl. Historie, neueste zuerst."""
    item = await _get_item(item_id, session)
    ordered = sorted(item.listings, key=lambda x: x.created_at, reverse=True)
    return [to_listing_response(x) for x in ordered]


@router.post("/{item_id}/listings/prepare", response_model=PreparedListingResponse)
async def prepare_listing(item_id: int, data: ListingPrepareRequest, session: AsyncSession = Depends(get_session)):
    """"Inserat vorbereiten": legt den Entwurf mit Text und Preisvorschlag an
    oder nimmt den vorhandenen. Der einzige Textweg (ADR 0002: eingestellt
    wird von Hand). Faellt die KI aus, kommt die Vorlage mit Warnung statt 503,
    damit der Klick nie ins Leere geht."""
    item = await _get_item(item_id, session)
    try:
        prepared = await prepare_listing_draft(item, data.platform, price=data.price)
    except ListingPrepareError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    if prepared.created:
        session.add(prepared.listing)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail=f"Es gibt schon ein offenes Listing auf {prepared.listing.platform}"
        ) from None
    await session.refresh(prepared.listing)
    logger.info(
        "listing.prepared",
        item_id=item.id,
        platform=prepared.listing.platform,
        created=prepared.created,
        text_source=prepared.text_source.value,
    )
    return PreparedListingResponse(
        listing=to_listing_response(prepared.listing),
        text_source=prepared.text_source.value,
        warning=prepared.warning,
    )


@router.post("/{item_id}/listings/{listing_id}/refresh-text", response_model=ListingResponse)
async def refresh_listing_text(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """KI schreibt Titel/Body neu — nur fuer offene Listings. Preis kommt vom
    laufenden Listing (ACTIVE/PAUSED), vom Preisvorschlag des Entwurfs oder
    sonst aus derselben Fallback-Kette wie beim Vorbereiten."""
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status not in OPEN_LISTING_STATUSES:
        raise HTTPException(status_code=400, detail="Beendete Listings sind Historie und unveraenderlich")
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")

    # Ein vorbereiteter Entwurf traegt seinen Preisvorschlag schon selbst.
    price = draft_price(item, listing, None)
    if listing.status == ListingStatus.DRAFT.value and listing.current_price is not None:
        price = listing.current_price
    if price is None:
        raise HTTPException(status_code=400, detail="Kein Preis ermittelbar — bitte Preis angeben")

    try:
        text = await write_text(item, platform=listing.platform, price=price, price_type=listing.price_type)
    except AIProviderError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc

    apply_text(listing, text)
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


@router.post("/{item_id}/listings/{listing_id}/activate", response_model=ListingResponse)
async def activate_listing(
    item_id: int, listing_id: int, data: ListingActivateRequest, session: AsyncSession = Depends(get_session)
):
    """Entwurf -> aktiv, sobald die Anzeige steht: URL einfuegen genuegt.

    Preis kommt vom Entwurf (ausser er wird mitgeschickt), Datum ist heute,
    Schmerzgrenze 70 % — wie bei "Als eingestellt markieren"."""
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status != ListingStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Nur ein Entwurf laesst sich aktiv schalten")
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Verkaufte Artikel lassen sich nicht neu einstellen")
    _reject_unconfirmed_draft(item)

    url = data.url.strip()
    url_error = validate_listing_url(url, listing.platform)
    if url_error:
        raise HTTPException(status_code=400, detail=url_error)
    price = data.current_price if data.current_price is not None else listing.current_price
    if price is None:
        raise HTTPException(status_code=400, detail="Der Entwurf hat keinen Preis — bitte Preis angeben")
    min_price = data.min_price if data.min_price is not None else default_min_price(price)
    error = validate_activation(listing.platform, price, min_price)
    if error:
        raise HTTPException(status_code=400, detail=error)

    listed_at = data.listed_at or date.today()
    listing.status = ListingStatus.ACTIVE.value
    listing.url = url
    listing.current_price = price
    listing.min_price = min_price
    listing.listed_at = listed_at
    listing.next_check_at = compute_next_check(listed_at, listing.check_interval_days)
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.activated", item_id=item_id, platform=listing.platform, price=price, via="url")
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

    if data.min_price is not None and data.min_price != listing.min_price:
        # Neue Schmerzgrenze: der einmalige Hinweis darf wieder kommen.
        listing.floor_notified_at = None

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


# ── Anpassungsvorschlaege (Tages-Check, PR 3) ─────────────────────────


async def _get_open_suggestion(item_id: int, listing_id: int, session: AsyncSession) -> Listing:
    listing = await _get_listing(item_id, listing_id, session)
    if listing.status != ListingStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Vorschlaege gibt es nur fuer aktive Listings")
    if listing.suggested_price is None:
        raise HTTPException(status_code=400, detail="Kein offener Preisvorschlag")
    return listing


@router.post("/{item_id}/listings/{listing_id}/suggestion/accept", response_model=ListingResponse)
async def accept_suggestion(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """"Uebernommen": Sebastian hat den Preis in der Anzeige selbst geaendert.
    Setzt current_price, schreibt das Price-Change-Event, plant neu."""
    listing = await _get_open_suggestion(item_id, listing_id, session)
    new_price = listing.suggested_price
    if listing.min_price is not None and new_price < listing.min_price:
        raise HTTPException(status_code=400, detail="Vorschlag liegt unter der Schmerzgrenze")
    old_price = listing.current_price
    change = apply_price_change(listing, new_price, datetime.now(UTC))
    if change is not None:
        session.add(change)
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.suggestion_accepted", item_id=item_id, listing_id=listing_id, old=old_price, new=new_price)
    return to_listing_response(listing)


@router.post("/{item_id}/listings/{listing_id}/suggestion/snooze", response_model=ListingResponse)
async def snooze_listing_suggestion(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """"Spaeter": Vorschlag bleibt offen, 3 Tage keine Erinnerung."""
    listing = await _get_open_suggestion(item_id, listing_id, session)
    snooze_suggestion(listing, datetime.now(UTC))
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.suggestion_snoozed", item_id=item_id, listing_id=listing_id)
    return to_listing_response(listing)


@router.post("/{item_id}/listings/{listing_id}/suggestion/dismiss", response_model=ListingResponse)
async def dismiss_listing_suggestion(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    """"Verwerfen": Vorschlag weg, Preis bleibt, naechster Check in einem Intervall."""
    listing = await _get_open_suggestion(item_id, listing_id, session)
    dismiss_suggestion(listing, datetime.now(UTC))
    await session.commit()
    await session.refresh(listing)
    logger.info("listing.suggestion_dismissed", item_id=item_id, listing_id=listing_id)
    return to_listing_response(listing)


class DueListingEntry(BaseModel):
    item_id: int
    item_label: str
    condition: str | None
    days_online: int | None
    snoozed: bool
    listing: ListingResponse


class DueAdjustmentsResponse(BaseModel):
    # Zahl fuer das Menue-Badge: offene, nicht zurueckgestellte Vorschlaege plus Leichen.
    due_count: int
    suggestions: list[DueListingEntry]
    at_floor: list[DueListingEntry]
    sold_leftovers: list[DueListingEntry]


def build_due_overview(items, now: datetime) -> DueAdjustmentsResponse:
    suggestions: list[DueListingEntry] = []
    at_floor: list[DueListingEntry] = []
    leftovers: list[DueListingEntry] = []

    def entry(item, listing) -> DueListingEntry:
        return DueListingEntry(
            item_id=item.id,
            item_label=item_label(item),
            condition=item.condition,
            days_online=days_online(listing, now),
            snoozed=is_snoozed(listing, now),
            listing=to_listing_response(listing),
        )

    for item in items:
        for listing in item.listings:
            if item.status == InventoryStatus.SOLD.value:
                if listing.status in (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value):
                    leftovers.append(entry(item, listing))
                continue
            if item.status != InventoryStatus.HOLDING.value or listing.status != ListingStatus.ACTIVE.value:
                continue
            if listing.suggested_price is not None:
                suggestions.append(entry(item, listing))
            elif is_at_floor(listing):
                at_floor.append(entry(item, listing))

    suggestions.sort(key=lambda e: (e.snoozed, -(e.days_online or 0), e.listing.id))
    at_floor.sort(key=lambda e: (-(e.days_online or 0), e.listing.id))
    due_count = sum(1 for e in suggestions if not e.snoozed) + len(leftovers)
    return DueAdjustmentsResponse(
        due_count=due_count, suggestions=suggestions, at_floor=at_floor, sold_leftovers=leftovers
    )


@overview_router.get("/due", response_model=DueAdjustmentsResponse)
async def due_adjustments(session: AsyncSession = Depends(get_session)):
    """"Faellige Anpassungen": offene Vorschlaege, Listings an der Schmerzgrenze
    und verkaufte Posten mit laufender Anzeige."""
    result = await session.execute(
        select(InventoryItem).where(
            InventoryItem.status.in_((InventoryStatus.HOLDING.value, InventoryStatus.SOLD.value))
        )
    )
    return build_due_overview(result.scalars().all(), datetime.now(UTC))
