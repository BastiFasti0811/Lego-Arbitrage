"""Persistent history for manual deal analyses."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnalysisHistoryEntry(Base):
    __tablename__ = "analysis_history_entries"

    set_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    release_year: Mapped[int] = mapped_column(Integer, nullable=False)
    theme: Mapped[str] = mapped_column(String(100), nullable=False)
    set_age: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    uvp: Mapped[float | None] = mapped_column(Float)
    offer_price: Mapped[float] = mapped_column(Float, nullable=False)
    discount_vs_uvp: Mapped[float | None] = mapped_column(Float)
    market_price: Mapped[float] = mapped_column(Float, nullable=False)
    num_sources: Mapped[int] = mapped_column(Integer, nullable=False)
    roi_percent: Mapped[float] = mapped_column(Float, nullable=False)
    annualized_roi: Mapped[float] = mapped_column(Float, nullable=False)
    net_profit: Mapped[float] = mapped_column(Float, nullable=False)
    total_purchase_cost: Mapped[float] = mapped_column(Float, nullable=False)
    total_selling_costs: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_rating: Mapped[str] = mapped_column(String(50), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    opportunity_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_prices: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_platform: Mapped[str | None] = mapped_column(String(50))
