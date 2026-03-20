"""Feedback loop model — tracks actual outcomes of deals for self-improvement."""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DealFeedback(Base):
    """Records actual deal outcomes to calibrate the system.

    Self-improvement loop:
    1. System recommends a deal (GO)
    2. User buys the set
    3. User sells the set
    4. User logs actual outcome here
    5. System compares predicted vs actual ROI
    6. ML model retrains with new data
    """

    __tablename__ = "deal_feedback"

    # ── References ───────────────────────────────────────
    set_id: Mapped[int] = mapped_column(ForeignKey("lego_sets.id", ondelete="CASCADE"), nullable=False)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))

    # ── Purchase ─────────────────────────────────────────
    purchase_price: Mapped[float] = mapped_column(Float, nullable=False)
    purchase_shipping: Mapped[float] = mapped_column(Float, default=0.0)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    purchase_platform: Mapped[str] = mapped_column(String(20), nullable=False)

    # ── Sale (filled later) ──────────────────────────────
    sale_price: Mapped[float | None] = mapped_column(Float)
    sale_fees: Mapped[float | None] = mapped_column(Float)  # eBay + payment fees
    sale_shipping: Mapped[float | None] = mapped_column(Float)
    sale_packaging: Mapped[float | None] = mapped_column(Float)
    sale_date: Mapped[date | None] = mapped_column(Date)
    sale_platform: Mapped[str | None] = mapped_column(String(20))

    # ── Calculated Outcomes ──────────────────────────────
    actual_profit: Mapped[float | None] = mapped_column(Float)
    actual_roi: Mapped[float | None] = mapped_column(Float)  # %
    holding_months: Mapped[float | None] = mapped_column(Float)
    annualized_roi: Mapped[float | None] = mapped_column(Float)  # %

    # ── Predicted vs Actual ──────────────────────────────
    predicted_roi: Mapped[float | None] = mapped_column(Float)  # What system predicted
    roi_deviation: Mapped[float | None] = mapped_column(Float)  # actual - predicted
    predicted_risk_score: Mapped[int | None] = mapped_column(Integer)

    # ── Notes ────────────────────────────────────────────
    notes: Mapped[str | None] = mapped_column(Text)  # User notes about the deal
    difficulty: Mapped[str | None] = mapped_column(String(20))  # EASY, MEDIUM, HARD

    def __repr__(self) -> str:
        return f"<DealFeedback set={self.set_id} roi={self.actual_roi}%>"

    def calculate_outcomes(self) -> None:
        """Calculate actual profit, ROI, and deviation from prediction."""
        if self.sale_price is None:
            return

        total_cost = self.purchase_price + (self.purchase_shipping or 0)
        total_fees = (self.sale_fees or 0) + (self.sale_shipping or 0) + (self.sale_packaging or 0)
        net_revenue = self.sale_price - total_fees

        self.actual_profit = net_revenue - total_cost
        self.actual_roi = (self.actual_profit / total_cost) * 100 if total_cost > 0 else 0

        if self.purchase_date and self.sale_date:
            days = (self.sale_date - self.purchase_date).days
            self.holding_months = days / 30.44
            if self.holding_months > 0:
                self.annualized_roi = self.actual_roi / (self.holding_months / 12)

        if self.predicted_roi is not None:
            self.roi_deviation = self.actual_roi - self.predicted_roi


class WatchlistItem(Base):
    """User's watchlist — sets they want to monitor for deals."""

    __tablename__ = "watchlist_items"

    set_id: Mapped[int] = mapped_column(ForeignKey("lego_sets.id", ondelete="CASCADE"), nullable=False)
    target_price: Mapped[float | None] = mapped_column(Float)  # Max price to buy
    min_roi: Mapped[float | None] = mapped_column(Float)  # Minimum ROI to alert
    max_risk: Mapped[int | None] = mapped_column(Integer)  # Max risk score
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<WatchlistItem set={self.set_id} target={self.target_price}€>"
