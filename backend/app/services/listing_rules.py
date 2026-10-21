"""Reine Regel-Logik fuer Listings — ohne DB, damit direkt testbar.

Oben die Regeln des manuellen Lifecycles (PR 1), unten die des Tages-Checks
(PR 3): Preistreppe nach Alter, Marktabgleich, Rundung auf glatte Betraege
(unter 50 auf 1 EUR, ab 50 auf 5 EUR) und die Schmerzgrenze. Alles hier
schlaegt nur vor; ausgefuehrt wird von Hand (ADR 0002).
"""

import math
from dataclasses import dataclass
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
    # Ein offener Vorschlag bezog sich auf den alten Preis und ist damit erledigt.
    clear_suggestion(listing)
    # Wieder ueber der Schmerzgrenze: der naechste Hinweis darf wieder kommen.
    if listing.min_price is None or new_price > listing.min_price:
        listing.floor_notified_at = None
    return change


# ── Tages-Check (PR 3) ────────────────────────────────────────────────

SNOOZE_DAYS = 3  # "Spaeter" — fest verdrahtet (Spec)
# Der gespeicherte Marktpreis gilt nur als Anker, wenn er frisch ist ...
MARKET_MAX_AGE_DAYS = 7
# ... und deutlich unter der Treppe liegt; sonst bleibt es bei der Treppe.
MARKET_UNDERCUT_RATIO = 0.9


@dataclass(frozen=True)
class PriceSuggestion:
    price: float
    reason: str
    steps: int
    clamped_to_floor: bool = False


def _euro(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}".replace(".", ",")


def round_listing_price(value: float) -> float:
    """Glatte Anzeigenpreise: unter 50 EUR auf volle Euro, ab 50 EUR auf volle 5 EUR."""
    exact = Decimal(str(value))
    if exact < 50:
        return float(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return float((exact / 5).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 5)


def next_round_price_below(value: float) -> float | None:
    """Der naechste glatte Preis echt unter `value` (fuer kleine Preise, bei denen
    die Rundung den Abschlag wieder auffressen wuerde)."""
    candidate = math.ceil(value) - 1
    if candidate >= 50:
        candidate -= candidate % 5
    return float(candidate) if candidate > 0 else None


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite liefert naive Zeitstempel; alles hier rechnet in UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def last_price_anchor(listing) -> datetime | None:
    """Seit wann der aktuelle Preis steht: letzte Preisaenderung, sonst Einstell-Datum."""
    changes = [as_utc(change.changed_at) for change in (listing.price_changes or [])]
    if changes:
        return max(changes)
    if listing.listed_at is None:
        return None
    return datetime(listing.listed_at.year, listing.listed_at.month, listing.listed_at.day, tzinfo=UTC)


def staircase_steps(anchor: datetime, now: datetime, interval_days: int) -> int:
    """Preistreppe nach Alter: je volles Pruefintervall ohne Preisaenderung eine
    Stufe (Default 14/28/42 Tage -> 1/2/3). Mindestens eine Stufe, sobald der
    Check faellig ist; ein verworfener Vorschlag holt die Stufe beim naechsten
    Check nach."""
    days = (now - anchor).days
    return max(1, days // max(1, interval_days))


def is_at_floor(listing) -> bool:
    return (
        listing.min_price is not None
        and listing.current_price is not None
        and listing.current_price <= listing.min_price
    )


def is_snoozed(listing, now: datetime) -> bool:
    snoozed_until = as_utc(listing.snoozed_until)
    return snoozed_until is not None and snoozed_until > now


def compute_suggestion(
    *,
    current_price: float | None,
    min_price: float | None,
    drop_percent: float,
    steps: int,
    days_unchanged: int,
    market_price: float | None = None,
) -> PriceSuggestion | None:
    """Preisvorschlag fuer ein faelliges Listing, oder None.

    1. Treppe: current_price x (1 - drop_percent) je Stufe, glatt gerundet.
    2. Marktabgleich: liegt der zustandsbereinigte Marktpreis unter 90 % der
       Treppe, ist er der Vorschlag (die Begruendung nennt beide Zahlen).
    3. Nie unter der Schmerzgrenze, nie gleich oder ueber dem aktuellen Preis.
    """
    if current_price is None or current_price <= 0:
        return None
    floor = min_price or 0.0
    if current_price <= floor:
        return None  # an der Schmerzgrenze: dafuer gibt es den einmaligen Hinweis

    stair = current_price * (1 - drop_percent / 100) ** steps
    days_text = f"{days_unchanged} Tage ohne Preisänderung"
    if drop_percent > 0:
        price = round_listing_price(stair)
        if price >= current_price:
            price = next_round_price_below(current_price)
        step_text = "1 Stufe" if steps == 1 else f"{steps} Stufen"
        reason = f"{days_text}: Preistreppe {step_text} à {_euro(drop_percent)} %"
    else:
        price = None
        reason = days_text

    if market_price is not None and market_price > 0 and market_price < stair * MARKET_UNDERCUT_RATIO:
        price = round_listing_price(market_price)
        reason = (
            f"{days_text}; Marktpreis für diesen Zustand {_euro(market_price)} € "
            f"liegt unter der Preistreppe ({_euro(round_listing_price(stair))} €)"
        )

    if price is None:
        return None
    clamped = False
    if price < floor:
        price = floor
        clamped = True
        reason += f" — begrenzt auf die Schmerzgrenze {_euro(floor)} €"
    if price >= current_price:
        return None
    return PriceSuggestion(price=price, reason=reason, steps=steps, clamped_to_floor=clamped)


def set_suggestion(listing, suggestion: PriceSuggestion, now: datetime) -> None:
    listing.suggested_price = suggestion.price
    listing.suggestion_reason = suggestion.reason
    listing.suggestion_at = now
    listing.snoozed_until = None


def clear_suggestion(listing) -> None:
    listing.suggested_price = None
    listing.suggestion_reason = None
    listing.suggestion_at = None
    listing.snoozed_until = None


def snooze_suggestion(listing, now: datetime) -> None:
    """"Spaeter": Vorschlag bleibt offen, taucht 3 Tage lang nicht in Telegram auf."""
    listing.snoozed_until = now + timedelta(days=SNOOZE_DAYS)


def dismiss_suggestion(listing, now: datetime) -> None:
    """"Verwerfen": Vorschlag weg, naechster Check in einem Intervall. Der Preis
    bleibt, also holt die Treppe die Stufe dann nach."""
    clear_suggestion(listing)
    listing.next_check_at = now + timedelta(days=listing.check_interval_days)
