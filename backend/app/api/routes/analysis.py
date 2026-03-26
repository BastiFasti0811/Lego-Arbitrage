"""Analysis API — run deal analysis on sets or offers."""

import asyncio
import re
from datetime import datetime

import structlog
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.decision_engine import analyze_deal
from app.models import AnalysisHistoryEntry, DealFeedback, async_session, get_session
from app.scrapers import (
    AmazonScraper,
    BrickEconomyScraper,
    BrickMergeScraper,
    EbaySoldScraper,
    IdealoScraper,
    LegoComScraper,
)
from app.scrapers.base import ScrapedPrice
from app.scrapers.kleinanzeigen import _parse_ka_price

logger = structlog.get_logger()
router = APIRouter()


class SetLookupResponse(BaseModel):
    """Quick set info lookup result."""

    set_number: str
    set_name: str | None = None
    theme: str | None = None
    release_year: int | None = None
    uvp: float | None = None
    eol_status: str | None = None
    found: bool = False


class ParseUrlRequest(BaseModel):
    """Request to parse a Kleinanzeigen or other listing URL."""

    url: str


class CodeLookupRequest(BaseModel):
    """Resolve a scanned code or barcode to a LEGO set."""

    code: str


class ParseUrlResponse(BaseModel):
    """Extracted data from a listing URL."""

    set_number: str | None = None
    set_numbers: list[str] = []
    is_konvolut: bool = False
    price: float | None = None
    shipping: float | None = None
    title: str | None = None
    condition: str = "NEW_SEALED"
    platform: str = "UNKNOWN"
    url: str = ""
    seller_url: str | None = None


class SellerCheckRequest(BaseModel):
    """Request to check a seller's other LEGO listings."""

    seller_url: str  # Kleinanzeigen seller profile/listings URL
    max_results: int = 20


class SellerListing(BaseModel):
    """A single listing from a seller."""

    title: str
    price: float | None = None
    set_number: str | None = None
    url: str
    is_negotiable: bool = False


class SellerCheckResponse(BaseModel):
    """All LEGO listings from a seller."""

    seller_name: str | None = None
    total_listings: int = 0
    lego_listings: list[SellerListing] = []
    total_value: float = 0.0
    bundle_suggestion: str | None = None


class AnalyzeRequest(BaseModel):
    """Request to analyze a specific deal."""

    set_number: str
    offer_price: float
    condition: str = "NEW_SEALED"
    box_damage: bool = False
    purchase_shipping: float | None = None
    source_url: str | None = None  # Original listing URL
    source_platform: str | None = None
    # Optional overrides (if user already knows these)
    set_name: str | None = None
    theme: str | None = None
    release_year: int | None = None
    uvp: float | None = None
    eol_status: str | None = None


class CodeLookupResponse(SetLookupResponse):
    """Set lookup result for a scanned code."""

    code: str
    matched_set_number: str | None = None
    message: str | None = None


class AnalysisResponse(BaseModel):
    """Full analysis result."""

    history_id: int | None = None
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
    reference_price: float | None = None
    reference_label: str | None = None
    still_in_retail: bool = False
    eol_status: str | None = None
    calibration_roi_delta: float | None = None
    calibrated_roi_percent: float | None = None
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
    source_url: str | None = None
    source_platform: str | None = None


def _detect_source_platform(source_url: str | None, source_platform: str | None) -> str | None:
    if source_platform:
        return source_platform
    if not source_url:
        return None

    lowered = source_url.lower()
    if "kleinanzeigen" in lowered:
        return "KLEINANZEIGEN"
    if "ebay" in lowered:
        return "EBAY"
    if "amazon" in lowered:
        return "AMAZON"
    return "UNKNOWN"


def _set_info_to_lookup_response(code: str, matched_set_number: str, info) -> CodeLookupResponse:
    return CodeLookupResponse(
        code=code,
        matched_set_number=matched_set_number,
        set_number=matched_set_number,
        set_name=info.set_name,
        theme=info.theme,
        release_year=info.release_year,
        uvp=info.uvp_eur,
        eol_status=info.eol_status,
        found=bool(info and info.set_name),
        message=f"Code {code} wurde als Set {matched_set_number} erkannt",
    )


def _extract_set_candidates_from_code(code: str) -> list[str]:
    normalized = re.sub(r"\D", "", code)
    candidates = re.findall(r"\b(\d{4,6})\b", code)
    if 4 <= len(normalized) <= 6:
        candidates.insert(0, normalized)
    elif len(normalized) > 6:
        candidates.extend(re.findall(r"(\d{4,6})", normalized))

    deduplicated: list[str] = []
    for candidate in candidates:
        if candidate not in deduplicated:
            deduplicated.append(candidate)
    return deduplicated


def _extract_set_number_from_lookup_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    texts = []
    title_el = soup.select_one("title")
    h1_el = soup.select_one("h1")
    if title_el:
        texts.append(title_el.get_text(" ", strip=True))
    if h1_el:
        texts.append(h1_el.get_text(" ", strip=True))

    for text in texts:
        matches = re.findall(r"\b(\d{4,6})\b", text)
        if matches:
            return matches[0]
    return None


async def _lookup_set_info(set_number: str):
    from app.scrapers.brickmerge import BrickMergeScraper

    async with BrickMergeScraper() as scraper:
        return await scraper.get_set_info(set_number)


async def _resolve_set_number_from_code(code: str) -> str | None:
    from app.scrapers.brickmerge import BrickMergeScraper

    candidates = _extract_set_candidates_from_code(code)
    for candidate in candidates:
        info = await _lookup_set_info(candidate)
        if info and info.set_name:
            return candidate

    normalized = re.sub(r"\D", "", code)
    if not normalized:
        return None

    try:
        async with BrickMergeScraper() as scraper:
            html = await scraper._fetch_detail_page(normalized)
        return _extract_set_number_from_lookup_html(html)
    except Exception as exc:
        logger.warning("lookup_code.lookup_failed", code=code, error=str(exc))
        return None


def _history_to_response(entry: AnalysisHistoryEntry) -> AnalysisResponse:
    return AnalysisResponse(
        history_id=entry.id,
        set_number=entry.set_number,
        set_name=entry.set_name,
        release_year=entry.release_year,
        theme=entry.theme,
        set_age=entry.set_age,
        category=entry.category,
        uvp=entry.uvp,
        offer_price=entry.offer_price,
        discount_vs_uvp=entry.discount_vs_uvp,
        market_price=entry.market_price,
        reference_price=entry.market_price,
        reference_label="MARKT_KONSENS",
        still_in_retail=False,
        eol_status=None,
        calibration_roi_delta=None,
        calibrated_roi_percent=entry.roi_percent,
        num_sources=entry.num_sources,
        roi_percent=entry.roi_percent,
        annualized_roi=entry.annualized_roi,
        net_profit=entry.net_profit,
        total_purchase_cost=entry.total_purchase_cost,
        total_selling_costs=entry.total_selling_costs,
        risk_score=entry.risk_score,
        risk_rating=entry.risk_rating,
        recommendation=entry.recommendation,
        reason=entry.reason,
        suggestions=entry.suggestions or [],
        opportunity_score=entry.opportunity_score,
        confidence=entry.confidence,
        warnings=entry.warnings or [],
        source_prices=entry.source_prices or {},
        analyzed_at=entry.analyzed_at.isoformat(),
        source_url=entry.source_url,
        source_platform=entry.source_platform,
    )


async def _store_history(session: AsyncSession, response: AnalysisResponse) -> AnalysisResponse:
    entry = AnalysisHistoryEntry(
        set_number=response.set_number,
        set_name=response.set_name,
        release_year=response.release_year,
        theme=response.theme,
        set_age=response.set_age,
        category=response.category,
        uvp=response.uvp,
        offer_price=response.offer_price,
        discount_vs_uvp=response.discount_vs_uvp,
        market_price=response.market_price,
        num_sources=response.num_sources,
        roi_percent=response.roi_percent,
        annualized_roi=response.annualized_roi,
        net_profit=response.net_profit,
        total_purchase_cost=response.total_purchase_cost,
        total_selling_costs=response.total_selling_costs,
        risk_score=response.risk_score,
        risk_rating=response.risk_rating,
        recommendation=response.recommendation,
        reason=response.reason,
        suggestions=response.suggestions,
        opportunity_score=response.opportunity_score,
        confidence=response.confidence,
        warnings=response.warnings,
        source_prices=response.source_prices,
        analyzed_at=datetime.fromisoformat(response.analyzed_at),
        source_url=response.source_url,
        source_platform=response.source_platform,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return _history_to_response(entry)


async def _get_feedback_calibration(session: AsyncSession) -> tuple[float | None, int]:
    completed = await session.scalar(
        select(func.count(DealFeedback.id)).where(DealFeedback.roi_deviation.isnot(None))
    )
    completed_count = completed or 0
    if completed_count < 3:
        return None, completed_count

    avg_deviation = await session.scalar(
        select(func.avg(DealFeedback.roi_deviation)).where(DealFeedback.roi_deviation.isnot(None))
    )
    if avg_deviation is None:
        return None, completed_count

    clamped = max(-15.0, min(15.0, float(avg_deviation)))
    return round(clamped, 1), completed_count


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_offer(
    request: AnalyzeRequest,
    session: AsyncSession = Depends(get_session),
):
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

    calibration_roi_delta, calibration_sample_size = await _get_feedback_calibration(session)
    calibrated_roi_percent = analysis.roi.roi_percent
    suggestions = list(analysis.suggestions)
    if calibration_roi_delta is not None:
        calibrated_roi_percent = round(analysis.roi.roi_percent + calibration_roi_delta, 1)
        direction = "unter" if calibration_roi_delta < 0 else "über"
        suggestions.insert(
            0,
            f"Lern-Korrektur: echte Verkäufe lagen zuletzt im Schnitt {abs(calibration_roi_delta):.1f}pp {direction} der Prognose ({calibration_sample_size} Verkäufe)",
        )

    response = AnalysisResponse(
        set_number=analysis.set_number,
        set_name=analysis.set_name,
        source_url=request.source_url,
        source_platform=_detect_source_platform(request.source_url, request.source_platform),
        release_year=analysis.release_year,
        theme=analysis.theme,
        set_age=analysis.set_age,
        category=analysis.category,
        uvp=analysis.uvp,
        offer_price=analysis.offer_price,
        discount_vs_uvp=analysis.discount_vs_uvp,
        market_price=analysis.market_consensus.consensus_price,
        reference_price=analysis.reference_price,
        reference_label=analysis.reference_label,
        still_in_retail=still_in_retail,
        eol_status=eol_status,
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
        suggestions=suggestions,
        opportunity_score=analysis.opportunity_score,
        confidence=analysis.confidence,
        warnings=analysis.market_consensus.warnings,
        source_prices=analysis.market_consensus.source_prices,
        analyzed_at=analysis.analyzed_at.isoformat(),
        calibration_roi_delta=calibration_roi_delta,
        calibrated_roi_percent=calibrated_roi_percent,
    )

    return await _store_history(session, response)


# Persistent analysis history
@router.get("/history", response_model=list[AnalysisResponse])
async def get_analysis_history(session: AsyncSession = Depends(get_session)):
    """Get recent analysis history (newest first)."""
    result = await session.execute(
        select(AnalysisHistoryEntry)
        .order_by(AnalysisHistoryEntry.analyzed_at.desc(), AnalysisHistoryEntry.id.desc())
        .limit(200)
    )
    return [_history_to_response(entry) for entry in result.scalars().all()]


@router.get("/lookup/{set_number}", response_model=SetLookupResponse)
async def lookup_set(set_number: str):
    """Quick set info lookup via BrickMerge.

    Returns set name, theme, release year, UVP — used for auto-fill in forms.
    """
    logger.info("lookup.start", set_number=set_number)

    try:
        from app.scrapers.brickmerge import BrickMergeScraper
        async with BrickMergeScraper() as scraper:
            info = await scraper.get_set_info(set_number)
            if info and info.set_name:
                return SetLookupResponse(
                    set_number=set_number,
                    set_name=info.set_name,
                    theme=info.theme,
                    release_year=info.release_year,
                    uvp=info.uvp_eur,
                    eol_status=info.eol_status,
                    found=True,
                )
    except Exception as e:
        logger.warning("lookup.failed", set_number=set_number, error=str(e))

    return SetLookupResponse(set_number=set_number, found=False)


@router.post("/lookup-code", response_model=CodeLookupResponse)
async def lookup_code(request: CodeLookupRequest):
    """Resolve a scanned code to a LEGO set if possible."""
    code = request.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code darf nicht leer sein")

    logger.info("lookup_code.start", code=code)
    matched_set_number = await _resolve_set_number_from_code(code)
    if not matched_set_number:
        return CodeLookupResponse(
            code=code,
            set_number="",
            matched_set_number=None,
            found=False,
            message="Kein LEGO-Set zum gescannten Code gefunden. Bitte Set-Nummer manuell prüfen.",
        )

    info = await _lookup_set_info(matched_set_number)
    if info and info.set_name:
        return _set_info_to_lookup_response(code, matched_set_number, info)

    return CodeLookupResponse(
        code=code,
        set_number=matched_set_number,
        matched_set_number=matched_set_number,
        found=False,
        message="Code erkannt, aber Set-Daten konnten noch nicht geladen werden.",
    )


@router.post("/parse-url", response_model=ParseUrlResponse)
async def parse_listing_url(request: ParseUrlRequest):
    """Parse a Kleinanzeigen/eBay/Amazon URL to extract set number and price.

    Supports:
    - kleinanzeigen.de listing URLs
    - ebay.de listing URLs
    - amazon.de product URLs
    """
    url = request.url.strip()
    logger.info("parse_url.start", url=url)

    platform = "UNKNOWN"
    if "kleinanzeigen.de" in url:
        platform = "KLEINANZEIGEN"
    elif "ebay.de" in url or "ebay.com" in url:
        platform = "EBAY"
    elif "amazon.de" in url or "amazon.com" in url:
        platform = "AMAZON"

    # First: try to extract set numbers from URL slug (fast, no HTTP needed)
    # Kleinanzeigen URLs look like: /s-anzeige/lego-naboo-starfighter-7877/2994338498-23-3902
    url_set_numbers: list[str] = []
    slug_match = re.search(r"/([^/]*lego[^/]*)/", url, re.IGNORECASE)
    if slug_match:
        slug = slug_match.group(1)
        url_set_numbers = re.findall(r"\b(\d{4,6})\b", slug)
    if not url_set_numbers:
        # Fallback: any 4-6 digit numbers in the URL path (before query string)
        url_path = url.split("?")[0]
        url_set_numbers = re.findall(r"\b(\d{4,6})\b", url_path)
    url_set_number = url_set_numbers[0] if url_set_numbers else None

    # Try to fetch the page for more details
    try:
        from app.scrapers.kleinanzeigen import KleinanzeigenScraper
        async with KleinanzeigenScraper() as scraper:
            html = await scraper._fetch(url)
    except Exception as e:
        logger.warning("parse_url.fetch_failed", url=url, error=str(e))
        all_set_numbers = list(dict.fromkeys(url_set_numbers))  # deduplicate, preserve order
        return ParseUrlResponse(
            set_number=url_set_number,
            set_numbers=all_set_numbers,
            is_konvolut=len(all_set_numbers) > 1,
            platform=platform,
            url=url,
        )

    soup = BeautifulSoup(html, "lxml")

    title = ""
    price = None
    set_number = None
    condition = "NEW_SEALED"

    if platform == "KLEINANZEIGEN":
        # Extract title
        title_el = soup.select_one(
            "#viewad-title, "
            "[id*=title], "
            "h1"
        )
        title = title_el.get_text(strip=True) if title_el else ""

        # Extract price
        price_el = soup.select_one(
            "#viewad-price, "
            "[id*=price], "
            "h2[class*=price], "
            "[class*=price]"
        )
        if price_el:
            price = _parse_ka_price(price_el.get_text(strip=True))

    elif platform == "EBAY":
        title_el = soup.select_one("h1.x-item-title__mainTitle span, h1[id*=title]")
        title = title_el.get_text(strip=True) if title_el else ""
        price_el = soup.select_one("[class*=price] span, .x-price-primary span")
        if price_el:
            price_text = price_el.get_text(strip=True)
            m = re.search(r"([\d.,]+)", price_text.replace(".", "").replace(",", "."))
            if m:
                price = float(m.group(1))

    elif platform == "AMAZON":
        title_el = soup.select_one("#productTitle, #title")
        title = title_el.get_text(strip=True) if title_el else ""
        price_el = soup.select_one(".a-price .a-offscreen, #priceblock_ourprice, #price_inside_buybox")
        if price_el:
            price_text = price_el.get_text(strip=True)
            m = re.search(r"([\d.,]+)", price_text.replace(".", "").replace(",", "."))
            if m:
                price = float(m.group(1))

    else:
        title_el = soup.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else ""

    # Extract LEGO set numbers from title (4-6 digit numbers)
    title_set_numbers: list[str] = []
    if title:
        title_set_numbers = re.findall(r"\b(\d{4,6})\b", title)
        if title_set_numbers:
            set_number = title_set_numbers[0]

        # Detect condition from title
        title_lower = title.lower()
        if any(kw in title_lower for kw in ["versiegelt", "sealed", "misb", "ovp", "neu"]):
            condition = "NEW_SEALED"
        elif any(kw in title_lower for kw in ["geöffnet", "open", "aufgebaut"]):
            condition = "NEW_OPEN"
        elif any(kw in title_lower for kw in ["gebraucht", "used", "bespielt"]):
            condition = "USED_COMPLETE"

    # Merge set numbers from title and URL, deduplicate preserving order
    all_set_numbers = list(dict.fromkeys(title_set_numbers + url_set_numbers))

    # Fallback: use set number extracted from URL if HTML parsing didn't find one
    if not set_number and url_set_number:
        set_number = url_set_number

    is_konvolut = len(all_set_numbers) > 1

    logger.info(
        "parse_url.done",
        set_number=set_number,
        set_numbers=all_set_numbers,
        is_konvolut=is_konvolut,
        price=price,
        platform=platform,
    )

    return ParseUrlResponse(
        set_number=set_number,
        set_numbers=all_set_numbers,
        is_konvolut=is_konvolut,
        price=price,
        shipping=None,  # Kleinanzeigen renders shipping via JS
        title=title,
        condition=condition,
        platform=platform,
        url=url,
    )


class AnalyzeMultiRequest(BaseModel):
    """Request to analyze a Konvolut (multi-set bundle) deal."""

    set_numbers: list[str]
    total_price: float
    condition: str = "NEW_SEALED"
    box_damage: bool = False
    purchase_shipping: float | None = None
    source_url: str | None = None
    source_platform: str | None = None


class MultiAnalysisResponse(BaseModel):
    """Combined analysis result for a Konvolut."""

    results: list[AnalysisResponse]
    summary: dict  # total_market_value, total_investment, combined_roi, recommendation
    price_allocation: dict[str, float]  # how total_price was split per set


@router.post("/analyze-multi", response_model=MultiAnalysisResponse)
async def analyze_multi(request: AnalyzeMultiRequest):
    """Analyze a Konvolut (multi-set bundle) deal.

    Looks up UVP for each set, allocates the total price proportionally,
    then runs full analysis on each set in parallel.
    """
    if not request.set_numbers:
        raise HTTPException(status_code=400, detail="set_numbers darf nicht leer sein")

    logger.info(
        "analyze_multi.start",
        set_numbers=request.set_numbers,
        total_price=request.total_price,
    )

    # ── Step 1: Look up UVP for each set via BrickMerge (parallel) ──
    async def lookup_uvp(set_number: str) -> tuple[str, float | None]:
        try:
            from app.scrapers.brickmerge import BrickMergeScraper

            async with BrickMergeScraper() as scraper:
                info = await scraper.get_set_info(set_number)
                if info and info.uvp_eur:
                    return set_number, info.uvp_eur
        except Exception as e:
            logger.warning("analyze_multi.uvp_lookup_failed", set_number=set_number, error=str(e))
        return set_number, None

    uvp_results = await asyncio.gather(*[lookup_uvp(sn) for sn in request.set_numbers])
    uvp_map: dict[str, float | None] = dict(uvp_results)

    # ── Step 2: Allocate total_price proportionally based on UVP ──
    known_uvps = {sn: uvp for sn, uvp in uvp_map.items() if uvp is not None}
    price_allocation: dict[str, float] = {}

    if known_uvps and len(known_uvps) == len(request.set_numbers):
        # All UVPs known — proportional allocation
        total_uvp = sum(known_uvps.values())
        for sn in request.set_numbers:
            price_allocation[sn] = round(request.total_price * (known_uvps[sn] / total_uvp), 2)
    elif known_uvps:
        # Some UVPs known — proportional for known, equal split for unknown
        unknown_count = len(request.set_numbers) - len(known_uvps)
        total_known_uvp = sum(known_uvps.values())
        # Estimate average UVP for unknowns
        avg_uvp = total_known_uvp / len(known_uvps)
        total_estimated_uvp = total_known_uvp + avg_uvp * unknown_count
        for sn in request.set_numbers:
            uvp_val = known_uvps.get(sn, avg_uvp)
            price_allocation[sn] = round(request.total_price * (uvp_val / total_estimated_uvp), 2)
    else:
        # No UVPs known — equal split
        equal_share = round(request.total_price / len(request.set_numbers), 2)
        for sn in request.set_numbers:
            price_allocation[sn] = equal_share

    # ── Step 3: Run analyze_offer for each set (parallel) ──
    async def analyze_single(set_number: str, allocated_price: float) -> AnalysisResponse:
        req = AnalyzeRequest(
            set_number=set_number,
            offer_price=allocated_price,
            condition=request.condition,
            box_damage=request.box_damage,
            purchase_shipping=None,
            source_url=request.source_url,
            source_platform=request.source_platform,
        )
        async with async_session() as item_session:
            return await analyze_offer(req, item_session)

    analysis_results = await asyncio.gather(
        *[analyze_single(sn, price_allocation[sn]) for sn in request.set_numbers],
        return_exceptions=True,
    )

    # Filter out failed analyses
    valid_results: list[AnalysisResponse] = []
    for i, result in enumerate(analysis_results):
        if isinstance(result, Exception):
            logger.warning(
                "analyze_multi.single_failed",
                set_number=request.set_numbers[i],
                error=str(result),
            )
        else:
            valid_results.append(result)

    if not valid_results:
        raise HTTPException(status_code=500, detail="Keine der Analysen war erfolgreich")

    # ── Step 4: Calculate summary ──
    total_market_value = sum(r.market_price for r in valid_results)
    total_investment = request.total_price + (request.purchase_shipping or 0.0)
    total_selling_costs = sum(r.total_selling_costs for r in valid_results)
    total_net_profit = total_market_value - total_investment - total_selling_costs
    combined_roi = (total_net_profit / total_investment * 100) if total_investment > 0 else 0.0

    # Overall recommendation logic
    recommendations = [r.recommendation for r in valid_results]
    if any(r in ("GO_STAR", "GO") for r in recommendations):
        overall_recommendation = "GO"
    elif all(r == "NO_GO" for r in recommendations):
        overall_recommendation = "NO_GO"
    else:
        overall_recommendation = "CHECK"

    summary = {
        "total_market_value": round(total_market_value, 2),
        "total_investment": round(total_investment, 2),
        "total_selling_costs": round(total_selling_costs, 2),
        "total_net_profit": round(total_net_profit, 2),
        "combined_roi": round(combined_roi, 1),
        "recommendation": overall_recommendation,
        "num_sets_analyzed": len(valid_results),
        "num_sets_total": len(request.set_numbers),
    }

    logger.info(
        "analyze_multi.complete",
        num_sets=len(valid_results),
        combined_roi=summary["combined_roi"],
        recommendation=overall_recommendation,
    )

    return MultiAnalysisResponse(
        results=valid_results,
        summary=summary,
        price_allocation=price_allocation,
    )


@router.post("/seller-check", response_model=SellerCheckResponse)
async def check_seller(request: SellerCheckRequest):
    """Check a Kleinanzeigen seller's other LEGO listings.

    Accepts a seller profile URL or any Kleinanzeigen URL.
    Scrapes their listings for LEGO items and extracts set numbers + prices.
    """
    url = request.seller_url.strip()
    logger.info("seller_check.start", url=url)

    # Normalize URL: if it's a regular listing, try to find seller link
    # Typical seller listing URLs:
    # https://www.kleinanzeigen.de/s-bestandsliste.html?userId=123456
    # https://www.kleinanzeigen.de/s-anzeigen/USERNAME/s-bestandsliste
    if "/s-anzeige/" in url and "/s-bestandsliste" not in url:
        # This is a single listing, not a seller page — inform user
        raise HTTPException(
            status_code=400,
            detail="Bitte den Seller-Profil-Link verwenden (z.B. 'Alle Anzeigen' auf Kleinanzeigen)",
        )

    from app.scrapers.kleinanzeigen import KleinanzeigenScraper, _parse_ka_price

    lego_listings: list[SellerListing] = []
    seller_name = None
    total_listings = 0

    try:
        async with KleinanzeigenScraper() as scraper:
            html = await scraper._fetch(url)
            soup = BeautifulSoup(html, "lxml")

            # Extract seller name from page
            name_el = soup.select_one(
                "h1, "
                "[class*=username], "
                "[class*=profile-name]"
            )
            if name_el:
                seller_name = name_el.get_text(strip=True)

            # Find all ad items
            items = soup.select(
                "[class*=aditem], "
                "[data-testid*=ad-listitem], "
                ".ad-listitem, "
                "article[class*=ad]"
            )
            total_listings = len(items)

            for item in items[:request.max_results]:
                # Title
                title_el = item.select_one("a[class*=title], [class*=title], h2, h3")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                # Only LEGO items
                title_lower = title.lower()
                if "lego" not in title_lower and "duplo" not in title_lower:
                    continue

                # Price
                price_el = item.select_one("[class*=price], p[class*=price]")
                price_text = price_el.get_text(strip=True) if price_el else ""
                price = _parse_ka_price(price_text) if price_text else None
                is_negotiable = "VB" in price_text

                # Link
                link_el = item.select_one("a[href*='/s-anzeige/']")
                if not link_el:
                    link_el = title_el if title_el.name == "a" else title_el.find_parent("a")
                href = link_el.get("href", "") if link_el else ""
                offer_url = href if href.startswith("http") else f"https://www.kleinanzeigen.de{href}"

                # Extract set number from title
                set_match = re.search(r"\b(\d{4,6})\b", title)
                set_number = set_match.group(1) if set_match else None

                lego_listings.append(SellerListing(
                    title=title,
                    price=price,
                    set_number=set_number,
                    url=offer_url,
                    is_negotiable=is_negotiable,
                ))

    except Exception as e:
        logger.error("seller_check.failed", url=url, error=str(e))
        raise HTTPException(status_code=500, detail=f"Seller-Check fehlgeschlagen: {str(e)}")

    # Calculate totals and bundle suggestion
    total_value = sum(listing.price for listing in lego_listings if listing.price)
    bundle_suggestion = None

    if len(lego_listings) >= 2:
        bundle_suggestion = (
            f"{len(lego_listings)} LEGO-Angebote gefunden "
            f"(Gesamtwert: {total_value:.0f}€). "
            f"Bundle-Verhandlung möglich — bei {len(lego_listings)} Sets "
            f"Mengenrabatt anfragen!"
        )

    logger.info(
        "seller_check.done",
        seller=seller_name,
        total=total_listings,
        lego=len(lego_listings),
    )

    return SellerCheckResponse(
        seller_name=seller_name,
        total_listings=total_listings,
        lego_listings=lego_listings,
        total_value=total_value,
        bundle_suggestion=bundle_suggestion,
    )
