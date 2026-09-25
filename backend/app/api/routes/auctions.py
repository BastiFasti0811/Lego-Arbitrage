"""Auction watchlist and discovery routes."""

import asyncio

import httpx
import structlog
from billiard.exceptions import SoftTimeLimitExceeded
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.platforms import detect_source_platform
from app.models import AuctionScanState, AuctionWatchItem, LegoSet, get_session
from app.runtime_settings import get_settings_map
from app.scrapers.brickmerge import BrickMergeScraper
from app.security.url_policy import UnsafeUrlError, validate_marketplace_url
from app.services.auction_scan_state import save_scan
from app.services.auction_tracking import refresh_watch_item
from app.services.auction_watch import evaluate_auction
from app.services.bricklink import BrickLinkScraper
from app.services.catawiki import (
    CatawikiParseError,
    CatawikiScraper,
    canonical_lot_url,
    lot_review_reasons,
    needs_lot_details,
)
from app.services.whatnot import WhatnotScraper

logger = structlog.get_logger()
router = APIRouter()

DISCOVERY_SETTINGS_BY_PLATFORM = {
    "CATAWIKI": {
        "cookie_header": "catawiki_cookie_header",
        "user_agent": "catawiki_user_agent",
        "scan_urls": "catawiki_scan_urls",
        "max_results": "catawiki_max_results_per_url",
    },
    "WHATNOT": {
        "cookie_header": "whatnot_cookie_header",
        "user_agent": "whatnot_user_agent",
        "scan_urls": "whatnot_scan_urls",
        "max_results": "whatnot_max_results_per_url",
    },
    "BRICKLINK": {
        "cookie_header": "bricklink_cookie_header",
        "user_agent": "bricklink_user_agent",
        "scan_urls": "bricklink_scan_urls",
        "max_results": "bricklink_max_results_per_url",
    },
}


class AuctionWatchCreate(BaseModel):
    set_number: str = Field(pattern=r"^\d{4,6}$")
    source_url: str
    source_platform: str = "CATAWIKI"
    lot_title: str | None = None
    current_bid: float = Field(ge=0, allow_inf_nan=False)
    purchase_shipping: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    desired_roi_percent: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    buyer_fee_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    buyer_fee_fixed: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fee_applies_to_shipping: bool = False
    notes: str | None = None


class AuctionWatchUpdate(BaseModel):
    lot_title: str | None = None
    current_bid: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    purchase_shipping: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    desired_roi_percent: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    buyer_fee_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    buyer_fee_fixed: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fee_applies_to_shipping: bool | None = None
    notes: str | None = None
    is_active: bool | None = None


class AuctionWatchResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    source_platform: str
    source_url: str
    lot_title: str | None = None
    current_bid: float
    purchase_shipping: float | None = None
    desired_roi_percent: float | None = None
    max_bid: float | None = None
    break_even_bid: float | None = None
    bid_gap: float | None = None
    bid_status: str | None = None
    recommendation_text: str | None = None
    expected_roi_current: float | None = None
    expected_roi_target: float | None = None
    expected_profit_current: float | None = None
    expected_profit_target: float | None = None
    all_in_cost_current: float | None = None
    all_in_cost_target: float | None = None
    buyer_fee_current: float | None = None
    buyer_fee_target: float | None = None
    market_price: float | None = None
    reference_price: float | None = None
    reference_label: str | None = None
    set_category: str | None = None
    eol_status: str | None = None
    status: str
    is_active: bool
    warning_text: str | None = None
    notes: str | None = None
    last_checked_at: str | None = None
    check_count: int


class AuctionDiscoverRequest(BaseModel):
    source_platform: str = "CATAWIKI"
    category_urls: list[str] = Field(default_factory=list, max_length=10)
    max_results_per_url: int = Field(default=20, ge=1, le=50)


class AuctionDiscoverResult(BaseModel):
    source_platform: str
    category_url: str
    lot_title: str
    source_url: str
    set_numbers: list[str] = []
    set_number: str | None = None
    current_bid: float | None = None
    purchase_shipping: float | None = None
    recommended_max_bid: float | None = None
    bid_gap: float | None = None
    can_bid_now: bool = False
    recommendation_text: str | None = None
    expected_roi_current: float | None = None
    expected_profit_current: float | None = None
    market_price: float | None = None
    reference_price: float | None = None
    warning_text: str | None = None
    bid_status: str = "NEEDS_REVIEW"
    condition: str = "UNKNOWN"
    all_in_cost_current: float | None = None
    buyer_fee_current: float | None = None
    source_prices: dict[str, float] = Field(default_factory=dict)


def _normalize_platform(platform: str | None) -> str:
    normalized = (platform or "CATAWIKI").upper()
    if normalized not in DISCOVERY_SETTINGS_BY_PLATFORM:
        raise HTTPException(status_code=400, detail="Unbekannte Auktionsplattform")
    return normalized


async def _get_scan_settings(platform: str) -> dict[str, str | None]:
    normalized = _normalize_platform(platform)
    keys = DISCOVERY_SETTINGS_BY_PLATFORM[normalized]
    return await get_settings_map(list(keys.values()))


def _platform_from_url(url: str) -> str:
    return _normalize_platform(detect_source_platform(url, None))


def _split_urls(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _build_configured_discovery_payload(
    platform: str,
    settings_map: dict[str, str | None],
    requested_max_results: int,
) -> tuple[list[str], str | None, str | None, int]:
    normalized = _normalize_platform(platform)
    keys = DISCOVERY_SETTINGS_BY_PLATFORM[normalized]
    category_urls = _split_urls(settings_map.get(keys["scan_urls"]))
    cookie_header = settings_map.get(keys["cookie_header"])
    user_agent = settings_map.get(keys["user_agent"])
    max_results = max(1, min(requested_max_results, 50))
    configured_limit = settings_map.get(keys["max_results"])
    if configured_limit and configured_limit.isdigit():
        max_results = max(1, min(max_results, int(configured_limit)))
    return list(dict.fromkeys(category_urls))[:10], cookie_header, user_agent, max_results


def _make_scraper(platform: str, cookie_header: str | None, user_agent: str | None):
    normalized = _normalize_platform(platform)
    if normalized == "WHATNOT":
        return WhatnotScraper(cookie_header=cookie_header, user_agent=user_agent)
    if normalized == "BRICKLINK":
        return BrickLinkScraper(cookie_header=cookie_header, user_agent=user_agent)
    return CatawikiScraper(cookie_header=cookie_header, user_agent=user_agent)


def _serialize_watch(item: AuctionWatchItem, lego_set: LegoSet) -> AuctionWatchResponse:
    return AuctionWatchResponse(
        id=item.id,
        set_number=lego_set.set_number,
        set_name=lego_set.set_name,
        source_platform=item.source_platform,
        source_url=item.source_url,
        lot_title=item.lot_title,
        current_bid=item.current_bid,
        purchase_shipping=item.purchase_shipping,
        desired_roi_percent=item.desired_roi_percent,
        max_bid=item.max_bid,
        break_even_bid=item.break_even_bid,
        bid_gap=item.bid_gap,
        bid_status=item.bid_status,
        recommendation_text=item.recommendation_text,
        expected_roi_current=item.expected_roi_current,
        expected_roi_target=item.expected_roi_target,
        expected_profit_current=item.expected_profit_current,
        expected_profit_target=item.expected_profit_target,
        all_in_cost_current=item.all_in_cost_current,
        all_in_cost_target=item.all_in_cost_target,
        buyer_fee_current=item.buyer_fee_current,
        buyer_fee_target=item.buyer_fee_target,
        market_price=item.market_price,
        reference_price=item.reference_price,
        reference_label=item.reference_label,
        set_category=item.set_category,
        eol_status=item.eol_status,
        status=item.status,
        is_active=item.is_active,
        warning_text=item.warning_text,
        notes=item.notes,
        last_checked_at=item.last_checked_at.isoformat() if item.last_checked_at else None,
        check_count=item.check_count or 0,
    )


async def _apply_watch_evaluation(item: AuctionWatchItem, lego_set: LegoSet) -> None:
    try:
        await refresh_watch_item(item, lego_set)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("auction.refresh_failed", error=type(exc).__name__)


async def _evaluate_lot(
    *,
    category_url: str,
    platform: str,
    lot,
) -> AuctionDiscoverResult | None:
    set_number = lot.set_numbers[0] if lot.set_numbers and len(lot.set_numbers) == 1 else None
    result = AuctionDiscoverResult(
        source_platform=platform, category_url=category_url, lot_title=lot.title, source_url=lot.url,
        set_numbers=lot.set_numbers or [], set_number=set_number, current_bid=lot.current_bid,
        purchase_shipping=lot.shipping_eur, condition=getattr(lot, "condition", "UNKNOWN"),
    )
    reasons = lot_review_reasons(lot) if platform == "CATAWIKI" else []
    if not set_number or lot.current_bid is None:
        reasons.append("Set oder Preis fehlt: manuell pruefen.")
    if reasons:
        result.bid_status = "ENDED" if getattr(lot, "is_closed", False) else "NEEDS_REVIEW"
        result.recommendation_text = " ".join(reasons)
        result.warning_text = result.recommendation_text
        return result

    try:
        evaluation = await evaluate_auction(
            set_number=set_number,
            current_bid=lot.current_bid,
            purchase_shipping=lot.shipping_eur,
            source_url=lot.url,
            source_platform=platform,
            condition=getattr(lot, "condition", "UNKNOWN"),
            box_damage=getattr(lot, "box_damage", False),
            buyer_fee_rate=getattr(lot, "buyer_fee_rate", None),
            buyer_fee_fixed=getattr(lot, "buyer_fee_fixed", None),
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("auction.discover_evaluation_failed", url=lot.url, error=str(exc))
        result.recommendation_text = "Marktvergleich fehlgeschlagen. Spaeter erneut pruefen."
        result.warning_text = result.recommendation_text
        return result

    return AuctionDiscoverResult(
        source_platform=platform,
        category_url=category_url,
        lot_title=lot.title,
        source_url=lot.url,
        set_numbers=lot.set_numbers or [],
        set_number=set_number,
        current_bid=lot.current_bid,
        purchase_shipping=lot.shipping_eur,
        recommended_max_bid=evaluation.bid_result.max_bid,
        bid_gap=evaluation.current_bid_gap,
        can_bid_now=evaluation.can_bid_now,
        recommendation_text=evaluation.recommendation_text,
        expected_roi_current=evaluation.expected_roi_at_current_bid,
        expected_profit_current=evaluation.expected_profit_at_current_bid,
        market_price=evaluation.analysis.market_consensus.consensus_price,
        reference_price=evaluation.bid_result.expected_sale_price,
        warning_text=" ".join(evaluation.warnings) or None,
        bid_status=evaluation.bid_status,
        condition=getattr(lot, "condition", "UNKNOWN"),
        all_in_cost_current=evaluation.current_total_purchase_cost,
        buyer_fee_current=evaluation.current_buyer_fee,
        source_prices=evaluation.analysis.market_consensus.source_prices,
    )


async def _discover_for_category(
    *,
    category_url: str,
    source_platform: str,
    cookie_header: str | None,
    user_agent: str | None,
    max_results: int,
    collected: dict[str, AuctionDiscoverResult] | None = None,
) -> list[AuctionDiscoverResult]:
    platform = _normalize_platform(source_platform or _platform_from_url(category_url))
    validate_marketplace_url(category_url, platform)
    results: list[AuctionDiscoverResult] = []
    async with _make_scraper(platform, cookie_header, user_agent) as scraper:
        direct_lot = False
        if platform == "CATAWIKI":
            try:
                canonical_lot_url(category_url)
                direct_lot = True
            except CatawikiParseError:
                pass
        lots = [await scraper.get_lot(category_url)] if direct_lot else await scraper.scan_category(
            category_url, limit=max_results,
        )
        for lot in lots:
            # Details nur fuer Lose, die laut Liste ein versiegeltes Einzelset sein koennen;
            # der Rest geht mit Pruefhinweis durch, ohne drei weitere Abrufe.
            if platform == "CATAWIKI" and not direct_lot and needs_lot_details(lot):
                lot = await scraper.get_lot(lot.url)
            evaluated = await _evaluate_lot(category_url=category_url, platform=platform, lot=lot)
            results.append(evaluated)
            if collected is not None:
                collected[evaluated.source_url] = evaluated
    return results


async def _discover_configured_platform(
    platform: str,
    max_results_per_url: int,
) -> list[AuctionDiscoverResult]:
    settings_map = await _get_scan_settings(platform)
    category_urls, cookie_header, user_agent, max_results = _build_configured_discovery_payload(
        platform,
        settings_map,
        max_results_per_url,
    )
    if not category_urls:
        return [], []
    return await _collect_scan(platform, category_urls, cookie_header, user_agent, max_results)


async def _scan_urls(platform, category_urls, cookie_header, user_agent, max_results):
    """API-Variante: ein Fehler kommt als 502 zurueck, das Teilergebnis ist gespeichert."""
    results, errors = await _collect_scan(platform, category_urls, cookie_header, user_agent, max_results)
    if errors:
        raise HTTPException(status_code=502, detail=" ".join(errors))
    return results


async def _collect_scan(platform, category_urls, cookie_header, user_agent, max_results):
    """Scan ausfuehren und speichern; liefert (Ergebnisse, Fehler) statt zu werfen.

    Der geplante Task braucht das Teilergebnis auch nach Zeitlimit oder Sperre,
    sonst werden gespeicherte Funde nie gemeldet.
    """
    # Validate the complete request before using this platform's session cookies.
    for url in category_urls:
        validate_marketplace_url(url, platform)
    discovered = {}
    errors = []
    try:
        # Three source platforms must finish within the worker's nine-minute limit.
        async with asyncio.timeout(120):
            for category_url in dict.fromkeys(category_urls):
                try:
                    results = await _discover_for_category(
                        category_url=category_url, source_platform=platform, cookie_header=cookie_header,
                        user_agent=user_agent, max_results=max_results,
                        collected=discovered,
                    )
                    for item in results:
                        discovered[item.source_url] = item
                except (httpx.HTTPError, CatawikiParseError) as exc:
                    errors.append(f"{platform}: Quelle gesperrt oder Seite nicht lesbar ({type(exc).__name__}).")
                    # Stop on source failure instead of multiplying requests against a block.
                    break
                except (SoftTimeLimitExceeded, TimeoutError):
                    # Worker-Limit und Scan-Zeitlimit haben eigene Behandlung (aussen).
                    raise
                except Exception as exc:  # noqa: BLE001 -- Teilergebnis muss gespeichert werden
                    logger.error("auction.scan_unexpected_error", platform=platform, error=repr(exc)[:300])
                    errors.append(f"{platform}: Unerwarteter Fehler beim Lesen ({type(exc).__name__}).")
                    break
    except TimeoutError:
        errors.append(f"{platform}: Zeitlimit erreicht. Weniger Lose oder URLs pro Scan einstellen.")
    results = sorted(discovered.values(), key=lambda item: (item.can_bid_now, item.expected_profit_current or 0),
                     reverse=True)
    await save_scan(platform, [item.model_dump() for item in results], errors)
    return results, errors


@router.get("/discovery-results")
async def latest_discovery_results(session: AsyncSession = Depends(get_session)):
    states = (await session.execute(select(AuctionScanState))).scalars().all()
    return [{"source_platform": state.platform, "scanned_at": state.scanned_at.isoformat(),
             "status": state.status, "results": state.results, "errors": state.errors} for state in states]


@router.get("/", response_model=list[AuctionWatchResponse])
async def list_auction_watchlist(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(AuctionWatchItem, LegoSet)
        .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
        .where(AuctionWatchItem.is_active)
        .order_by(AuctionWatchItem.updated_at.desc())
    )
    return [_serialize_watch(item, lego_set) for item, lego_set in result.all()]


@router.post("/", response_model=AuctionWatchResponse)
async def add_auction_watch(data: AuctionWatchCreate, session: AsyncSession = Depends(get_session)):
    platform = _normalize_platform(data.source_platform)
    try:
        validate_marketplace_url(data.source_url, platform)
        source_url = canonical_lot_url(data.source_url) if platform == "CATAWIKI" else data.source_url
    except (UnsafeUrlError, CatawikiParseError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = await session.execute(select(AuctionWatchItem).where(
        AuctionWatchItem.source_url == source_url, AuctionWatchItem.is_active,
    ))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Dieses Los wird bereits beobachtet")
    result = await session.execute(select(LegoSet).where(LegoSet.set_number == data.set_number))
    lego_set = result.scalar_one_or_none()
    if not lego_set:
        async with BrickMergeScraper() as scraper:
            info = await scraper.get_set_info(data.set_number)
        if not info or not info.release_year:
            raise HTTPException(status_code=422, detail="Setmetadaten fehlen. Set zuerst im Deal-Checker pruefen.")
        lego_set = LegoSet(
            set_number=data.set_number, set_name=info.set_name or f"LEGO {data.set_number}",
            theme=info.theme or "Unknown", release_year=info.release_year,
            uvp_eur=info.uvp_eur, eol_status=info.eol_status or "UNKNOWN",
        )
        session.add(lego_set)
        await session.flush()

    item = AuctionWatchItem(
        set_id=lego_set.id,
        source_platform=platform,
        source_url=source_url,
        lot_title=data.lot_title,
        current_bid=data.current_bid,
        purchase_shipping=data.purchase_shipping,
        desired_roi_percent=data.desired_roi_percent,
        buyer_fee_rate=data.buyer_fee_rate,
        buyer_fee_fixed=data.buyer_fee_fixed,
        fee_applies_to_shipping=data.fee_applies_to_shipping,
        notes=data.notes,
    )
    await _apply_watch_evaluation(item, lego_set)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _serialize_watch(item, lego_set)


@router.patch("/{item_id}", response_model=AuctionWatchResponse)
async def update_auction_watch(item_id: int, data: AuctionWatchUpdate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(AuctionWatchItem, LegoSet)
        .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
        .where(AuctionWatchItem.id == item_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Auktions-Item nicht gefunden")
    item, lego_set = row

    for field in (
        "lot_title",
        "current_bid",
        "purchase_shipping",
        "desired_roi_percent",
        "buyer_fee_rate",
        "buyer_fee_fixed",
        "notes",
        "is_active",
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(item, field, value)
    if data.fee_applies_to_shipping is not None:
        item.fee_applies_to_shipping = data.fee_applies_to_shipping

    await _apply_watch_evaluation(item, lego_set)
    await session.commit()
    return _serialize_watch(item, lego_set)


@router.post("/{item_id}/refresh", response_model=AuctionWatchResponse)
async def refresh_auction_watch(item_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(AuctionWatchItem, LegoSet)
        .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
        .where(AuctionWatchItem.id == item_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Auktions-Item nicht gefunden")
    item, lego_set = row
    await _apply_watch_evaluation(item, lego_set)
    await session.commit()
    return _serialize_watch(item, lego_set)


@router.delete("/{item_id}")
async def remove_auction_watch(item_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(AuctionWatchItem).where(AuctionWatchItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Auktions-Item nicht gefunden")
    item.is_active = False
    item.status = "ARCHIVED"
    await session.commit()
    return {"status": "removed", "id": item_id}


@router.post("/discover", response_model=list[AuctionDiscoverResult])
async def discover_auction_lots(request: AuctionDiscoverRequest):
    platform = _normalize_platform(request.source_platform)
    settings_map = await _get_scan_settings(platform)
    configured_urls, cookie_header, user_agent, max_results = _build_configured_discovery_payload(
        platform,
        settings_map,
        request.max_results_per_url,
    )
    category_urls = request.category_urls or configured_urls
    if not category_urls:
        raise HTTPException(status_code=400, detail=f"Keine {platform.title()}-Scan-URLs konfiguriert")

    try:
        return await _scan_urls(platform, category_urls, cookie_header, user_agent, max_results)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
