"""Watch concrete auction lots and recalculate bid ceilings regularly."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuctionWatchStatus(str):
    ACTIVE = "ACTIVE"
    OVER_LIMIT = "OVER_LIMIT"
    ENDED = "ENDED"
    ARCHIVED = "ARCHIVED"


class AuctionWatchItem(Base):
    """A concrete auction lot the user wants to monitor."""

    __tablename__ = "auction_watch_items"
    __table_args__ = (
        Index("ix_auction_watch_items_active", "is_active"),
        Index("ix_auction_watch_items_status", "status"),
        Index("ix_auction_watch_items_platform", "source_platform"),
        Index("ix_auction_watch_items_last_checked", "last_checked_at"),
    )

    set_id: Mapped[int] = mapped_column(ForeignKey("lego_sets.id", ondelete="CASCADE"), nullable=False)

    source_platform: Mapped[str] = mapped_column(String(30), nullable=False, default="CATAWIKI")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    lot_title: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    current_bid: Mapped[float] = mapped_column(Float, nullable=False)
    purchase_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    desired_roi_percent: Mapped[float | None] = mapped_column(Float)
    buyer_fee_rate: Mapped[float | None] = mapped_column(Float)
    buyer_fee_fixed: Mapped[float | None] = mapped_column(Float)
    fee_applies_to_shipping: Mapped[bool] = mapped_column(default=False)

    max_bid: Mapped[float | None] = mapped_column(Float)
    break_even_bid: Mapped[float | None] = mapped_column(Float)
    current_bid_gap: Mapped[float | None] = mapped_column(Float)
    expected_roi_at_current_bid: Mapped[float | None] = mapped_column(Float)
    expected_profit_at_current_bid: Mapped[float | None] = mapped_column(Float)
    expected_roi_at_max_bid: Mapped[float | None] = mapped_column(Float)
    expected_profit_at_max_bid: Mapped[float | None] = mapped_column(Float)
    total_purchase_cost_at_current_bid: Mapped[float | None] = mapped_column(Float)
    total_purchase_cost_at_max_bid: Mapped[float | None] = mapped_column(Float)
    buyer_fee_at_current_bid: Mapped[float | None] = mapped_column(Float)
    buyer_fee_at_max_bid: Mapped[float | None] = mapped_column(Float)
    market_price: Mapped[float | None] = mapped_column(Float)
    reference_price: Mapped[float | None] = mapped_column(Float)
    reference_label: Mapped[str | None] = mapped_column(String(40))
    set_category: Mapped[str | None] = mapped_column(String(20))
    eol_status: Mapped[str | None] = mapped_column(String(20))
    current_bid_status: Mapped[str | None] = mapped_column(String(20))
    current_bid_recommendation: Mapped[str | None] = mapped_column(Text)
    last_warning: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default=AuctionWatchStatus.ACTIVE)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_count: Mapped[int] = mapped_column(Integer, default=0)

    lego_set: Mapped["LegoSet"] = relationship(lazy="selectin")

    def __repr__(self) -> str:
        return f"<AuctionWatchItem set={self.set_id} platform={self.source_platform} bid={self.current_bid}>"


from app.models.set import LegoSet  # noqa: E402
