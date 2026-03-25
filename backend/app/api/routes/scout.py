"""Scout API for on-demand and cached deal discovery."""

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.decision_engine import analyze_deal
from app.models import LegoSet, Offer, get_session
from app.scrapers import OFFER_SCRAPERS, PRICE_SCRAPERS
from app.scrapers.base import ScrapedOffer, ScrapedPrice

logger = structlog.get_logger()
router = APIRouter()


class ScoutRequest(BaseModel):
    """Request to scout for deals."""

    set_numbers: list[str]
    max_budget: float | None = None
    min_roi: float = 15.0
    cached_only: bool = False


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
    set_name: str | None = None
    theme: str | None = None


class ScoutResponse(BaseModel):
    """Scout results."""

    deals: list[DealResult]
    total_scanned: int
    sets_analyzed: int


def _build_deal_result(offer: Offer, lego_set: LegoSet) -> DealResult:
    estimated_roi = offer.estimated_roi or 0.0
    risk_score = offer.risk_score or 10
    opportunity_score = round(max(0.0, estimated_roi) * max(0, 10 - risk_score), 1)

    return DealResult(
        set_number=lego_set.set_number,
        set_name=lego_set.set_name,
        theme=lego_set.theme,
        platform=offer.platform,
        offer_title=offer.offer_title,
        offer_url=offer.offer_url,
        price=offer.price_eur,
        shipping=offer.shipping_eur,
        market_price=lego_set.current_market_price or lego_set.uvp_eur or offer.price_eur,
        estimated_roi=estimated_roi,
        risk_score=risk_score,
        recommendation=offer.recommendation or "CHECK",
        reason=offer.analysis_notes or "Analyse noch ausstehend",
        opportunity_score=opportunity_score,
    )


async def _cached_scout_deals(request: ScoutRequest, session: AsyncSession) -> ScoutResponse:
    result = await session.execute(
        select(Offer, LegoSet)
        .join(LegoSet, Offer.set_id == LegoSet.id)
        .where(Offer.status == "ACTIVE")
        .where(Offer.recommendation.is_not(None))
        .order_by(Offer.last_seen_at.desc())
    )

    seen_urls: set[str] = set()
    deals: list[DealResult] = []
    total_scanned = 0

    for offer, lego_set in result.all():
        if request.set_numbers and lego_set.set_number not in request.set_numbers:
            continue
        if request.max_budget and offer.price_eur > request.max_budget:
            continue
        if (offer.estimated_roi or 0.0) < request.min_roi:
            continue

        total_scanned += 1
        dedupe_key = f"{offer.platform}:{offer.offer_url}"
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        deals.append(_build_deal_result(offer, lego_set))

    deals.sort(key=lambda deal: deal.opportunity_score, reverse=True)
    return ScoutResponse(deals=deals[:20], total_scanned=total_scanned, sets_analyzed=len(request.set_numbers))


@router.post("/scan", response_model=ScoutResponse)
async def scout_deals(request: ScoutRequest, session: AsyncSession = Depends(get_session)):
    """Scout multiple sets for profitable deals."""
    if request.cached_only:
        return await _cached_scout_deals(request, session)

    all_deals: list[DealResult] = []
    total_offers = 0

    for set_number in request.set_numbers:
        logger.info("scout.scanning", set_number=set_number)
        set_result = await session.execute(select(LegoSet).where(LegoSet.set_number == set_number))
        lego_set = set_result.scalar_one_or_none()

        prices: list[ScrapedPrice] = []
        for scraper_cls in PRICE_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    price = await scraper.get_price(set_number)
                    if price:
                        prices.append(price)
            except Exception as exc:
                logger.warning("scout.price_failed", scraper=scraper_cls.__name__, error=str(exc))

        offers: list[ScrapedOffer] = []
        for scraper_cls in OFFER_SCRAPERS:
            try:
                async with scraper_cls() as scraper:
                    offers.extend(await scraper.get_offers(set_number))
            except Exception as exc:
                logger.warning("scout.offers_failed", scraper=scraper_cls.__name__, error=str(exc))

        total_offers += len(offers)

        for offer in offers:
            if request.max_budget and offer.price_eur > request.max_budget:
                continue

            analysis = analyze_deal(
                set_number=set_number,
                set_name=lego_set.set_name if lego_set else offer.offer_title,
                release_year=lego_set.release_year if lego_set else 2022,
                theme=lego_set.theme if lego_set else "Unknown",
                offer_price=offer.price_eur,
                prices=prices,
                uvp=lego_set.uvp_eur if lego_set else None,
                eol_status=lego_set.eol_status if lego_set else "UNKNOWN",
                condition=offer.condition,
                box_damage=offer.box_damage,
                purchase_shipping=offer.shipping_eur,
            )

            if analysis.roi.roi_percent >= request.min_roi:
                all_deals.append(
                    DealResult(
                        set_number=set_number,
                        set_name=lego_set.set_name if lego_set else None,
                        theme=lego_set.theme if lego_set else None,
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
                    )
                )

    all_deals.sort(key=lambda deal: deal.opportunity_score, reverse=True)
    return ScoutResponse(deals=all_deals[:20], total_scanned=total_offers, sets_analyzed=len(request.set_numbers))


@router.get("/quick/{set_number}")
async def quick_scout(
    set_number: str,
    max_results: int = Query(default=10, le=50),
    session: AsyncSession = Depends(get_session),
):
    """Quick scout for a single set."""
    response = await scout_deals(ScoutRequest(set_numbers=[set_number]), session=session)
    return ScoutResponse(deals=response.deals[:max_results], total_scanned=response.total_scanned, sets_analyzed=1)
