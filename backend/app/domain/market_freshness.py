"""Wann ein gespeicherter Marktwert als veraltet gilt.

Der Bewertungslauf ueberschreibt `current_market_price` nur, wenn er einen
belastbaren Preis findet. Bei einem uebersprungenen Set bleibt der alte Wert
stehen. Am 21.10.2026 trugen so 8 von 49 Sets Marktwerte vom 20.08. bis
04.10., alle mit aktivem Sell-Signal. Die Oberflaeche zeigte sie wie frische
Werte.

Eine Regel fuer alle Leser: der Listing-Check (Marktanker der Preistreppe),
die Inventar-API (Altersmarke) und der Bewertungslauf (Sell-Signal) fragen
dieselbe Funktion.
"""

from datetime import UTC, datetime, timedelta

# Der Lauf kommt alle 6 Stunden. Sieben Tage ohne Bestaetigung heissen rund
# 28 Laeufe in Folge ohne belastbaren Preis fuer dieses Set.
MARKET_MAX_AGE_DAYS = 7


def _as_utc(value: datetime) -> datetime:
    # SQLite und `datetime.utcnow()` liefern naive Zeitstempel; gemeint ist UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_market_price_stale(updated_at: datetime | None, now: datetime) -> bool:
    """True, wenn der Wert aelter als MARKET_MAX_AGE_DAYS ist.

    Ohne Zeitstempel ist ein vorhandener Preis veraltet: niemand weiss, von
    wann er stammt. Ob ueberhaupt ein Preis da ist, prueft der Aufrufer.
    """
    if updated_at is None:
        return True
    return _as_utc(now) - _as_utc(updated_at) > timedelta(days=MARKET_MAX_AGE_DAYS)
