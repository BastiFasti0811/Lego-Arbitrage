"""Inventory model - tracks purchased LEGO sets for portfolio management."""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryStatus(StrEnum):
    HOLDING = "HOLDING"
    SOLD = "SOLD"


class InventoryItemType(StrEnum):
    LEGO = "LEGO"
    GENERIC = "GENERIC"


LEGO_PRODUCT_GROUP = "Lego"

# Startliste fuer das Warengruppen-Dropdown; per Freitext erweiterbar,
# gespeicherte Werte kommen per DISTINCT dazu (siehe /product-groups).
PRODUCT_GROUP_SUGGESTIONS = [LEGO_PRODUCT_GROUP, "Elektronik", "Kleidung", "Haushalt", "Spielzeug", "Diverses"]


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_status", "status"),
        Index("ix_inventory_set_number", "set_number"),
    )

    # Purchase info
    # GENERIC-Artikel haben keine Set-Nummer; LEGO-Anlage erzwingt sie im Schema.
    set_number: Mapped[str | None] = mapped_column(String(20))
    item_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=InventoryItemType.LEGO.value)
    product_group: Mapped[str] = mapped_column(String(100), nullable=False, server_default=LEGO_PRODUCT_GROUP)
    # eBay-Suchbegriff der Preisrecherche; bei LEGO automatisch "LEGO {set_number}".
    search_query: Mapped[str | None] = mapped_column(String(300))
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(100))
    image_url: Mapped[str | None] = mapped_column(Text)
    # Dachbodenfunde haben keinen Kaufpreis; Rechnungen ueberspringen sie dann.
    buy_price: Mapped[float | None] = mapped_column(Float)
    buy_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_platform: Mapped[str | None] = mapped_column(String(100))
    buy_url: Mapped[str | None] = mapped_column(Text)
    # Nachschlage-Link, den der Nutzer selbst setzt. BrickMerge und Idealo
    # werden aus der Setnummer erzeugt und brauchen keinen Speicher.
    reference_url: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str] = mapped_column(String(20), default="NEW_SEALED")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text)

    # Current valuation (auto-updated by Celery)
    current_market_price: Mapped[float | None] = mapped_column(Float)
    market_price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unrealized_profit: Mapped[float | None] = mapped_column(Float)
    unrealized_roi_percent: Mapped[float | None] = mapped_column(Float)

    # Sell signal
    sell_signal_active: Mapped[bool] = mapped_column(Boolean, default=False)
    sell_signal_reason: Mapped[str | None] = mapped_column(Text)

    # Status & sale info
    status: Mapped[str] = mapped_column(String(20), default=InventoryStatus.HOLDING.value)
    sell_price: Mapped[float | None] = mapped_column(Float)
    sell_date: Mapped[date | None] = mapped_column(Date)
    sell_platform: Mapped[str | None] = mapped_column(String(100))
    realized_profit: Mapped[float | None] = mapped_column(Float)
    realized_roi_percent: Mapped[float | None] = mapped_column(Float)
    photos: Mapped[list["InventoryPhoto"]] = relationship(
        back_populates="item",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="InventoryPhoto.sort_order",
    )
    listings: Mapped[list["Listing"]] = relationship(
        back_populates="item",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Listing.created_at",
    )

    def __repr__(self) -> str:
        return f"<InventoryItem {self.set_number} '{self.set_name}' {self.status}>"


from app.models.inventory_photo import InventoryPhoto  # noqa: E402
from app.models.listing import Listing  # noqa: E402
