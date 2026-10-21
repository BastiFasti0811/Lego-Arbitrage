"""Tages-Check der eigenen Listings — die DB-freie Planung.

Der Celery-Task (`app.tasks.listing_check`) laedt die Inventarposten samt
Listings, ruft `plan_listing_check` auf und verschickt das Ergebnis. Alles hier
arbeitet auf bereits geladenen Objekten, damit es ohne Datenbank testbar ist.

Was der Check tut (Spec `docs/superpowers/specs/2026-08-30-inventar-fuer-alles-design.md`):
- faellige ACTIVE-Listings ohne offenen Vorschlag bekommen einen Preisvorschlag
  (Preistreppe nach Alter, Marktabgleich nach Zustand, nie unter der Schmerzgrenze),
- ein Listing an der Schmerzgrenze bekommt genau einen Hinweis,
- verkaufte Posten mit noch laufender Anzeige werden angemahnt.

Nichts davon aendert eine Anzeige oder einen Preis (ADR 0002).
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.market_freshness import is_market_price_stale
from app.engine.market_consensus import PriceBasis
from app.models.inventory import InventoryStatus
from app.models.listing import ListingStatus
from app.services.listing_prepare import condition_market_price
from app.services.listing_rules import (
    as_utc,
    compute_suggestion,
    is_at_floor,
    is_snoozed,
    last_price_anchor,
    set_suggestion,
    staircase_steps,
)

# Nur Marktpreise, die auf mehr als einer Haendlerliste beruhen: BrickMerge
# allein ist oft der EOL-Haendlerpreis und kein Verkaufspreis.
_TRUSTED_MARKET_BASES = (PriceBasis.CONSENSUS, PriceBasis.EBAY_SOLD)
_ONLINE = (ListingStatus.ACTIVE.value, ListingStatus.PAUSED.value)


@dataclass
class CheckEntry:
    """Eine Zeile der Sammelnachricht und der Uebersicht."""

    item_id: int
    listing_id: int
    label: str
    platform: str
    url: str | None
    current_price: float | None
    min_price: float | None = None
    suggested_price: float | None = None
    reason: str | None = None
    days_online: int | None = None
    is_new: bool = False


@dataclass
class CheckPlan:
    checked: int = 0
    new_suggestions: int = 0
    suggestions: list[CheckEntry] = field(default_factory=list)  # neue + weiter offene, nicht gesnoozt
    floor_hints: list[CheckEntry] = field(default_factory=list)  # erstmals an der Schmerzgrenze
    sold_leftovers: list[CheckEntry] = field(default_factory=list)  # verkauft, Anzeige laeuft noch
    snoozed: int = 0
    # Listings, deren floor_notified_at dieser Lauf gesetzt hat. Kommt die
    # Nachricht nicht an, setzt der Task sie zurueck, damit der Hinweis nicht verloren geht.
    floor_listings: list = field(default_factory=list)

    @property
    def has_news(self) -> bool:
        return bool(self.suggestions or self.floor_hints or self.sold_leftovers)


def item_label(item) -> str:
    if item.set_number and item.set_number not in (item.set_name or ""):
        return f"{item.set_number} {item.set_name}"
    return item.set_name


def days_online(listing, now: datetime) -> int | None:
    if listing.listed_at is None:
        return None
    return max(0, (now.date() - listing.listed_at).days)


def fresh_market_price(item, now: datetime) -> float | None:
    """Zustandsbereinigter Marktpreis, wenn er belastbar und hoechstens 7 Tage alt ist."""
    if item.market_price_basis not in _TRUSTED_MARKET_BASES:
        return None
    if is_market_price_stale(item.market_price_updated_at, now):
        return None
    return condition_market_price(item)


def is_due(listing, now: datetime) -> bool:
    next_check_at = as_utc(listing.next_check_at)
    return (
        listing.status == ListingStatus.ACTIVE.value
        and next_check_at is not None
        and next_check_at <= now
        and listing.suggested_price is None
        and not is_snoozed(listing, now)
    )


def _entry(item, listing, now: datetime, *, is_new: bool = False) -> CheckEntry:
    return CheckEntry(
        item_id=item.id,
        listing_id=listing.id,
        label=item_label(item),
        platform=listing.platform,
        url=listing.url,
        current_price=listing.current_price,
        min_price=listing.min_price,
        suggested_price=listing.suggested_price,
        reason=listing.suggestion_reason,
        days_online=days_online(listing, now),
        is_new=is_new,
    )


def suggest_for_listing(item, listing, now: datetime) -> bool:
    """Schreibt einen Vorschlag an das Listing, wenn die Regeln einen ergeben."""
    anchor = last_price_anchor(listing)
    if anchor is None:
        return False
    steps = staircase_steps(anchor, now, listing.check_interval_days)
    suggestion = compute_suggestion(
        current_price=listing.current_price,
        min_price=listing.min_price,
        drop_percent=listing.price_drop_percent,
        steps=steps,
        days_unchanged=max(0, (now - anchor).days),
        market_price=fresh_market_price(item, now),
    )
    if suggestion is None:
        return False
    set_suggestion(listing, suggestion, now)
    return True


def plan_listing_check(items, now: datetime) -> CheckPlan:
    """Ein Lauf ueber alle Posten. Aendert nur die Vorschlags- und Hinweisfelder."""
    plan = CheckPlan()
    for item in items:
        if item.status == InventoryStatus.SOLD.value:
            for listing in item.listings:
                if listing.status in _ONLINE:
                    plan.sold_leftovers.append(_entry(item, listing, now))
            continue
        if item.status != InventoryStatus.HOLDING.value:
            continue

        for listing in item.listings:
            if listing.status != ListingStatus.ACTIVE.value:
                continue
            if is_due(listing, now):
                plan.checked += 1
                if is_at_floor(listing):
                    if listing.floor_notified_at is None:
                        listing.floor_notified_at = now
                        plan.floor_listings.append(listing)
                        plan.floor_hints.append(_entry(item, listing, now))
                    continue
                if suggest_for_listing(item, listing, now):
                    plan.new_suggestions += 1
                    plan.suggestions.append(_entry(item, listing, now, is_new=True))
                continue
            if listing.suggested_price is not None:
                if is_snoozed(listing, now):
                    plan.snoozed += 1
                else:
                    plan.suggestions.append(_entry(item, listing, now))
    plan.suggestions.sort(key=lambda entry: (not entry.is_new, -(entry.days_online or 0), entry.listing_id))
    return plan


def revert_floor_hints(plan: CheckPlan) -> None:
    """Nachricht nicht angekommen: Hinweis beim naechsten Lauf erneut versuchen."""
    for listing in plan.floor_listings:
        listing.floor_notified_at = None
