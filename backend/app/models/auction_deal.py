"""Auktionslose mit Gebotsfreigabe — die Auktionshaelfte des Deal-Zaehlers.

`auction_scan_states` haelt nur das letzte Scan-Ergebnis und eine URL-Liste
ohne Zeitstempel. Fuer "gepruefte Deals pro Woche" (Memory projekt-ziel)
braucht es den Zeitpunkt, an dem ein Los zum ersten Mal freigegeben war:
eine Zeile je Los, spaetere Scans aendern sie nicht.

Freigabe heisst `can_bid_now`: Konsens-Marktpreis, bekannter Versand und
Zustand, Gebot unter dem Maximalgebot. NEEDS_REVIEW (etwa nur BrickMerge)
kommt hier nie an.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuctionDeal(Base):
    __tablename__ = "auction_deals"
    __table_args__ = (
        UniqueConstraint("platform", "source_url", name="uq_auction_deals_platform_url"),
        Index("ix_auction_deals_found_at", "found_at"),
    )

    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    set_number: Mapped[str | None] = mapped_column(String(20))
    lot_title: Mapped[str | None] = mapped_column(String(500))
    current_bid: Mapped[float | None] = mapped_column(Float)
    max_bid: Mapped[float | None] = mapped_column(Float)
    expected_profit: Mapped[float | None] = mapped_column(Float)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<AuctionDeal {self.platform} {self.set_number} {self.found_at:%Y-%m-%d}>"
