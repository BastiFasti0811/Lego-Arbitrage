"""Active offers found on marketplaces."""

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OfferPlatform(str, Enum):
    """Platform where the offer was found."""

    EBAY = "EBAY"
    KLEINANZEIGEN = "KLEINANZEIGEN"
    AMAZON = "AMAZON"
    CATAWIKI = "CATAWIKI"
    BRICKMERGE = "BRICKMERGE"  # Shop links via BrickMerge
    OTHER = "OTHER"


class OfferCondition(str, Enum):
    """Condition of the set in the offer."""

    NEW_SEALED = "NEW_SEALED"
    NEW_OPEN_BOX = "NEW_OPEN_BOX"
    USED_COMPLETE = "USED_COMPLETE"
    USED_INCOMPLETE = "USED_INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class OfferStatus(str, Enum):
    """Tracking status of the offer."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SOLD = "SOLD"
    FLAGGED = "FLAGGED"  # Manually flagged for review


class Offer(Base):
    """An active offer/listing found during scraping.

    This represents a buyable item on a marketplace.
    """

    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_set_platform", "set_id", "platform"),
        Index("ix_offers_status", "status"),
        Index("ix_offers_discovered", "discovered_at"),
    )

    # ── Foreign Key ──────────────────────────────────────
    set_id: Mapped[int] = mapped_column(ForeignKey("lego_sets.id", ondelete="CASCADE"), nullable=False)

    # ── Offer Details ────────────────────────────────────
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    offer_url: Mapped[str] = mapped_column(Text, nullable=False)
    offer_title: Mapped[str] = mapped_column(String(500), nullable=False)
    price_eur: Mapped[float] = mapped_column(Float, nullable=False)
    shipping_eur: Mapped[float | None] = mapped_column(Float)
    total_price_eur: Mapped[float | None] = mapped_column(Float)  # price + shipping

    # ── Condition ────────────────────────────────────────
    condition: Mapped[str] = mapped_column(String(20), default=OfferCondition.UNKNOWN.value)
    box_damage: Mapped[bool] = mapped_column(default=False)
    sealed: Mapped[bool] = mapped_column(default=True)

    # ── Seller Info ──────────────────────────────────────
    seller_name: Mapped[str | None] = mapped_column(String(200))
    seller_rating: Mapped[float | None] = mapped_column(Float)  # 0-100%
    seller_location: Mapped[str | None] = mapped_column(String(200))

    # ── Status ───────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default=OfferStatus.ACTIVE.value)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_auction: Mapped[bool] = mapped_column(default=False)
    auction_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Analysis Results (filled by engine) ──────────────
    estimated_roi: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    recommendation: Mapped[str | None] = mapped_column(String(20))  # GO, NO_GO, CHECK
    analysis_notes: Mapped[str | None] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(default=False)  # Has user been notified?

    # ── Relationships ────────────────────────────────────
    lego_set: Mapped["LegoSet"] = relationship(back_populates="offers")

    def __repr__(self) -> str:
        return f"<Offer {self.platform} set={self.set_id} price={self.price_eur}€ status={self.status}>"


from app.models.set import LegoSet  # noqa: E402
