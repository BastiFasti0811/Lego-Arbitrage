"""Kaufentscheidung zu einem gemeldeten Angebot (Telegram-Knöpfe "Gekauft"/"Abgelehnt")."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DecisionKind(StrEnum):
    BOUGHT = "BOUGHT"
    REJECTED = "REJECTED"


class DecisionSource(StrEnum):
    TELEGRAM = "TELEGRAM"
    APP = "APP"


class OfferDecision(Base):
    """Eine Entscheidung je Inserat; ein zweiter Knopfdruck ändert sie.

    Schlüssel ist wie bei `DismissedOffer` die Identität aus `offer_identity()`,
    nicht die Offer-Zeile: die Dedupe-Bereinigung löscht Zeilen, die Entscheidung
    soll im Wochen-Digest trotzdem mitzählen. `offer_id` bleibt als Verweis,
    solange die Zeile lebt, und wird beim Löschen NULL.

    Setnummer, Preis und Empfehlung sind Kopien vom Zeitpunkt der Entscheidung.
    """

    __tablename__ = "offer_decisions"
    __table_args__ = (Index("ix_offer_decisions_decided_at", "decided_at"),)

    offer_identity: Mapped[str] = mapped_column(String(600), unique=True, index=True, nullable=False)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    offer_url: Mapped[str] = mapped_column(Text, nullable=False)
    set_number: Mapped[str | None] = mapped_column(String(20))
    price_eur: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str | None] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<OfferDecision {self.decision} {self.offer_identity}>"
