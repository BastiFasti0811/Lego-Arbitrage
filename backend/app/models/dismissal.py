"""Inserate, die der Nutzer nicht mehr im Feed sehen will."""

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.offer_url import offer_identity
from app.models.base import Base


class DismissedOffer(Base):
    """Eine Abwahl — ein Inserat bleibt aus dem Live Feed draußen.

    Der Schlüssel ist die Identität aus ``offer_identity()``, nicht die
    Offer-Zeile: die Dedupe-Bereinigung löscht Zeilen, der nächste Scrape legt
    sie neu an. Eine ``offer_id`` überlebt das nicht, die Identität schon —
    und genau darum ging es, denn abgewählt wird gegen den nächsten Lauf.

    Titel, Setnummer und Preis sind Kopien vom Zeitpunkt der Abwahl. Ohne sie
    stünde in der Liste der Ausgeblendeten nur noch eine URL, sobald das
    Angebot vom Markt und damit die Offer-Zeile aus der DB ist.
    """

    __tablename__ = "dismissed_offers"

    offer_identity: Mapped[str] = mapped_column(String(600), unique=True, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    offer_url: Mapped[str] = mapped_column(Text, nullable=False)
    offer_title: Mapped[str | None] = mapped_column(String(500))
    set_number: Mapped[str | None] = mapped_column(String(20))
    price_eur: Mapped[float | None] = mapped_column(Float)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<DismissedOffer {self.offer_identity}>"


def dismissal_key(platform: str | None, offer_url: str | None) -> str:
    """The identity a dismissal is stored under and matched by.

    `offer_identity` is unbounded: `canonical_offer_url` returns unparseable
    input verbatim and keeps the full path for hosts it has no rule for, so a
    725-character identity against a 600-character column is reachable. That
    would make the ✕ answer 500.

    The clipping lives here, in one function both sides call, precisely because
    clipping only on write would be worse than the crash: the feed would go on
    computing the full identity, the stored key would never match, and the
    listing would sit in the dismissed list AND in the feed with nothing to
    explain it.
    """
    return offer_identity(platform, offer_url)[: DismissedOffer.__table__.c.offer_identity.type.length]
