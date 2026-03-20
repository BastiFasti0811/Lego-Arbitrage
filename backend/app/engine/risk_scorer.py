"""Risk Score Calculator (0-10 scale).

Implements the complete risk scoring algorithm from the agent spec.
Factors: set age, EOL status, condition, box damage, market liquidity,
data quality, theme popularity.
"""

from dataclasses import dataclass

from app.models.set import EOLStatus, SetCategory, ThemeTier


# Theme classification for risk scoring
TIER_1_THEMES = {
    "Star Wars", "Harry Potter", "Marvel", "DC", "Batman",
    "Super Heroes", "Disney", "BrickHeadz",
}
TIER_2_THEMES = {
    "Icons", "Creator Expert", "Technic", "Architecture",
    "Ideas", "Art", "Botanical Collection",
}


@dataclass
class RiskBreakdown:
    """Detailed risk score breakdown for transparency."""

    age_risk: int  # 0-4
    eol_risk: int  # 0-3
    condition_risk: int  # 0-2
    box_risk: int  # 0-1
    liquidity_risk: int  # 0-2
    data_quality_risk: int  # 0-1
    theme_risk: int  # 0-1
    total: int  # 0-10
    rating: str  # SEHR SICHER / MODERAT / ERHÖHT / HOCH
    color: str  # green / yellow / orange / red


def calculate_risk_score(
    set_age: int,
    eol_status: str,
    months_since_eol: int | None = None,
    condition: str = "NEW_SEALED",
    box_damage: bool = False,
    monthly_sales: int | None = None,
    num_price_sources: int = 0,
    theme: str | None = None,
    still_in_retail: bool = False,
) -> RiskBreakdown:
    """Calculate risk score on 0-10 scale.

    Lower is better (safer investment).
    """
    # ── 1. Age Risk (0-4 points) ─────────────────────────
    if set_age <= 1:
        age_risk = 4  # Frisch retired — very risky
    elif set_age <= 4:
        age_risk = 1  # Sweet spot
    elif set_age <= 7:
        age_risk = 0  # Established — safest
    elif set_age <= 11:
        age_risk = 2  # Vintage — market saturation possible
    else:
        age_risk = 3  # Legacy — extremely volatile

    # ── 2. EOL Status (0-3 points) ───────────────────────
    if still_in_retail or eol_status == EOLStatus.AVAILABLE.value:
        eol_risk = 3  # Very risky — still in stores
    elif eol_status == EOLStatus.RETIRING_SOON.value:
        eol_risk = 2
    elif months_since_eol is not None:
        if months_since_eol < 6:
            eol_risk = 2  # Price still unstable
        elif months_since_eol < 12:
            eol_risk = 1  # Slightly uncertain
        else:
            eol_risk = 0  # Stable
    else:
        eol_risk = 1  # Unknown EOL timing

    # ── 3. Condition (0-2 points) ────────────────────────
    condition_upper = condition.upper()
    if condition_upper in ("NEW_SEALED", "NEU", "MISB"):
        condition_risk = 0
    elif condition_upper in ("NEW_OPEN_BOX", "OVP", "UNGEÖFFNET"):
        condition_risk = 1
    else:  # Used, opened, incomplete
        condition_risk = 2

    # ── 4. Box Damage (0-1 point) ────────────────────────
    box_risk = 1 if box_damage else 0

    # ── 5. Market Liquidity (0-2 points) ─────────────────
    if monthly_sales is not None:
        if monthly_sales >= 10:
            liquidity_risk = 0  # High liquidity
        elif monthly_sales >= 5:
            liquidity_risk = 1  # Medium
        else:
            liquidity_risk = 2  # Low = risky
    else:
        liquidity_risk = 1  # Unknown — assume medium

    # ── 6. Data Quality (0-1 point) ──────────────────────
    data_quality_risk = 0 if num_price_sources >= 2 else 1

    # ── 7. Theme Risk (0-1 point) ────────────────────────
    theme_risk = 0
    if theme:
        theme_clean = theme.strip()
        if theme_clean not in TIER_1_THEMES and theme_clean not in TIER_2_THEMES:
            theme_risk = 1  # Niche theme

    # ── Total (capped at 10) ─────────────────────────────
    total = min(
        age_risk + eol_risk + condition_risk + box_risk +
        liquidity_risk + data_quality_risk + theme_risk,
        10,
    )

    # Rating
    if total <= 2:
        rating, color = "SEHR SICHER", "green"
    elif total <= 5:
        rating, color = "MODERAT", "yellow"
    elif total <= 7:
        rating, color = "ERHÖHT", "orange"
    else:
        rating, color = "HOCH", "red"

    return RiskBreakdown(
        age_risk=age_risk,
        eol_risk=eol_risk,
        condition_risk=condition_risk,
        box_risk=box_risk,
        liquidity_risk=liquidity_risk,
        data_quality_risk=data_quality_risk,
        theme_risk=theme_risk,
        total=total,
        rating=rating,
        color=color,
    )
