"""Latest discovery result per marketplace, independent of the watchlist."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuctionScanState(Base):
    __tablename__ = "auction_scan_states"

    platform: Mapped[str] = mapped_column(String(30), unique=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    results: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    notified_urls: Mapped[list] = mapped_column(JSON, default=list)
