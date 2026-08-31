"""Reine Regel-Logik fuer Listings — ohne DB, damit direkt testbar.

Preisvorschlags-Rundung auf glatte Betraege (unter 50 auf 1, darueber
auf 5) lebt erst im Tages-Check (PR 3); hier stehen die Regeln, die der
manuelle Lifecycle aus PR 1 braucht.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.models.listing import ListingPlatform, ListingPriceChange, PriceType


def default_min_price(price: float) -> float:
    """Schmerzgrenzen-Vorbefuellung: 70 % vom Startpreis, kaufmaennisch gerundet (Grill-Entscheid Q5)."""
    return float((Decimal(str(price)) * Decimal("0.7")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def default_price_type(platform: str) -> str:
    return PriceType.VB.value if platform == ListingPlatform.KLEINANZEIGEN.value else PriceType.FIXED.value


def compute_next_check(listed_at: date, interval_days: int) -> datetime:
    """Faelligkeit zaehlt ab dem Einstell-Datum, auch bei nachtraeglicher Erfassung."""
    return datetime(listed_at.year, listed_at.month, listed_at.day, tzinfo=UTC) + timedelta(days=interval_days)


def validate_activation(platform: str, price: float | None, min_price: float | None) -> str | None:
    if platform not in (p.value for p in ListingPlatform):
        return f"Unbekannte Plattform: {platform}"
    if not price or price <= 0:
        return "Preis muss groesser 0 sein"
    if min_price is None or min_price <= 0:
        return "Schmerzgrenze (min_price) ist Pflicht"
    if min_price > price:
        return "Schmerzgrenze liegt ueber dem Startpreis"
    return None


def apply_price_change(listing, new_price: float, now: datetime) -> ListingPriceChange | None:
    """Preisaenderung = Anpassung passiert: Event schreiben, Check neu planen."""
    if listing.current_price == new_price:
        return None
    change = ListingPriceChange(
        listing_id=listing.id,
        changed_at=now,
        old_price=listing.current_price,
        new_price=new_price,
    )
    listing.current_price = new_price
    listing.next_check_at = now + timedelta(days=listing.check_interval_days)
    return change
