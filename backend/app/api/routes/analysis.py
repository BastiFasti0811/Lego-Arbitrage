"""Analysis API — run deal analysis on sets or offers."""

import asyncio
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.decision_engine import AnalysisResult, analyze_deal
from app.scrapers import (
    AmazonScraper,
    BrickEconomyScraper,
    BrickMergeScraper,
    EbaySoldScraper,
    IdealoScraper,
    LegoComScraper,
)
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()
router = APIRouter()


class AnalyzeRequest(BaseModel):
    """Request to analyze a specific deal."""

    set_number: str
    offer_price: float
    condition: str = "NEW_SEALED"
    box_damage: bool = False
    purchase_shipping: float | None = None
    # Optional overrides (if user already knows these)
    set_name: str | None = None
    theme: str | None = None
    release_year: int | None = None
    uvp: float | None = None
    eol_status: str | None = None


class AnalysisResponse(BaseModel):
    """Full analysis result."""

    set_number: str
    set_name: str
    release_year: int
    theme: str
    set_age: int
    category: str
    uvp: float | None
    offer_price: float
    discount_vs_uvp: float | None
    market_price: float
    num_sources: int
    roi_percent: float
    annualized_roi: float
    net_profit: float
    total_purchase_cost: float
    total_selling_costs: float
    risk_score: int
    risk_rating: str
    recommendation: str
    reason: str
    suggestions: list[str]
    opportunity_score: float
    confidence: float
    warnings: list[str]
    source_prices: dict[str, float]
    analyzed_at: str


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_offer(request: AnalyzeRequest):
    """Run full analysis on a potential LEGO deal.

    Scrapes all data sources, calculates ROI, risk, and gives
    a GO/NO-GO recommendation.
    """
    logger.info("analysis.start", set_number=request.set_number, price=request.offer_price)

    # ── Step 1: Gather data from all scrapers ────────────
    prices: list[ScrapedPrice] = []
    set_name = request.set_name or f"LEGO {request.set_number}"
    theme = request.theme or "Unknown"
    release_year = request.release_year or 2020
    uvp = request.uvp
    eol_status = request.eol_status or "UNKNOWN"

    async def scrape_source(scraper_cls, set_number: str):
        """Run a single scraper safely."""
        try:
            async with scraper_cls() as scraper:
                info = await scraper.get_set_info(set_number)
                price = await scraper.get_price(set_number)
                return info, price
        except Exception as e:
            logger.warning("analysis.scraper_failed", scraper=scraper_cls.__name__, error=str(e))
            return None, None

    # Run all scrapers concurrently
    scrapers = [
        BrickEconomyScraper,
        BrickMergeScraper,
        EbaySoldScraper,
        IdealoScraper,
        AmazonScraper,
        LegoComScraper,
    ]

    results = await asyncio.gather(
        *[scrape_source(s, request.set_number) for s in scrapers],
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            continue
        info, price = result
        if info:
            # Merge set info (first non-None wins)
            if info.set_name and set_name == f"LEGO {request.set_number}":
                set_name = info.set_name
            if info.theme and theme == "Unknown":
                theme = info.theme
            if info.release_year and release_year == 2020:
                release_year = info.release_year
            if info.uvp_eur and not uvp:
                uvp = info.uvp_eur
            if info.eol_status and eol_status == "UNKNOWN":
                eol_status = info.eol_status
        if price:
            prices.append(price)

    # Override with user-provided values
    if request.set_name:
        set_name = request.set_name
    if request.theme:
        theme = request.theme
    if request.release_year:
        release_year = request.release_year
    if request.uvp:
        uvp = request.uvp
    if request.eol_status:
        eol_status = request.eol_status

    # ── Step 2: Run analysis engine ──────────────────────
    still_in_retail = eol_status in ("AVAILABLE", "RETIRING_SOON")

    # Estimate monthly sales from eBay data
    monthly_sales = None
    for p in prices:
        if p.source == "EBAY_SOLD" and p.sold_count:
            monthly_sales = int(p.sold_count / 2)  # 60 days → monthly

    analysis = analyze_deal(
        set_number=request.set_number,
        set_name=set_name,
        release_year=release_year,
        theme=theme,
        offer_price=request.offer_price,
        prices=prices,
        uvp=uvp,
        eol_status=eol_status,
        condition=request.condition,
        box_damage=request.box_damage,
        monthly_sales=monthly_sales,
        still_in_retail=still_in_retail,
        purchase_shipping=request.purchase_shipping,
    )

    logger.info(
        "analysis.complete",
        set_number=request.set_number,
        recommendation=analysis.recommendation,
        roi=analysis.roi.roi_percent,
        risk=analysis.risk.total,
    )

    return AnalysisResponse(
        set_number=analysis.set_number,
        set_name=analysis.set_name,
        release_year=analysis.release_year,
        theme=analysis.theme,
        set_age=analysis.set_age,
        category=analysis.category,
        uvp=analysis.uvp,
        offer_price=analysis.offer_price,
        discount_vs_uvp=analysis.discount_vs_uvp,
        market_price=analysis.market_consensus.consensus_price,
        num_sources=analysis.market_consensus.num_sources,
        roi_percent=analysis.roi.roi_percent,
        annualized_roi=analysis.roi.annualized_roi,
        net_profit=analysis.roi.net_profit,
        total_purchase_cost=analysis.roi.total_purchase_cost,
        total_selling_costs=analysis.roi.total_selling_costs,
        risk_score=analysis.risk.total,
        risk_rating=analysis.risk.rating,
        recommendation=analysis.recommendation,
        reason=analysis.reason,
        suggestions=analysis.suggestions,
        opportunity_score=analysis.opportunity_score,
        confidence=analysis.confidence,
        warnings=analysis.market_consensus.warnings,
        source_prices=analysis.market_consensus.source_prices,
        analyzed_at=analysis.analyzed_at.isoformat(),
    )
