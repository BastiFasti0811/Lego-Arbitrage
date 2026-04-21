"""Shared auction watch evaluation helpers."""

import asyncio
from dataclasses import dataclass

import structlog

from app.config import settings
from app.engine.auction_calculator import (
    AuctionBidResult,
    AuctionFeeProfile,
    calculate_auction_purchase_total,
    calculate_max_auction_bid,
)
from app.engine.decision_engine import AnalysisResult, analyze_deal
from app.scrapers import (
    AmazonScraper,
    BrickEconomyScraper,
    BrickMergeScraper,
    EbaySoldScraper,
    IdealoScraper,
    LegoComScraper,
    METADATA_SCRAPERS,
)
from app.scrapers.base import ScrapedPrice

logger = structlog.get_logger()


@dataclass
class AuctionEvaluation:
    """Computed view of a watched auction lot."""

    analysis: AnalysisResult
    target_roi_percent: float
    fee_profile: AuctionFeeProfile
    bid_result: AuctionBidResult
    current_bid: float
    current_total_purchase_cost: float
    current_buyer_fee: float
    current_profit: float
    current_roi: float
    current_bid_gap: float
    can_bid_now: bool
    current_bid_status: str
    current_bid_recommendation: str
    warnings: list[str]


def detect_source_platform(source_url: str | None, source_platform: str | None) -> str | None:
    if source_platform:
        return source_platform
    if not source_url:
        return None

    lowered = source_url.lower()
    if "catawiki" in lowered:
        return "CATAWIKI"
    if "kleinanzeigen" in lowered:
        return "KLEINANZEIGEN"
    if "ebay" in lowered:
        return "EBAY"
    if "amazon" in lowered:
        return "AMAZON"
    if "whatnot" in lowered:
        return "WHATNOT"
    return "UNKNOWN"


def default_target_roi_for_category(category: str) -> float:
    mapping = {
        "FRESH": settings.min_roi_fresh,
        "SWEET_SPOT": settings.min_roi_sweet_spot,
        "ESTABLISHED": settings.min_roi_established,
        "VINTAGE": settings.min_roi_vintage,
        "LEGACY": settings.min_roi_legacy,
    }
    return float(mapping.get(category, settings.min_roi_sweet_spot))


def estimate_monthly_sales(prices: list[ScrapedPrice]) -> int | None:
    for price in prices:
        if price.source == "EBAY_SOLD" and price.sold_count:
            return int(price.sold_count / 2)
    return None


def merge_set_info(
    *,
    info,
    set_number: str,
    set_name: str,
    theme: str,
    release_year: int,
    uvp: float | None,
    eol_status: str,
) -> tuple[str, str, int, float | None, str]:
    if info.set_name and (not set_name or set_name == set_number or set_name == f"LEGO {set_number}"):
        set_name = info.set_name
    if info.theme and (theme == "Unknown" or not theme):
        theme = info.theme
    if info.release_year and (release_year == 2020 or not release_year):
        release_year = info.release_year
    if info.uvp_eur and not uvp:
        uvp = info.uvp_eur
    if info.eol_status and eol_status == "UNKNOWN":
        eol_status = info.eol_status
    return set_name, theme, release_year, uvp, eol_status


def needs_metadata_retry(theme: str, release_year: int, uvp: float | None, eol_status: str) -> bool:
    return theme == "Unknown" or release_year == 2020 or not uvp or eol_status == "UNKNOWN"


async def retry_authoritative_metadata(
    set_number: str,
    set_name: str,
    theme: str,
    release_year: int,
    uvp: float | None,
    eol_status: str,
) -> tuple[str, str, int, float | None, str]:
    for scraper_cls in METADATA_SCRAPERS:
        if not needs_metadata_retry(theme, release_year, uvp, eol_status):
            break
        try:
            async with scraper_cls() as scraper:
                info = await scraper.get_set_info(set_number)
            if info:
                set_name, theme, release_year, uvp, eol_status = merge_set_info(
                    info=info,
                    set_number=set_number,
                    set_name=set_name,
                    theme=theme,
                    release_year=release_year,
                    uvp=uvp,
                    eol_status=eol_status,
                )
        except Exception as exc:
            logger.warning(
                "auction_watch.metadata_retry_failed",
                scraper=scraper_cls.__name__,
                set_number=set_number,
                error=str(exc),
            )
    return set_name, theme, release_year, uvp, eol_status


async def gather_market_context(
    *,
    set_number: str,
    set_name: str | None = None,
    theme: str | None = None,
    release_year: int | None = None,
    uvp: float | None = None,
    eol_status: str | None = None,
) -> tuple[list[ScrapedPrice], str, str, int, float | None, str]:
    prices: list[ScrapedPrice] = []
    resolved_set_name = set_name or f"LEGO {set_number}"
    resolved_theme = theme or "Unknown"
    resolved_release_year = release_year or 2020
    resolved_uvp = uvp
    resolved_eol_status = eol_status or "UNKNOWN"

    async def scrape_source(scraper_cls, requested_set_number: str):
        try:
            async with scraper_cls() as scraper:
                info = await scraper.get_set_info(requested_set_number)
                price = await scraper.get_price(requested_set_number)
                return info, price
        except Exception as exc:
            logger.warning("auction_watch.scraper_failed", scraper=scraper_cls.__name__, error=str(exc))
            return None, None

    scrapers = [
        BrickEconomyScraper,
        BrickMergeScraper,
        EbaySoldScraper,
        IdealoScraper,
        AmazonScraper,
        LegoComScraper,
    ]

    results = await asyncio.gather(
        *[scrape_source(scraper_cls, set_number) for scraper_cls in scrapers],
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            continue
        info, price = result
        if info:
            resolved_set_name, resolved_theme, resolved_release_year, resolved_uvp, resolved_eol_status = merge_set_info(
                info=info,
                set_number=set_number,
                set_name=resolved_set_name,
                theme=resolved_theme,
                release_year=resolved_release_year,
                uvp=resolved_uvp,
                eol_status=resolved_eol_status,
            )
        if price:
            prices.append(price)

    if needs_metadata_retry(resolved_theme, resolved_release_year, resolved_uvp, resolved_eol_status):
        (
            resolved_set_name,
            resolved_theme,
            resolved_release_year,
            resolved_uvp,
            resolved_eol_status,
        ) = await retry_authoritative_metadata(
            set_number,
            resolved_set_name,
            resolved_theme,
            resolved_release_year,
            resolved_uvp,
            resolved_eol_status,
        )

    return (
        prices,
        resolved_set_name,
        resolved_theme,
        resolved_release_year,
        resolved_uvp,
        resolved_eol_status,
    )


def build_auction_fee_profile(
    *,
    source_platform: str,
    buyer_fee_rate: float | None = None,
    buyer_fee_fixed: float | None = None,
    fee_applies_to_shipping: bool = False,
) -> AuctionFeeProfile:
    normalized_platform = (source_platform or "CATAWIKI").upper()
    default_rate = settings.catawiki_buyer_fee_rate if normalized_platform == "CATAWIKI" else 0.0
    default_fixed = settings.catawiki_buyer_fee_fixed if normalized_platform == "CATAWIKI" else 0.0
    return AuctionFeeProfile(
        platform=normalized_platform,
        buyer_fee_rate=buyer_fee_rate if buyer_fee_rate is not None else default_rate,
        buyer_fee_fixed=buyer_fee_fixed if buyer_fee_fixed is not None else default_fixed,
        fee_applies_to_shipping=fee_applies_to_shipping,
    )


async def evaluate_auction(
    *,
    set_number: str,
    current_bid: float,
    purchase_shipping: float | None,
    source_platform: str,
    source_url: str | None = None,
    desired_roi_percent: float | None = None,
    buyer_fee_rate: float | None = None,
    buyer_fee_fixed: float | None = None,
    fee_applies_to_shipping: bool = False,
    set_name: str | None = None,
    theme: str | None = None,
    release_year: int | None = None,
    uvp: float | None = None,
    eol_status: str | None = None,
) -> AuctionEvaluation:
    prices, set_name, theme, release_year, uvp, eol_status = await gather_market_context(
        set_number=set_number,
        set_name=set_name,
        theme=theme,
        release_year=release_year,
        uvp=uvp,
        eol_status=eol_status,
    )

    detected_platform = detect_source_platform(source_url, source_platform) or source_platform or "CATAWIKI"
    detected_platform = detected_platform.upper()

    still_in_retail = eol_status in ("AVAILABLE", "RETIRING_SOON")
    if not still_in_retail and detected_platform in {"AMAZON", "LEGO"}:
        still_in_retail = True
        if eol_status == "UNKNOWN":
            eol_status = "AVAILABLE"

    monthly_sales = estimate_monthly_sales(prices)
    analysis = analyze_deal(
        set_number=set_number,
        set_name=set_name,
        release_year=release_year,
        theme=theme,
        offer_price=current_bid,
        prices=prices,
        uvp=uvp,
        eol_status=eol_status,
        monthly_sales=monthly_sales,
        still_in_retail=still_in_retail,
        purchase_shipping=purchase_shipping,
    )

    target_roi = desired_roi_percent if desired_roi_percent is not None else default_target_roi_for_category(analysis.category)
    fee_profile = build_auction_fee_profile(
        source_platform=detected_platform,
        buyer_fee_rate=buyer_fee_rate,
        buyer_fee_fixed=buyer_fee_fixed,
        fee_applies_to_shipping=fee_applies_to_shipping,
    )
    bid_result = calculate_max_auction_bid(
        expected_sale_price=analysis.reference_price,
        target_roi_percent=target_roi,
        profile=fee_profile,
        purchase_shipping=purchase_shipping,
        uvp=uvp,
    )
    current_total_purchase_cost, current_buyer_fee = calculate_auction_purchase_total(
        bid=current_bid,
        purchase_shipping=bid_result.purchase_shipping,
        profile=fee_profile,
    )
    current_profit = round(bid_result.net_sale_revenue - current_total_purchase_cost, 2)
    current_roi = round(current_profit / current_total_purchase_cost * 100, 1) if current_total_purchase_cost > 0 else 0.0
    current_bid_gap = round(bid_result.max_bid - current_bid, 2)
    can_bid_now = current_bid_gap >= 0
    if current_bid_gap >= 5:
        current_bid_status = "UNDER_LIMIT"
        current_bid_recommendation = "Noch Luft bis zum Maximalgebot."
    elif current_bid_gap >= 0:
        current_bid_status = "AT_LIMIT"
        current_bid_recommendation = "Nur noch wenig Luft. Auto-Bid eng setzen."
    else:
        current_bid_status = "OVER_LIMIT"
        current_bid_recommendation = "Nicht weiter bieten. Gebot liegt bereits über deinem Zielkorridor."

    warnings = list(analysis.market_consensus.warnings)
    if detected_platform == "CATAWIKI":
        warnings.insert(
            0,
            "Catawiki all-in = Hammerpreis + 9% + 3€ Käuferschutz + Versand. Zölle oder Importabgaben sind nicht enthalten.",
        )
    if analysis.market_consensus.num_sources < 2:
        warnings.append("Datenlage dünn. Maximalgebot besser konservativ ansetzen.")

    return AuctionEvaluation(
        analysis=analysis,
        target_roi_percent=round(float(target_roi), 1),
        fee_profile=fee_profile,
        bid_result=bid_result,
        current_bid=current_bid,
        current_total_purchase_cost=current_total_purchase_cost,
        current_buyer_fee=current_buyer_fee,
        current_profit=current_profit,
        current_roi=current_roi,
        current_bid_gap=current_bid_gap,
        can_bid_now=can_bid_now,
        current_bid_status=current_bid_status,
        current_bid_recommendation=current_bid_recommendation,
        warnings=warnings,
    )
