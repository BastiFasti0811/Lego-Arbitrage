"""Eigene Anzeigen (Listings) je Artikel und Plattform — siehe CONTEXT.md.

Nicht verwechseln mit `offers`: Das sind fremde Angebote der Arbitrage-
Pipeline. Ein Listing ist unsere eigene Anzeige, manuell eingestellt
(ADR 0002 — das System postet nie selbst).
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ListingPlatform(StrEnum):
    KLEINANZEIGEN = "KLEINANZEIGEN"
    EBAY = "EBAY"


class ListingStatus(StrEnum):
    DRAFT = "DRAFT"  # Text generiert, noch nicht eingestellt (ab PR 2 in Benutzung)
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"
    SOLD = "SOLD"


class PriceType(StrEnum):
    VB = "VB"
    FIXED = "FIXED"


OPEN_LISTING_STATUSES: tuple[str, ...] = (
    ListingStatus.DRAFT.value,
    ListingStatus.ACTIVE.value,
    ListingStatus.PAUSED.value,
)

_OPEN_STATUS_SQL = text("status IN ('DRAFT','ACTIVE','PAUSED')")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listings_item_id", "item_id"),
        # Genau ein offenes Listing je Artikel+Plattform; beendete Zeilen
        # bleiben als Historie stehen (Kleinanzeigen-Refresh = neue Zeile).
        Index(
            "uq_listings_open_per_platform",
            "item_id",
            "platform",
            unique=True,
            sqlite_where=_OPEN_STATUS_SQL,
            postgresql_where=_OPEN_STATUS_SQL,
        ),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    price_type: Mapped[str] = mapped_column(String(10), nullable=False)

    # Anzeigeninhalt (Texte kommen ab PR 2 aus der KI, bleiben hier editierbar)
    title: Mapped[str | None] = mapped_column(String(120))
    body: Mapped[str | None] = mapped_column(Text)
    platform_category: Mapped[str | None] = mapped_column(String(200))

    # Einstell-Daten
    listed_at: Mapped[date | None] = mapped_column(Date)
    current_price: Mapped[float | None] = mapped_column(Float)
    url: Mapped[str | None] = mapped_column(Text)

    # Anpassungsregel (Tages-Check nutzt sie ab PR 3)
    check_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="14")
    price_drop_percent: Mapped[float] = mapped_column(Float, nullable=False, server_default="10")
    min_price: Mapped[float | None] = mapped_column(Float)

    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    floor_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Offener Anpassungsvorschlag (NULL = keiner)
    suggested_price: Mapped[float | None] = mapped_column(Float)
    suggestion_reason: Mapped[str | None] = mapped_column(Text)
    suggestion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    item: Mapped["InventoryItem"] = relationship(back_populates="listings")
    price_changes: Mapped[list["ListingPriceChange"]] = relationship(
        back_populates="listing",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="ListingPriceChange.changed_at",
    )

    def __repr__(self) -> str:
        return f"<Listing item={self.item_id} {self.platform} {self.status}>"


class ListingPriceChange(Base):
    __tablename__ = "listing_price_changes"

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    old_price: Mapped[float] = mapped_column(Float, nullable=False)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)

    listing: Mapped["Listing"] = relationship(back_populates="price_changes")


from app.models.inventory import InventoryItem  # noqa: E402
