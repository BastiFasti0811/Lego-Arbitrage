"""Inventory model - tracks purchased LEGO sets for portfolio management."""

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Boolean, Date, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryStatus(str, Enum):
    HOLDING = "HOLDING"
    SOLD = "SOLD"


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_status", "status"),
        Index("ix_inventory_set_number", "set_number"),
    )

    # Purchase info
    set_number: Mapped[str] = mapped_column(String(20), nullable=False)
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(100))
    image_url: Mapped[str | None] = mapped_column(Text)
    buy_price: Mapped[float] = mapped_column(Float, nullable=False)
    buy_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_platform: Mapped[str | None] = mapped_column(String(100))
    buy_url: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str] = mapped_column(String(20), default="NEW_SEALED")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text)

    # Current valuation (auto-updated by Celery)
    current_market_price: Mapped[float | None] = mapped_column(Float)
    market_price_updated_at: Mapped[datetime | None] = mapped_column()
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

    def __repr__(self) -> str:
        return f"<InventoryItem {self.set_number} '{self.set_name}' {self.status}>"


from app.models.inventory_photo import InventoryPhoto  # noqa: E402
