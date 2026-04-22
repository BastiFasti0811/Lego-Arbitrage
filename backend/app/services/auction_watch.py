"""Shared fee-aware auction evaluation helpers."""

import asyncio
from dataclasses import dataclass

import structlog

from app.config import settings
from app.engine.decision_engine import analyze_deal
from app.engine.roi_calculator import calculate_ebay_fees, estimate_shipping
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
class AuctionFeeProfile:
    platform: str
    buyer_fee_rate: float
    buyer_fee_fixed: float
    fee_applies_to_shipping: bool = False


@dataclass
class AuctionBidResult:
    platform: str
    target_roi_percent: float
    expected_sale_price: float
    net_sale_revenue: float
    purchase_shipping: float
    buyer_fee_rate: float
    buyer_fee_fixed: float
    fee_applies_to_shipping: bool
    packaging_cost: float
    sale_shipping: float
    total_selling_costs: float
    max_bid: float
    buyer_fee_at_max_bid: float
    total_purchase_cost_at_max_bid: float
    expected_profit_at_max_bid: float
    expected_roi_at_max_bid: float
    break_even_bid: float
    break_even_total_purchase_cost: float
    break_even_buyer_fee: float


@dataclass
class AuctionEvaluation:
    analysis: object
    fee_profile: AuctionFeeProfile
    bid_result: AuctionBidResult
    current_total_purchase_cost: float
    current_buyer_fee: float
    expected_profit_at_current_bid: float
    expected_roi_at_current_bid: float
    current_bid_gap: float
    can_bid_now: bool
    bid_status: str
    recommendation_text: str
    warnings: list[str]
    detected_platform: str
    eol_status: str


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
    if "bricklink" in lowered:
        return "BRICKLINK"
    return "UNKNOWN"


def build_fee_profile(
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
            (
                resolved_set_name,
                resolved_theme,
                resolved_release_year,
                resolved_uvp,
                resolved_eol_status,
            ) = merge_set_info(
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


def calculate_auction_purchase_total(
    *,
    bid: float,
    purchase_shipping: float,
    fee_profile: AuctionFeeProfile,
) -> tuple[float, float]:
    fee_base = bid + purchase_shipping if fee_profile.fee_applies_to_shipping else bid
    buyer_fee = fee_base * fee_profile.buyer_fee_rate + fee_profile.buyer_fee_fixed
    total_purchase_cost = bid + purchase_shipping + buyer_fee
    return round(total_purchase_cost, 2), round(buyer_fee, 2)


def solve_max_bid(
    *,
    target_total_purchase_cost: float,
    purchase_shipping: float,
    fee_profile: AuctionFeeProfile,
) -> float:
    if target_total_purchase_cost <= 0:
        return 0.0

    if fee_profile.fee_applies_to_shipping:
        numerator = (target_total_purchase_cost - fee_profile.buyer_fee_fixed) / (1 + fee_profile.buyer_fee_rate)
        max_bid = numerator - purchase_shipping
    else:
        numerator = target_total_purchase_cost - purchase_shipping - fee_profile.buyer_fee_fixed
        max_bid = numerator / (1 + fee_profile.buyer_fee_rate)

    return round(max(0.0, max_bid), 2)


def calculate_max_bid(
    *,
    expected_sale_price: float,
    target_roi_percent: float,
    fee_profile: AuctionFeeProfile,
    purchase_shipping: float | None = None,
    sale_shipping: float | None = None,
    packaging_cost: float | None = None,
    uvp: float | None = None,
) -> AuctionBidResult:
    shipping = estimate_shipping(uvp=uvp or expected_sale_price)
    if purchase_shipping is None:
        purchase_shipping = shipping.purchase_shipping
    if sale_shipping is None:
        sale_shipping = shipping.sale_shipping
    if packaging_cost is None:
        packaging_cost = shipping.packaging_cost

    ebay_provision, payment_fee = calculate_ebay_fees(expected_sale_price)
    total_selling_costs = round(ebay_provision + payment_fee + packaging_cost + sale_shipping, 2)
    net_sale_revenue = round(expected_sale_price - total_selling_costs, 2)
    target_total_purchase_cost = net_sale_revenue / (1 + target_roi_percent / 100) if net_sale_revenue > 0 else 0.0

    max_bid = solve_max_bid(
        target_total_purchase_cost=target_total_purchase_cost,
        purchase_shipping=purchase_shipping,
        fee_profile=fee_profile,
    )
    total_purchase_cost_at_max_bid, buyer_fee_at_max_bid = calculate_auction_purchase_total(
        bid=max_bid,
        purchase_shipping=purchase_shipping,
        fee_profile=fee_profile,
    )
    expected_profit_at_max_bid = round(net_sale_revenue - total_purchase_cost_at_max_bid, 2)
    expected_roi_at_max_bid = (
        round(expected_profit_at_max_bid / total_purchase_cost_at_max_bid * 100, 1)
        if total_purchase_cost_at_max_bid > 0
        else 0.0
    )

    break_even_total_purchase_cost = max(0.0, net_sale_revenue)
    break_even_bid = solve_max_bid(
        target_total_purchase_cost=break_even_total_purchase_cost,
        purchase_shipping=purchase_shipping,
        fee_profile=fee_profile,
    )
    break_even_purchase_cost, break_even_buyer_fee = calculate_auction_purchase_total(
        bid=break_even_bid,
        purchase_shipping=purchase_shipping,
        fee_profile=fee_profile,
    )

    return AuctionBidResult(
        platform=fee_profile.platform,
        target_roi_percent=round(target_roi_percent, 1),
        expected_sale_price=round(expected_sale_price, 2),
        net_sale_revenue=net_sale_revenue,
        purchase_shipping=round(purchase_shipping, 2),
        buyer_fee_rate=fee_profile.buyer_fee_rate,
        buyer_fee_fixed=round(fee_profile.buyer_fee_fixed, 2),
        fee_applies_to_shipping=fee_profile.fee_applies_to_shipping,
        packaging_cost=round(packaging_cost, 2),
        sale_shipping=round(sale_shipping, 2),
        total_selling_costs=total_selling_costs,
        max_bid=max_bid,
        buyer_fee_at_max_bid=buyer_fee_at_max_bid,
        total_purchase_cost_at_max_bid=total_purchase_cost_at_max_bid,
        expected_profit_at_max_bid=expected_profit_at_max_bid,
        expected_roi_at_max_bid=expected_roi_at_max_bid,
        break_even_bid=break_even_bid,
        break_even_total_purchase_cost=break_even_purchase_cost,
        break_even_buyer_fee=break_even_buyer_fee,
    )


async def evaluate_auction(
    *,
    set_number: str,
    current_bid: float,
    purchase_shipping: float | None = None,
    source_platform: str = "CATAWIKI",
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

    detected_platform = (detect_source_platform(source_url, source_platform) or source_platform or "CATAWIKI").upper()
    still_in_retail = eol_status in ("AVAILABLE", "RETIRING_SOON")
    if not still_in_retail and detected_platform in {"AMAZON", "LEGO"}:
        still_in_retail = True
        if eol_status == "UNKNOWN":
            eol_status = "AVAILABLE"

    analysis = analyze_deal(
        set_number=set_number,
        set_name=set_name,
        release_year=release_year,
        theme=theme,
        offer_price=current_bid,
        prices=prices,
        uvp=uvp,
        eol_status=eol_status,
        monthly_sales=estimate_monthly_sales(prices),
        still_in_retail=still_in_retail,
        purchase_shipping=purchase_shipping,
    )

    target_roi = desired_roi_percent if desired_roi_percent is not None else default_target_roi_for_category(
        analysis.category
    )
    fee_profile = build_fee_profile(
        source_platform=detected_platform,
        buyer_fee_rate=buyer_fee_rate,
        buyer_fee_fixed=buyer_fee_fixed,
        fee_applies_to_shipping=fee_applies_to_shipping,
    )
    bid_result = calculate_max_bid(
        expected_sale_price=analysis.reference_price,
        target_roi_percent=target_roi,
        fee_profile=fee_profile,
        purchase_shipping=purchase_shipping,
        uvp=uvp,
    )
    current_total_purchase_cost, current_buyer_fee = calculate_auction_purchase_total(
        bid=current_bid,
        purchase_shipping=bid_result.purchase_shipping,
        fee_profile=fee_profile,
    )
    expected_profit_at_current_bid = round(bid_result.net_sale_revenue - current_total_purchase_cost, 2)
    expected_roi_at_current_bid = (
        round(expected_profit_at_current_bid / current_total_purchase_cost * 100, 1)
        if current_total_purchase_cost > 0
        else 0.0
    )
    current_bid_gap = round(bid_result.max_bid - current_bid, 2)
    can_bid_now = current_bid_gap >= 0
    if current_bid_gap >= 5:
        bid_status = "UNDER_LIMIT"
        recommendation_text = "Noch Luft bis zum Maximalgebot."
    elif current_bid_gap >= 0:
        bid_status = "AT_LIMIT"
        recommendation_text = "Nur noch wenig Luft. Auto-Bid eng setzen."
    else:
        bid_status = "OVER_LIMIT"
        recommendation_text = "Nicht weiter bieten. Gebot liegt bereits ueber deinem Zielkorridor."

    warnings = list(analysis.market_consensus.warnings)
    if detected_platform == "CATAWIKI":
        warnings.insert(
            0,
            "Catawiki all-in = Hammerpreis + 9% + 3 EUR Kaeuferschutz + Versand. Zoll/Import sind nicht enthalten.",
        )
    elif detected_platform == "WHATNOT":
        warnings.insert(0, "Whatnot-Kosten pruefen: Versand ist enthalten, weitere Plattformkosten koennen variieren.")
    elif detected_platform == "BRICKLINK":
        warnings.insert(0, "BrickLink ist meist Fixpreis. Versand und Shop-Mindestbestaende extra gegenpruefen.")
    if analysis.market_consensus.num_sources < 2:
        warnings.append("Datenlage duenn. Maximalgebot besser konservativ ansetzen.")

    return AuctionEvaluation(
        analysis=analysis,
        fee_profile=fee_profile,
        bid_result=bid_result,
        current_total_purchase_cost=current_total_purchase_cost,
        current_buyer_fee=current_buyer_fee,
        expected_profit_at_current_bid=expected_profit_at_current_bid,
        expected_roi_at_current_bid=expected_roi_at_current_bid,
        current_bid_gap=current_bid_gap,
        can_bid_now=can_bid_now,
        bid_status=bid_status,
        recommendation_text=recommendation_text,
        warnings=warnings,
        detected_platform=detected_platform,
        eol_status=eol_status,
    )
