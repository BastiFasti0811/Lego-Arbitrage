"""Scout API — proactive deal discovery across platforms."""

import asyncio

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.engine.decision_engine import analyze_deal
from app.scrapers import OFFER_SCRAPERS, PRICE_SCRAPERS
from app.scrapers.base import ScrapedOffer, ScrapedPrice

logger = structlog.get_logger()
router = APIRouter()


class ScoutRequest(BaseModel):
    """Request to scout for deals."""

    set_numbers: list[str]  # Sets to scan
    max_budget: float | None = None
    min_roi: float = 15.0


class DealResult(BaseModel):
    """A discovered deal."""

    set_number: str
    platform: str
    offer_title: str
    offer_url: str
    price: float
    shipping: float | None
    market_price: float
    estimated_roi: float
    risk_score: int
    recommendation: str
    reason: str
    opportunity_score: float


class ScoutResponse(BaseModel):
    """Scout results — top deals found."""

    deals: list[DealResult]
    total_scanned: int
    sets_analyzed: int


@router.post("/scan", response_model=ScoutResponse)
async def scout_deals(request: ScoutRequest):
    """Scout multiple sets for profitable deals.

    For each set:
    1. Fetch market prices from all sources
    2. Fetch active offers from all platforms
    3. Run analysis on each offer
    4. Return ranked by opportunity score
    """
    all_deals: list[DealResult] = []
    total_offers = 0

    for set_number in request.set_numbers:
        logger.info("scout.scanning", set_number=set_number)

        # Gather prices
        prices: list[ScrapedPrice] = []
        for scraper_cls in PRICE_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    price = await scraper.get_price(set_number)
                    if price:
                        prices.append(price)
            except Exception as e:
                logger.warning("scout.price_failed", scraper=scraper_cls.__name__, error=str(e))

        # Gather offers
        offers: list[ScrapedOffer] = []
        for scraper_cls in OFFER_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    found = await scraper.get_offers(set_number)
                    offers.extend(found)
            except Exception as e:
                logger.warning("scout.offers_failed", scraper=scraper_cls.__name__, error=str(e))

        total_offers += len(offers)

        # Analyze each offer
        for offer in offers:
            if request.max_budget and offer.price_eur > request.max_budget:
                continue

            analysis = analyze_deal(
                set_number=set_number,
                set_name=offer.offer_title,
                release_year=2022,  # Will be enriched later
                theme="Unknown",
                offer_price=offer.price_eur,
                prices=prices,
                condition=offer.condition,
                box_damage=offer.box_damage,
                purchase_shipping=offer.shipping_eur,
            )

            if analysis.roi.roi_percent >= request.min_roi:
                all_deals.append(DealResult(
                    set_number=set_number,
                    platform=offer.platform,
                    offer_title=offer.offer_title,
                    offer_url=offer.offer_url,
                    price=offer.price_eur,
                    shipping=offer.shipping_eur,
                    market_price=analysis.market_consensus.consensus_price,
                    estimated_roi=analysis.roi.roi_percent,
                    risk_score=analysis.risk.total,
                    recommendation=analysis.recommendation,
                    reason=analysis.reason,
                    opportunity_score=analysis.opportunity_score,
                ))

    # Sort by opportunity score (highest first)
    all_deals.sort(key=lambda d: d.opportunity_score, reverse=True)

    return ScoutResponse(
        deals=all_deals[:20],  # Top 20
        total_scanned=total_offers,
        sets_analyzed=len(request.set_numbers),
    )


@router.get("/quick/{set_number}")
async def quick_scout(
    set_number: str,
    max_results: int = Query(default=10, le=50),
):
    """Quick scout for a single set — find best current offers."""
    request = ScoutRequest(set_numbers=[set_number])
    return await scout_deals(request)
