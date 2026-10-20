"""Inserat vorbereiten: ein Weg vom Inventarposten zum fertigen Entwurf.

Vorher gab es zwei Textwege nebeneinander: `/sell-links` kopierte einen
festen Kurztext ohne Listing, `/listings/draft` liess die KI schreiben. Jetzt
legt `prepare_listing_draft` immer eine DRAFT-Zeile an (oder nimmt die schon
vorhandene) und traegt Text und Preisvorschlag ein. Die feste Vorlage lebt nur
noch als Rueckfall, wenn die KI ausfaellt — dann mit sichtbarer Warnung.

ADR 0002 bleibt: hier wird nichts eingestellt, nur vorbereitet.
Kein Import aus den Routen-Modulen, sonst Zirkularimport.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import structlog

from app.ai import AIProviderError, ListingText, get_provider
from app.domain.condition import condition_ad_label, condition_ad_title_suffix
from app.models.inventory import InventoryItem, InventoryItemType, InventoryStatus
from app.models.listing import OPEN_LISTING_STATUSES, Listing, ListingPlatform, ListingStatus, PriceType
from app.services.listing_rules import default_price_type

logger = structlog.get_logger()

TITLE_MAX_LENGTH = Listing.__table__.c.title.type.length
# Laenge, die der alte Kleinanzeigen-/eBay-Kurztext schon benutzte.
TEMPLATE_TITLE_MAX_LENGTH = 80

_LEGO_PREFIX = re.compile(r"^\s*LEGO\s*®?\s*", re.IGNORECASE)


class TextSource(StrEnum):
    AI = "KI"  # frisch von der KI geschrieben
    TEMPLATE = "VORLAGE"  # KI ausgefallen, feste Vorlage
    EXISTING = "ENTWURF"  # vorhandener Entwurfstext (z. B. aus /lego-eingang) uebernommen


class ListingPrepareError(Exception):
    """Fachlicher Grund, warum kein Entwurf entsteht. Die Route macht daraus 400."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass
class PreparedDraft:
    listing: Listing
    created: bool
    text_source: TextSource
    warning: str | None = None


def listing_name(item: InventoryItem) -> str:
    """Artikelname fuer den KI-Prompt. Bei Lego gehoeren Marke und Setnummer in
    den Titel -- danach wird gesucht."""
    if item.item_type != InventoryItemType.LEGO.value or not item.set_number:
        return item.set_name
    if re.search(rf"\b{re.escape(item.set_number)}\b", item.set_name):
        return item.set_name
    return f"LEGO {item.set_number} {_LEGO_PREFIX.sub('', item.set_name)}"


def draft_price(item: InventoryItem, listing: Listing | None, body_price: float | None) -> float | None:
    """Preis-Fallback-Kette fuer Entwuerfe und Text-Refresh.

    Reihenfolge: expliziter Preis aus dem Request-Body, dann der laufende
    Anzeigenpreis eines schon eingestellten Listings (ACTIVE/PAUSED — beim
    Text-Refresh gibt es keinen Body-Preis), dann der Marktpreis der Position,
    dann die Mitte der KI-Preisspanne (nur wenn beide Grenzen bekannt sind),
    dann der 1,5-fache Einkaufspreis. None, wenn keine Quelle greift.
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


def template_listing_text(item: InventoryItem, *, price: float, price_type: str) -> ListingText:
    """Feste Vorlage, wenn die KI nicht schreibt. Behauptet nur, was in der
    Zeile steht: der Zustand kommt aus `item.condition`, nie fest verdrahtet."""
    title = listing_name(item) if item.set_number else item.set_name
    suffix = condition_ad_title_suffix(item.condition)
    if suffix:
        title = f"{title} {suffix}"
    if len(title) > TEMPLATE_TITLE_MAX_LENGTH:
        title = title[: TEMPLATE_TITLE_MAX_LENGTH - 3] + "..."

    lines = [f"LEGO Set {item.set_number} - {item.set_name}" if item.set_number else item.set_name]
    ad_label = condition_ad_label(item.condition)
    if ad_label:
        lines.append(f"Zustand: {ad_label}")
    price_suffix = " VB" if price_type == PriceType.VB.value else ""
    lines.append(f"Preis: {price:.0f}€{price_suffix}")
    if (item.quantity or 1) > 1:
        lines.append(f"{item.quantity} Stück vorhanden, der Preis gilt pro Stück.")
    body = "\n".join(lines) + "\n\nVersand möglich."
    return ListingText(title=title, body=body, platform_category="")


def open_listing_for(item: InventoryItem, platform: str) -> Listing | None:
    return next((x for x in item.listings if x.platform == platform and x.status in OPEN_LISTING_STATUSES), None)


def normalize_platform(platform: str) -> str:
    value = (platform or "").strip().upper()
    if value not in (p.value for p in ListingPlatform):
        raise ListingPrepareError(f"Unbekannte Plattform: {value}")
    return value


def check_item_can_be_listed(item: InventoryItem) -> None:
    if item.status == InventoryStatus.SOLD.value:
        raise ListingPrepareError("Verkaufte Artikel lassen sich nicht neu einstellen")
    if item.status == InventoryStatus.DRAFT.value:
        raise ListingPrepareError("Entwurf zuerst bestaetigen")


async def write_text(item: InventoryItem, *, platform: str, price: float, price_type: str) -> ListingText:
    """Nur die KI. Fehler gehen als AIProviderError an den Aufrufer."""
    provider = get_provider()
    return await provider.write_listing(
        name=listing_name(item),
        condition=item.condition,
        notes=item.notes,
        platform=platform,
        price=price,
        price_type=price_type,
        quantity=item.quantity,
    )


def apply_text(listing: Listing, text: ListingText) -> None:
    listing.title = text.title[:TITLE_MAX_LENGTH]
    listing.body = text.body
    listing.platform_category = text.platform_category or None


async def prepare_listing_draft(
    item: InventoryItem, platform: str, *, price: float | None = None
) -> PreparedDraft:
    """Legt den Entwurf an oder frischt den vorhandenen auf. Committet nicht.

    - Offenes ACTIVE/PAUSED-Listing auf der Plattform: Fehler, die Anzeige
      laeuft schon.
    - Vorhandener DRAFT mit Text: Text bleibt (kein bezahlter KI-Aufruf),
      nur der Preis wird gesetzt, falls er fehlt oder ausdruecklich kommt.
    - Sonst schreibt die KI; faellt sie aus, greift die Vorlage mit Warnung.
    """
    check_item_can_be_listed(item)
    platform = normalize_platform(platform)
    existing = open_listing_for(item, platform)
    if existing is not None and existing.status != ListingStatus.DRAFT.value:
        raise ListingPrepareError("Auf dieser Plattform läuft schon eine Anzeige")

    if price is not None and price <= 0:
        raise ListingPrepareError("Preis muss groesser 0 sein")
    existing_price = existing.current_price if existing is not None else None
    resolved_price = price if price is not None else existing_price or draft_price(item, None, None)
    if resolved_price is None:
        raise ListingPrepareError("Kein Preis ermittelbar — bitte Preis angeben")
    resolved_price = round(resolved_price, 2)

    created = existing is None
    if existing is not None:
        listing = existing
    else:
        listing = Listing(
            item_id=item.id,
            platform=platform,
            status=ListingStatus.DRAFT.value,
            price_type=default_price_type(platform),
            check_interval_days=14,
            price_drop_percent=10.0,
        )
    listing.current_price = resolved_price

    # Der Preis steht im Text. Kommt ein anderer Preis, waere der alte Text falsch.
    price_unchanged = existing_price is None or existing_price == resolved_price
    if listing.title and listing.body and price_unchanged:
        return PreparedDraft(listing=listing, created=created, text_source=TextSource.EXISTING)

    warning = None
    try:
        text = await write_text(item, platform=platform, price=resolved_price, price_type=listing.price_type)
        source = TextSource.AI
    except AIProviderError as exc:
        logger.warning("listing.prepare_ai_fallback", item_id=item.id, platform=platform, error=exc.detail)
        text = template_listing_text(item, price=resolved_price, price_type=listing.price_type)
        source = TextSource.TEMPLATE
        warning = f"KI-Text nicht verfügbar ({exc.detail}) — Vorlagentext verwendet"
    apply_text(listing, text)
    return PreparedDraft(listing=listing, created=created, text_source=source, warning=warning)


_PLATFORM_HOSTS = {
    ListingPlatform.KLEINANZEIGEN.value: ("kleinanzeigen.de",),
    ListingPlatform.EBAY.value: ("ebay.de", "ebay.com"),
}


def validate_listing_url(url: str, platform: str) -> str | None:
    """Fehlertext, wenn die eingefuegte URL nicht zur Plattform passt, sonst None.

    Faengt den haeufigsten Fehlgriff ab: Zwischenablage enthaelt noch den
    Anzeigentext statt der Adresse, oder die Adresse der anderen Plattform."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Bitte die vollständige Adresse der Anzeige einfügen (https://…)"
    host = parsed.hostname.lower()
    allowed = _PLATFORM_HOSTS.get(platform, ())
    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
        return f"Die Adresse gehört nicht zu {platform.title()} ({host})"
    return None
