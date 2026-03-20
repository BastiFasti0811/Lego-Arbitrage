"""GO/NO-GO Decision Engine — the final arbiter.

Combines ROI calculation, risk scoring, and market consensus
into a clear investment recommendation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings
from app.engine.market_consensus import MarketConsensus, calculate_consensus
from app.engine.risk_scorer import RiskBreakdown, calculate_risk_score
from app.engine.roi_calculator import ROIResult, calculate_roi
from app.models.set import SetCategory
from app.scrapers.base import ScrapedPrice


class Recommendation:
    GO_STAR = "GO_STAR"  # Exzellentes Risk-Reward ⭐
    GO = "GO"  # Solides Investment
    CHECK = "CHECK"  # Grenzfall — weitere Analyse nötig
    NO_GO = "NO_GO"  # Nicht kaufen


@dataclass
class AnalysisResult:
    """Complete analysis output for a deal."""

    # ── Set Info ─────────────────────────────────────────
    set_number: str
    set_name: str
    release_year: int
    theme: str
    set_age: int
    category: str  # SetCategory value

    # ── Market Data ──────────────────────────────────────
    uvp: float | None
    market_consensus: MarketConsensus
    offer_price: float
    discount_vs_uvp: float | None  # Percentage

    # ── ROI ──────────────────────────────────────────────
    roi: ROIResult

    # ── Risk ─────────────────────────────────────────────
    risk: RiskBreakdown

    # ── Decision ─────────────────────────────────────────
    recommendation: str  # GO_STAR, GO, CHECK, NO_GO
    reason: str
    suggestions: list[str] = field(default_factory=list)

    # ── Metadata ─────────────────────────────────────────
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0  # 0-1, reduced if data unreliable

    # ── Opportunity Score (for ranking) ──────────────────
    opportunity_score: float = 0.0


def _get_min_roi(category: str) -> float:
    """Get minimum ROI threshold for the set category."""
    mapping = {
        SetCategory.FRESH.value: settings.min_roi_fresh,
        SetCategory.SWEET_SPOT.value: settings.min_roi_sweet_spot,
        SetCategory.ESTABLISHED.value: settings.min_roi_established,
        SetCategory.VINTAGE.value: settings.min_roi_vintage,
        SetCategory.LEGACY.value: settings.min_roi_legacy,
    }
    return mapping.get(category, 20.0)


def _get_optimal_roi(category: str) -> float:
    """Get optimal ROI threshold for GO ⭐."""
    mapping = {
        SetCategory.FRESH.value: 50.0,
        SetCategory.SWEET_SPOT.value: 25.0,
        SetCategory.ESTABLISHED.value: 20.0,
        SetCategory.VINTAGE.value: 30.0,
        SetCategory.LEGACY.value: 40.0,
    }
    return mapping.get(category, 25.0)


def _get_holding_months(category: str) -> float:
    """Estimate typical holding time for the category."""
    mapping = {
        SetCategory.FRESH.value: 4.5,  # 3-6 months
        SetCategory.SWEET_SPOT.value: 12.0,  # 6-18 months
        SetCategory.ESTABLISHED.value: 24.0,  # 12-36 months
        SetCategory.VINTAGE.value: 42.0,  # 24-60 months
        SetCategory.LEGACY.value: 36.0,  # Variable
    }
    return mapping.get(category, 12.0)


def _categorize_set(release_year: int, current_year: int = 2026) -> str:
    """Determine set category from age."""
    age = current_year - release_year
    if age <= 1:
        return SetCategory.FRESH.value
    elif age <= 4:
        return SetCategory.SWEET_SPOT.value
    elif age <= 7:
        return SetCategory.ESTABLISHED.value
    elif age <= 11:
        return SetCategory.VINTAGE.value
    else:
        return SetCategory.LEGACY.value


def analyze_deal(
    set_number: str,
    set_name: str,
    release_year: int,
    theme: str,
    offer_price: float,
    prices: list[ScrapedPrice],
    uvp: float | None = None,
    eol_status: str = "UNKNOWN",
    months_since_eol: int | None = None,
    condition: str = "NEW_SEALED",
    box_damage: bool = False,
    monthly_sales: int | None = None,
    still_in_retail: bool = False,
    purchase_shipping: float | None = None,
) -> AnalysisResult:
    """Run full analysis on a potential deal.

    This is the main entry point for the analysis pipeline.
    """
    current_year = 2026
    set_age = current_year - release_year
    category = _categorize_set(release_year, current_year)
    holding_months = _get_holding_months(category)
    min_roi = _get_min_roi(category)
    optimal_roi = _get_optimal_roi(category)

    # ── 1. Market Consensus ──────────────────────────────
    consensus = calculate_consensus(prices)

    # ── 2. ROI Calculation ───────────────────────────────
    market_price = consensus.consensus_price if consensus.consensus_price > 0 else offer_price
    roi = calculate_roi(
        purchase_price=offer_price,
        market_price=market_price,
        purchase_shipping=purchase_shipping,
        holding_months=holding_months,
        uvp=uvp,
    )

    # ── 3. Risk Scoring ──────────────────────────────────
    risk = calculate_risk_score(
        set_age=set_age,
        eol_status=eol_status,
        months_since_eol=months_since_eol,
        condition=condition,
        box_damage=box_damage,
        monthly_sales=monthly_sales,
        num_price_sources=consensus.num_sources,
        theme=theme,
        still_in_retail=still_in_retail,
    )

    # ── 4. Discount vs UVP ──────────────────────────────
    discount_vs_uvp = None
    if uvp and uvp > 0:
        discount_vs_uvp = round((1 - offer_price / uvp) * 100, 1)

    # ── 5. GO/NO-GO Decision ─────────────────────────────
    recommendation, reason, suggestions = _make_decision(
        roi=roi,
        risk=risk,
        consensus=consensus,
        min_roi=min_roi,
        optimal_roi=optimal_roi,
        category=category,
        still_in_retail=still_in_retail,
        discount_vs_uvp=discount_vs_uvp,
    )

    # ── 6. Opportunity Score ─────────────────────────────
    liquidity_factor = 1.0
    if monthly_sales is not None:
        if monthly_sales >= 10:
            liquidity_factor = 1.0
        elif monthly_sales >= 5:
            liquidity_factor = 0.8
        else:
            liquidity_factor = 0.5

    opp_score = max(0, roi.roi_percent) * max(0, 10 - risk.total) * liquidity_factor

    # Confidence based on data quality
    confidence = 1.0
    if not consensus.is_reliable:
        confidence *= 0.7
    if consensus.num_sources < 2:
        confidence *= 0.6

    return AnalysisResult(
        set_number=set_number,
        set_name=set_name,
        release_year=release_year,
        theme=theme,
        set_age=set_age,
        category=category,
        uvp=uvp,
        market_consensus=consensus,
        offer_price=offer_price,
        discount_vs_uvp=discount_vs_uvp,
        roi=roi,
        risk=risk,
        recommendation=recommendation,
        reason=reason,
        suggestions=suggestions,
        opportunity_score=round(opp_score, 1),
        confidence=confidence,
    )


def _make_decision(
    roi: ROIResult,
    risk: RiskBreakdown,
    consensus: MarketConsensus,
    min_roi: float,
    optimal_roi: float,
    category: str,
    still_in_retail: bool,
    discount_vs_uvp: float | None,
) -> tuple[str, str, list[str]]:
    """Apply GO/NO-GO criteria and return recommendation."""

    suggestions = []

    # ── Criterion 1: ROI below minimum ───────────────────
    if roi.roi_percent < min_roi:
        # Calculate what price would give minimum ROI
        # roi = (market - costs - purchase) / purchase * 100
        # We need: min_roi = (market - costs - X) / X * 100
        # Target purchase to hit min ROI
        needed_total = roi.net_revenue / (1 + min_roi / 100)
        needed_price = needed_total - roi.purchase_shipping
        suggestions.append(f"Bei unter {needed_price:.0f}€ wäre ROI >{min_roi:.0f}% — dann kaufen!")
        suggestions.append(f"Suche Sets mit besserem Risk-Reward im {category} Bereich")

        return (
            Recommendation.NO_GO,
            f"ROI {roi.roi_percent:.1f}% liegt unter Minimum {min_roi:.0f}% für {category} Sets.",
            suggestions,
        )

    # ── Criterion 2: Risk too high ───────────────────────
    if risk.total >= 8:
        suggestions.append("Risiko-Faktoren prüfen und ggf. bessere Konditionen verhandeln")
        return (
            Recommendation.NO_GO,
            f"Risk-Score {risk.total}/10 zu hoch. {risk.rating}.",
            suggestions,
        )

    # ── Criterion 3: Unreliable market data ──────────────
    if consensus.num_sources < 2:
        suggestions.append("Weitere Preisquellen manuell recherchieren")
        suggestions.append("eBay verkaufte Artikel der letzten 60 Tage prüfen")
        return (
            Recommendation.CHECK,
            f"Unsichere Datenlage — nur {consensus.num_sources} Quelle(n). Manuell verifizieren!",
            suggestions,
        )

    # ── Criterion 4: Still in retail ─────────────────────
    if still_in_retail:
        if discount_vs_uvp and discount_vs_uvp >= 40:
            suggestions.append("Trotz Retail: Extremer Rabatt könnte Restposten sein")
        else:
            suggestions.append("Warte bis das Set aus dem Handel genommen wird (EOL)")
            return (
                Recommendation.NO_GO,
                "Set noch regulär im Handel erhältlich!",
                suggestions,
            )

    # ── Criterion 5: Excellent deal ──────────────────────
    if roi.roi_percent >= optimal_roi and risk.total <= settings.max_risk_score_go_star:
        suggestions.append("Exzellentes Risk-Reward — bei verfügbarem Kapital zuschlagen!")
        return (
            Recommendation.GO_STAR,
            f"ROI {roi.roi_percent:.1f}% bei Risk {risk.total}/10. Top-Deal!",
            suggestions,
        )

    # ── Criterion 6: Good deal ───────────────────────────
    if roi.roi_percent >= min_roi and risk.total <= settings.max_risk_score_go:
        suggestions.append(f"Erwarteter Gewinn: {roi.net_profit:.0f}€ in {roi.holding_months:.0f} Monaten")
        return (
            Recommendation.GO,
            f"Solides Investment. ROI {roi.roi_percent:.1f}%, Risk {risk.total}/10.",
            suggestions,
        )

    # ── Fallback: Edge case ──────────────────────────────
    suggestions.append("Grenzfall — Preis verhandeln oder auf besseres Angebot warten")
    return (
        Recommendation.CHECK,
        f"ROI {roi.roi_percent:.1f}%, Risk {risk.total}/10. Grenzfall.",
        suggestions,
    )
