"""Lego-Set aus dem Inventar per eBay Sell API einstellen, beenden, abgleichen.

Nachtrag ADR 0002 (Entscheidung Sebastian 25.10.2026, „alles wie es am
einfachsten ist“). Ablauf beim Einstellen eines eBay-Entwurfs:

1. Fotos zur Media API (EPS-Adressen; unsere Fotos sind nicht oeffentlich),
2. `createOrReplaceInventoryItem` mit Titel, Text, Zustand, Fotos, Menge,
3. Angebot anlegen oder aktualisieren (`createOffer`/`updateOffer`),
   Festpreis aus dem Entwurf (Zustandsabschlag steckt schon in
   `draft_price`), Geschaeftsrichtlinien, Lagerort,
4. `publishOffer` -> Listing ACTIVE mit Artikel-URL, Schmerzgrenze 70 %.

Danach: Preis aus der Preistreppe uebernehmen schiebt ihn zu eBay
(`push_price`), Beenden zieht das Angebot zurueck (`end_listing`), und der
Verkaufsabgleich (`sync_sales`, Celery) markiert auf eBay Verkauftes im
Inventar und meldet offene Anzeigen auf anderen Kanaelen.

Eingestellt wird nur auf Sebastians Klick (App) oder Befehl, nie automatisch.
Der Service committet nicht.
"""

from __future__ import annotations

import html
import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.domain.condition import normalize_condition
from app.engine.roi_calculator import calculate_ebay_fees
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingPlatform, ListingStatus
from app.models.listing_posting_job import ListingPostingJob, PostingJobStatus
from app.models.settings import AppSetting
from app.services.ebay_api import EbayClient, EbayError
from app.services.listing_prepare import ListingPrepareError, activate_draft, check_item_can_be_listed

logger = structlog.get_logger()

EBAY_TITLE_MAX = 80
EBAY_MAX_PHOTOS = 10  # export_photos liefert hoechstens so viele; eBay nimmt bis 24

# Inventory-API-ConditionEnum. LEGO-Komplettsets (19006) kennen Neu (1000),
# Neu: Sonstige (1500) und Gebraucht (3000 = USED_EXCELLENT). Unvollstaendige
# Sets laufen als Gebraucht mit Zustandsbeschreibung.
_EBAY_CONDITION = {
    "NEW_SEALED": ("NEW", None),
    "NEW_OPEN_BOX": ("NEW_OTHER", "Neu, Verpackung geöffnet."),
    "USED_COMPLETE": ("USED_EXCELLENT", "Gebraucht, vollständig."),
    "USED_INCOMPLETE": ("USED_EXCELLENT", "Gebraucht, nicht vollständig — Details siehe Beschreibung."),
}


# ── reine Regeln ────────────────────────────────────────────────────


def ebay_sku(item_id: int, listing_id: int, environment: str) -> str:
    """Eindeutig je Listing und Umgebung; Sandbox und Produktion kollidieren nie."""
    env = "P" if environment == "production" else "S"
    return f"LA-{env}-{item_id}-{listing_id}"


def ebay_title(title: str) -> str:
    title = " ".join((title or "").split())
    if len(title) <= EBAY_TITLE_MAX:
        return title
    cut = title[:EBAY_TITLE_MAX]
    space = cut.rfind(" ")
    if space >= EBAY_TITLE_MAX // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-")


def ebay_condition(condition: str | None) -> tuple[str, str | None]:
    key = normalize_condition(condition)
    if key not in _EBAY_CONDITION:
        raise EbayError("Zustand unbekannt — bitte am Posten setzen, bevor er auf eBay geht")
    return _EBAY_CONDITION[key]


def description_html(body: str) -> str:
    """Anzeigentext als schlichtes HTML: escapen, Absaetze und Zeilen erhalten."""
    paragraphs = [p.strip() for p in (body or "").strip().split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def check_publishable(listing: Listing, item: InventoryItem) -> None:
    if listing.platform != ListingPlatform.EBAY.value:
        raise EbayError("Nur eBay-Entwürfe lassen sich über die eBay-API einstellen")
    if listing.status != ListingStatus.DRAFT.value:
        raise EbayError("Nur ein Entwurf lässt sich einstellen")
    try:
        check_item_can_be_listed(item)
    except ListingPrepareError as exc:
        raise EbayError(exc.detail) from exc
    if item.item_type != InventoryItemType.LEGO.value or not item.set_number:
        raise EbayError("Über die eBay-API werden vorerst nur Lego-Sets mit Setnummer eingestellt")
    if not (listing.title or "").strip() or not (listing.body or "").strip():
        raise EbayError("Der Entwurf braucht Titel und Text")
    if not listing.current_price or listing.current_price <= 0:
        raise EbayError("Der Entwurf braucht einen Preis")
    if not item.photos:
        raise EbayError("eBay verlangt mindestens ein Foto")
    ebay_condition(item.condition)


def build_inventory_item(item: InventoryItem, listing: Listing, image_urls: list[str]) -> dict:
    condition, condition_text = ebay_condition(item.condition)
    aspects = {"Marke": ["LEGO"], "Produktart": ["Komplettset"], "LEGO Set-Nummer": [item.set_number]}
    if item.theme:
        aspects["LEGO Themenwelt"] = [item.theme]
    body: dict = {
        "product": {
            "title": ebay_title(listing.title),
            "description": description_html(listing.body),
            "brand": "LEGO",
            "mpn": item.set_number,
            "aspects": aspects,
            "imageUrls": image_urls,
        },
        "condition": condition,
        "availability": {"shipToLocationAvailability": {"quantity": max(1, item.quantity or 1)}},
    }
    if condition_text:
        body["conditionDescription"] = condition_text
    return body


@dataclass(frozen=True)
class PolicyIds:
    fulfillment: str
    payment: str
    returns: str


def build_offer(item: InventoryItem, listing: Listing, sku: str, policies: PolicyIds) -> dict:
    return {
        "sku": sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, item.quantity or 1),
        "categoryId": settings.ebay_lego_category_id,
        "listingDescription": description_html(listing.body),
        "listingDuration": "GTC",
        "merchantLocationKey": settings.ebay_location_key,
        "pricingSummary": {"price": {"value": f"{listing.current_price:.2f}", "currency": "EUR"}},
        "listingPolicies": {
            "fulfillmentPolicyId": policies.fulfillment,
            "paymentPolicyId": policies.payment,
            "returnPolicyId": policies.returns,
        },
    }


# ── Richtlinien ─────────────────────────────────────────────────────


async def resolve_policies(client: EbayClient) -> PolicyIds:
    """IDs aus der .env, sonst die Richtlinie namens `ebay_policy_name`, sonst
    die einzige vorhandene. Mehrdeutig oder keine: klare Fehlermeldung."""
    configured = {
        "fulfillment": settings.ebay_fulfillment_policy_id,
        "payment": settings.ebay_payment_policy_id,
        "return": settings.ebay_return_policy_id,
    }
    resolved: dict[str, str] = {}
    for kind, value in configured.items():
        if value:
            resolved[kind] = value
            continue
        policies = await client.list_policies(kind)
        named = [p for p in policies if p.get("name") == settings.ebay_policy_name]
        candidates = named or (policies if len(policies) == 1 else [])
        if len(candidates) != 1:
            raise EbayError(
                f"eBay-{kind}-Richtlinie nicht eindeutig ({len(policies)} vorhanden) — "
                f"`python -m app.tools.ebay_setup policies` ausführen oder ID in der .env setzen"
            )
        resolved[kind] = str(candidates[0][f"{kind}PolicyId"])
    return PolicyIds(fulfillment=resolved["fulfillment"], payment=resolved["payment"], returns=resolved["return"])


# ── Einstellen, Preis, Beenden ──────────────────────────────────────


async def upload_photos(client: EbayClient, item: InventoryItem, photo_root: Path) -> list[str]:
    # Gleiche Aufbereitung wie fuer Kleinanzeigen: verkleinert, ohne EXIF (Aufnahmeort).
    from app.services.ka_posting import export_photos

    with tempfile.TemporaryDirectory(prefix="ebay-photos-") as tmp:
        paths = export_photos(item, photo_root, Path(tmp) / "photos")[:EBAY_MAX_PHOTOS]
        if not paths:
            raise EbayError("Keine Fotodatei gefunden — Fotos am Posten prüfen")
        return [await client.upload_image(path.read_bytes(), path.name) for path in paths]


async def publish_listing(
    listing: Listing, item: InventoryItem, client: EbayClient, *, photo_root: Path, today: date | None = None
) -> str:
    """eBay-Entwurf einstellen. Gibt die Artikel-URL zurueck.

    Setzt SKU und Angebots-ID, sobald sie feststehen: scheitert ein spaeterer
    Schritt, speichert die Route sie trotzdem, und der naechste Versuch nimmt
    dasselbe Angebot statt ein zweites anzulegen."""
    check_publishable(listing, item)
    policies = await resolve_policies(client)
    if not listing.ebay_sku:
        listing.ebay_sku = ebay_sku(item.id, listing.id, client.config.environment)
    sku = listing.ebay_sku

    image_urls = await upload_photos(client, item, photo_root)
    await client.put_inventory_item(sku, build_inventory_item(item, listing, image_urls))

    offer = build_offer(item, listing, sku, policies)
    offer_id = listing.ebay_offer_id or await client.find_offer_id(sku)
    if offer_id:
        await client.update_offer(offer_id, offer)
    else:
        offer_id = await client.create_offer(offer)
    listing.ebay_offer_id = offer_id

    ebay_item_id = await client.publish_offer(offer_id)
    url = client.item_url(ebay_item_id)
    listing.ebay_listing_id = ebay_item_id
    try:
        activate_draft(listing, item, url=url, listed_at=today)
    except ListingPrepareError as exc:  # pragma: no cover - check_publishable deckt das vorher ab
        raise EbayError(exc.detail) from exc
    logger.info("ebay.published", item_id=item.id, listing_id=listing.id, ebay_item_id=ebay_item_id)
    return url


def is_api_listing(listing: Listing) -> bool:
    return listing.platform == ListingPlatform.EBAY.value and bool(listing.ebay_offer_id and listing.ebay_sku)


async def push_price(listing: Listing, item: InventoryItem, new_price: float, client: EbayClient) -> None:
    """Neuen Preis eines aktiven API-Listings auf eBay setzen (vor dem Commit)."""
    if not is_api_listing(listing) or listing.status != ListingStatus.ACTIVE.value:
        return
    await client.update_price(
        sku=listing.ebay_sku, offer_id=listing.ebay_offer_id, price=new_price, quantity=max(1, item.quantity or 1)
    )
    logger.info("ebay.price_pushed", listing_id=listing.id, price=new_price)


async def end_listing(listing: Listing, client: EbayClient) -> None:
    """Angebot auf eBay zurueckziehen; das Listing bleibt als Historie (ENDED)."""
    if not is_api_listing(listing):
        raise EbayError("Dieses Listing wurde nicht über die eBay-API eingestellt")
    if listing.status not in (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value):
        raise EbayError("Nur aktive eBay-Angebote lassen sich beenden")
    await client.withdraw_offer(listing.ebay_offer_id)
    listing.status = ListingStatus.ENDED.value
    logger.info("ebay.withdrawn", listing_id=listing.id)


# ── Verkaufsabgleich ────────────────────────────────────────────────


@dataclass
class SaleNotice:
    item_label: str
    price: float
    open_elsewhere: list[tuple[str, str | None]]  # (Plattform, URL)


@dataclass
class SyncResult:
    orders_seen: int = 0
    sold: list[SaleNotice] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)  # Teilverkaeufe, von Hand aufteilen
    withdrawn: int = 0  # auf anderem Kanal verkauft, eBay-Angebot zurueckgezogen
    errors: list[str] = field(default_factory=list)  # nur Log und Task-Ergebnis, keine Meldung


# Schon gemeldete Teilverkaeufe (SKU + Bestellnummern), damit Telegram nicht
# alle 30 Minuten dasselbe meldet. Eine neue Bestellung zum selben Posten
# ergibt einen neuen Schluessel und damit eine neue Meldung.
SETTING_REPORTED_PARTIALS = "ebay_reported_partials"
_REPORTED_KEEP = 200


async def _reported_partials(session: AsyncSession) -> tuple[AppSetting | None, list[str]]:
    query = select(AppSetting).where(AppSetting.key == SETTING_REPORTED_PARTIALS)
    row = (await session.execute(query)).scalar_one_or_none()
    try:
        keys = json.loads(row.value) if row and row.value else []
    except ValueError:
        keys = []
    return row, keys if isinstance(keys, list) else []


def _order_cancelled(order: dict) -> bool:
    state = (order.get("cancelStatus") or {}).get("cancelState")
    return state in ("CANCELED", "IN_PROGRESS")


def _amount(value: dict | None) -> float | None:
    try:
        return float((value or {}).get("value"))
    except (TypeError, ValueError):
        return None


def _order_date(order: dict) -> date:
    raw = order.get("creationDate") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()


def apply_ebay_sale(item: InventoryItem, listing: Listing, price: float, sold_on: date) -> list[Listing]:
    """Posten und eBay-Listing als verkauft markieren, wie „Verkauft“ in der App.

    Gibt die offenen Listings auf anderen Kanaelen zurueck (Loesch-Checkliste)."""
    item.status = InventoryStatus.SOLD.value
    item.sell_price = round(price, 2)
    item.sell_date = sold_on
    item.sell_platform = "eBay"
    item.sell_signal_active = False
    listing.status = ListingStatus.SOLD.value
    if item.buy_price is not None:
        invested = item.buy_price + (item.buy_shipping or 0)
        profit = price - invested - sum(calculate_ebay_fees(price))
        item.realized_profit = round(profit, 2)
        item.realized_roi_percent = round(profit / invested * 100, 1) if invested > 0 else 0
    return [x for x in item.listings if x is not listing and x.status in OPEN_LISTING_STATUSES]


async def _cancel_open_posting_jobs(session: AsyncSession, listing_ids: list[int], now: datetime) -> None:
    if not listing_ids:
        return
    rows = await session.execute(
        select(ListingPostingJob).where(
            ListingPostingJob.listing_id.in_(listing_ids),
            ListingPostingJob.status.in_((PostingJobStatus.REQUESTED.value, PostingJobStatus.APPROVED.value)),
        )
    )
    for job in rows.scalars():
        job.status = PostingJobStatus.CANCELLED.value
        job.finished_at = now
        job.error = "Auf eBay verkauft"


def _label(item: InventoryItem) -> str:
    return f"{item.set_number} {item.set_name}" if item.set_number else item.set_name


async def sync_sales(session: AsyncSession, client: EbayClient, now: datetime | None = None) -> SyncResult:
    """Bestellungen der letzten Tage gegen die API-Listings abgleichen.

    Idempotent: es zaehlen nur Listings, die noch ACTIVE/PAUSED sind. Danach in
    die Gegenrichtung: ist ein Posten anderswo verkauft (Status SOLD), aber sein
    eBay-Angebot laeuft noch, wird es zurueckgezogen."""
    now = now or datetime.now(UTC)
    result = SyncResult()
    rows = await session.execute(
        select(Listing)
        .where(
            Listing.platform == ListingPlatform.EBAY.value,
            Listing.ebay_sku.is_not(None),
            Listing.status.in_((ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value)),
        )
        .options(selectinload(Listing.item))
    )
    by_sku = {listing.ebay_sku: listing for listing in rows.scalars()}
    if not by_sku:
        return result

    since = (now - timedelta(days=settings.ebay_order_lookback_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    orders = await client.orders_modified_since(since)
    result.orders_seen = len(orders)
    sold_qty: dict[str, int] = {}
    order_ids: dict[str, set[str]] = {}
    sale_info: dict[str, tuple[float, date]] = {}
    for order in orders:
        if _order_cancelled(order):
            continue
        for line in order.get("lineItems") or []:
            sku = line.get("sku")
            if sku not in by_sku:
                continue
            qty = int(line.get("quantity") or 1)
            sold_qty[sku] = sold_qty.get(sku, 0) + qty
            order_ids.setdefault(sku, set()).add(str(order.get("orderId")))
            unit = (_amount(line.get("lineItemCost")) or _amount(line.get("total")) or 0.0) / max(1, qty)
            sale_info[sku] = (unit, _order_date(order))

    reported_row, reported = await _reported_partials(session)
    newly_reported: list[str] = []
    for sku, qty in sold_qty.items():
        listing = by_sku[sku]
        item = listing.item
        if item.status == InventoryStatus.SOLD.value:
            listing.status = ListingStatus.SOLD.value
            continue
        if qty < (item.quantity or 1):
            key = f"{sku}|{','.join(sorted(order_ids[sku]))}"
            if key not in reported:
                result.partial.append(f"{_label(item)}: {qty} von {item.quantity} verkauft")
                newly_reported.append(key)
            continue
        price, sold_on = sale_info[sku]
        others = apply_ebay_sale(item, listing, price, sold_on)
        await _cancel_open_posting_jobs(session, [x.id for x in others], now)
        result.sold.append(SaleNotice(_label(item), price, [(x.platform, x.url) for x in others]))
        logger.info("ebay.sold", item_id=item.id, listing_id=listing.id, price=price, open_elsewhere=len(others))

    if newly_reported:
        value = json.dumps((reported + newly_reported)[-_REPORTED_KEEP:])
        if reported_row is None:
            # "internal": Zustand, keine Einstellung; die Einstellungsseite blendet ihn aus.
            session.add(AppSetting(key=SETTING_REPORTED_PARTIALS, value=value, category="internal"))
        else:
            reported_row.value = value

    for listing in by_sku.values():
        if listing.item.status != InventoryStatus.SOLD.value or listing.status not in OPEN_LISTING_STATUSES:
            continue
        try:
            await end_listing(listing, client)
            result.withdrawn += 1
        except EbayError as exc:
            result.errors.append(f"{_label(listing.item)}: {exc.detail}")
            logger.warning("ebay.withdraw_failed", listing_id=listing.id, error=exc.detail)
    return result


def format_sync_message(result: SyncResult) -> str | None:
    """Telegram-Text, nur wenn es etwas zu tun gibt."""
    lines: list[str] = []
    for sale in result.sold:
        price = f"{sale.price:.2f}".replace(".", ",")
        lines.append(f"✅ Auf eBay verkauft: {sale.item_label} für {price} €")
        for platform, url in sale.open_elsewhere:
            target = url or "ohne Adresse"
            lines.append(f"   ↳ {platform.title()}-Anzeige von Hand löschen: {target}")
    for text in result.partial:
        lines.append(f"🟡 Teilverkauf auf eBay: {text} — Posten in der App aufteilen")
    return "\n".join(lines) if lines else None
