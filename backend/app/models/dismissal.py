"""Inserate, die der Nutzer nicht mehr im Feed sehen will."""

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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
