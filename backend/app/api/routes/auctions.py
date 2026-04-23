"""Auction watchlist and discovery routes."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuctionWatchItem, LegoSet, get_session
from app.runtime_settings import get_settings_map
from app.services.auction_watch import evaluate_auction
from app.services.bricklink import BrickLinkScraper
from app.services.catawiki import CatawikiScraper
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
    set_number: str
    source_url: str
    source_platform: str = "CATAWIKI"
    lot_title: str | None = None
    current_bid: float
    purchase_shipping: float | None = None
    desired_roi_percent: float | None = None
    buyer_fee_rate: float | None = None
    buyer_fee_fixed: float | None = None
    fee_applies_to_shipping: bool = False
    notes: str | None = None


class AuctionWatchUpdate(BaseModel):
    lot_title: str | None = None
    current_bid: float | None = None
    purchase_shipping: float | None = None
    desired_roi_percent: float | None = None
    buyer_fee_rate: float | None = None
    buyer_fee_fixed: float | None = None
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
    purchase_shipping: float
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
    category_urls: list[str] = []
    max_results_per_url: int = 20


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


def _normalize_platform(platform: str | None) -> str:
    normalized = (platform or "CATAWIKI").upper()
    return normalized if normalized in DISCOVERY_SETTINGS_BY_PLATFORM else "CATAWIKI"


async def _get_scan_settings(platform: str) -> dict[str, str | None]:
    normalized = _normalize_platform(platform)
    keys = DISCOVERY_SETTINGS_BY_PLATFORM[normalized]
    return await get_settings_map(list(keys.values()))


def _platform_from_url(url: str) -> str:
    lowered = (url or "").lower()
    if "whatnot.com" in lowered:
        return "WHATNOT"
    if "bricklink.com" in lowered:
        return "BRICKLINK"
    return "CATAWIKI"


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
    max_results = requested_max_results
    configured_limit = settings_map.get(keys["max_results"])
    if configured_limit and configured_limit.isdigit():
        max_results = min(max_results, int(configured_limit))
    return category_urls, cookie_header, user_agent, max_results


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
    evaluation = await evaluate_auction(
        set_number=lego_set.set_number,
        current_bid=item.current_bid,
        purchase_shipping=item.purchase_shipping,
        source_url=item.source_url,
        source_platform=item.source_platform,
        desired_roi_percent=item.desired_roi_percent,
        buyer_fee_rate=item.buyer_fee_rate,
        buyer_fee_fixed=item.buyer_fee_fixed,
        fee_applies_to_shipping=item.fee_applies_to_shipping,
        set_name=lego_set.set_name,
        theme=lego_set.theme,
        release_year=lego_set.release_year,
        uvp=lego_set.uvp_eur,
        eol_status=lego_set.eol_status,
    )

    item.max_bid = evaluation.bid_result.max_bid
    item.break_even_bid = evaluation.bid_result.break_even_bid
    item.bid_gap = evaluation.current_bid_gap
    item.bid_status = evaluation.bid_status
    item.recommendation_text = evaluation.recommendation_text
    item.expected_roi_current = evaluation.expected_roi_at_current_bid
    item.expected_roi_target = evaluation.bid_result.expected_roi_at_max_bid
    item.expected_profit_current = evaluation.expected_profit_at_current_bid
    item.expected_profit_target = evaluation.bid_result.expected_profit_at_max_bid
    item.all_in_cost_current = evaluation.current_total_purchase_cost
    item.all_in_cost_target = evaluation.bid_result.total_purchase_cost_at_max_bid
    item.buyer_fee_current = evaluation.current_buyer_fee
    item.buyer_fee_target = evaluation.bid_result.buyer_fee_at_max_bid
    item.market_price = evaluation.analysis.market_consensus.consensus_price
    item.reference_price = evaluation.analysis.reference_price
    item.reference_label = evaluation.analysis.reference_label
    item.set_category = evaluation.analysis.category
    item.eol_status = evaluation.eol_status
    item.warning_text = evaluation.warnings[0] if evaluation.warnings else None
    item.last_checked_at = datetime.now(timezone.utc)
    item.check_count = (item.check_count or 0) + 1
    item.status = "ACTIVE" if evaluation.can_bid_now else "OVER_LIMIT"


async def _evaluate_lot(
    *,
    category_url: str,
    platform: str,
    lot,
) -> AuctionDiscoverResult | None:
    set_number = lot.set_numbers[0] if lot.set_numbers else None
    if not set_number or lot.current_bid is None:
        return None

    try:
        evaluation = await evaluate_auction(
            set_number=set_number,
            current_bid=lot.current_bid,
            purchase_shipping=lot.shipping_eur,
            source_url=lot.url,
            source_platform=platform,
        )
    except Exception as exc:
        logger.warning("auction.discover_evaluation_failed", url=lot.url, error=str(exc))
        return None

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
        reference_price=evaluation.analysis.reference_price,
        warning_text=evaluation.warnings[0] if evaluation.warnings else None,
    )


async def _discover_for_category(
    *,
    category_url: str,
    source_platform: str,
    cookie_header: str | None,
    user_agent: str | None,
    max_results: int,
) -> list[AuctionDiscoverResult]:
    platform = _normalize_platform(source_platform or _platform_from_url(category_url))
    async with _make_scraper(platform, cookie_header, user_agent) as scraper:
        lots = await scraper.scan_category(category_url, limit=max_results)

    results: list[AuctionDiscoverResult] = []
    for lot in lots:
        evaluated = await _evaluate_lot(category_url=category_url, platform=platform, lot=lot)
        if evaluated:
            results.append(evaluated)
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
    discovered: list[AuctionDiscoverResult] = []
    for category_url in category_urls:
        discovered.extend(
            await _discover_for_category(
                category_url=category_url,
                source_platform=platform,
                cookie_header=cookie_header,
                user_agent=user_agent,
                max_results=max_results,
            )
        )
    return discovered


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
    result = await session.execute(select(LegoSet).where(LegoSet.set_number == data.set_number))
    lego_set = result.scalar_one_or_none()
    if not lego_set:
        raise HTTPException(status_code=404, detail=f"Set {data.set_number} nicht gefunden")

    item = AuctionWatchItem(
        set_id=lego_set.id,
        source_platform=data.source_platform.upper(),
        source_url=data.source_url,
        lot_title=data.lot_title,
        current_bid=data.current_bid,
        purchase_shipping=data.purchase_shipping or 0.0,
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

    discovered: list[AuctionDiscoverResult] = []
    for category_url in category_urls:
        effective_platform = _normalize_platform(platform if request.category_urls else _platform_from_url(category_url))
        discovered.extend(
            await _discover_for_category(
                category_url=category_url,
                source_platform=effective_platform,
                cookie_header=cookie_header,
                user_agent=user_agent,
                max_results=max_results,
            )
        )

    discovered.sort(key=lambda item: (item.can_bid_now, item.expected_profit_current or 0), reverse=True)
    return discovered
