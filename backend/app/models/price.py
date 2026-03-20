"""Price records from various data sources."""

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PriceSource(str, Enum):
    """Data source for price information."""

    BRICKECONOMY = "BRICKECONOMY"
    BRICKMERGE = "BRICKMERGE"
    IDEALO = "IDEALO"
    EBAY_SOLD = "EBAY_SOLD"
    EBAY_ACTIVE = "EBAY_ACTIVE"
    KLEINANZEIGEN = "KLEINANZEIGEN"
    AMAZON = "AMAZON"
    LEGO_COM = "LEGO_COM"
    OTHER = "OTHER"


class PriceRecord(Base):
    """A single price data point from a specific source at a specific time.

    Used for building price history and calculating market consensus.
    """

    __tablename__ = "price_records"
    __table_args__ = (
        Index("ix_price_records_set_source", "set_id", "source"),
        Index("ix_price_records_scraped_at", "scraped_at"),
    )

    # ── Foreign Key ──────────────────────────────────────
    set_id: Mapped[int] = mapped_column(ForeignKey("lego_sets.id", ondelete="CASCADE"), nullable=False)

    # ── Price Data ───────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # PriceSource
    price_eur: Mapped[float] = mapped_column(Float, nullable=False)
    price_original: Mapped[float | None] = mapped_column(Float)  # If USD, original value
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    # ── Condition ────────────────────────────────────────
    condition: Mapped[str] = mapped_column(String(20), default="NEW_SEALED")  # NEW_SEALED, OPENED, USED

    # ── eBay-specific ────────────────────────────────────
    sold_count: Mapped[int | None] = mapped_column(Integer)  # Number of items in sample
    median_price: Mapped[float | None] = mapped_column(Float)  # Calculated median
    min_price: Mapped[float | None] = mapped_column(Float)
    max_price: Mapped[float | None] = mapped_column(Float)

    # ── Metadata ─────────────────────────────────────────
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_reliable: Mapped[bool] = mapped_column(default=True)  # False if data quality issues
    notes: Mapped[str | None] = mapped_column(Text)

    # ── Relationships ────────────────────────────────────
    lego_set: Mapped["LegoSet"] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return f"<PriceRecord set={self.set_id} source={self.source} price={self.price_eur}€>"


from app.models.set import LegoSet  # noqa: E402
