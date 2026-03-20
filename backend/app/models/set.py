"""LEGO Set model — core entity representing a LEGO set."""

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Date, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SetCategory(str, Enum):
    """Set age category relative to current year."""

    FRESH = "FRESH"  # 0-1 years (Frisch Retired/Retiring)
    SWEET_SPOT = "SWEET_SPOT"  # 2-4 years ⭐
    ESTABLISHED = "ESTABLISHED"  # 5-7 years (Etabliert)
    VINTAGE = "VINTAGE"  # 8-11 years
    LEGACY = "LEGACY"  # 12+ years


class EOLStatus(str, Enum):
    """End-of-Life status."""

    AVAILABLE = "AVAILABLE"  # Still in retail
    RETIRING_SOON = "RETIRING_SOON"  # "Bald nicht mehr verfügbar"
    RETIRED = "RETIRED"  # Confirmed retired
    UNKNOWN = "UNKNOWN"


class ThemeTier(str, Enum):
    """Theme investment tier."""

    TIER_1 = "TIER_1"  # Premium: Star Wars, Harry Potter, Marvel, DC
    TIER_2 = "TIER_2"  # Solid: Icons, Technic Flagship, Architecture
    TIER_3 = "TIER_3"  # Specialized: Ninjago, Friends, City


class LegoSet(Base):
    """Represents a LEGO set with all metadata needed for investment analysis."""

    __tablename__ = "lego_sets"
    __table_args__ = (
        Index("ix_lego_sets_set_number", "set_number", unique=True),
        Index("ix_lego_sets_theme", "theme"),
        Index("ix_lego_sets_year", "release_year"),
        Index("ix_lego_sets_eol_status", "eol_status"),
    )

    # ── Identification ───────────────────────────────────
    set_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    set_name: Mapped[str] = mapped_column(String(300), nullable=False)
    theme: Mapped[str] = mapped_column(String(100), nullable=False)
    subtheme: Mapped[str | None] = mapped_column(String(100))

    # ── Product Details ──────────────────────────────────
    release_year: Mapped[int] = mapped_column(Integer, nullable=False)
    piece_count: Mapped[int | None] = mapped_column(Integer)
    minifigure_count: Mapped[int | None] = mapped_column(Integer)
    uvp_eur: Mapped[float | None] = mapped_column(Float)  # UVP in EUR
    weight_kg: Mapped[float | None] = mapped_column(Float)
    box_dimensions: Mapped[str | None] = mapped_column(String(50))  # "LxWxH cm"

    # ── Status ───────────────────────────────────────────
    eol_status: Mapped[str] = mapped_column(String(20), default=EOLStatus.UNKNOWN.value)
    eol_date: Mapped[date | None] = mapped_column(Date)
    is_exclusive: Mapped[bool] = mapped_column(default=False)  # LEGO.com exclusive
    is_gwp: Mapped[bool] = mapped_column(default=False)  # Gift With Purchase

    # ── Classification ───────────────────────────────────
    theme_tier: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str | None] = mapped_column(String(20))  # SetCategory

    # ── Cached Market Data ───────────────────────────────
    current_market_price: Mapped[float | None] = mapped_column(Float)
    market_price_updated_at: Mapped[datetime | None] = mapped_column()
    growth_percent: Mapped[float | None] = mapped_column(Float)  # Since release

    # ── Images ───────────────────────────────────────────
    image_url: Mapped[str | None] = mapped_column(Text)
    brickeconomy_url: Mapped[str | None] = mapped_column(Text)

    # ── Relationships ────────────────────────────────────
    prices: Mapped[list["PriceRecord"]] = relationship(back_populates="lego_set", lazy="selectin")
    offers: Mapped[list["Offer"]] = relationship(back_populates="lego_set", lazy="selectin")

    def __repr__(self) -> str:
        return f"<LegoSet {self.set_number} '{self.set_name}' ({self.release_year})>"

    @property
    def set_age(self) -> int:
        """Age of the set in years (relative to 2026)."""
        return 2026 - self.release_year

    def compute_category(self) -> SetCategory:
        """Determine investment category based on set age."""
        age = self.set_age
        if age <= 1:
            return SetCategory.FRESH
        elif age <= 4:
            return SetCategory.SWEET_SPOT
        elif age <= 7:
            return SetCategory.ESTABLISHED
        elif age <= 11:
            return SetCategory.VINTAGE
        else:
            return SetCategory.LEGACY


# Import here to avoid circular imports — these are defined in their own files
from app.models.price import PriceRecord  # noqa: E402
from app.models.offer import Offer  # noqa: E402
