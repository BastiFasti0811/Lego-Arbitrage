"""Auction watchlist API for monitored lots such as Catawiki auctions."""

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuctionWatchItem, LegoSet, get_session
from app.services.auction_watch import evaluate_auction

logger = structlog.get_logger()
router = APIRouter()


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
    current_bid_gap: float | None = None
    current_bid_status: str | None = None
    current_bid_recommendation: str | None = None
    expected_roi_at_current_bid: float | None = None
    expected_profit_at_current_bid: float | None = None
    expected_roi_at_max_bid: float | None = None
    expected_profit_at_max_bid: float | None = None
    total_purchase_cost_at_current_bid: float | None = None
    total_purchase_cost_at_max_bid: float | None = None
    buyer_fee_at_current_bid: float | None = None
    buyer_fee_at_max_bid: float | None = None
    market_price: float | None = None
    reference_price: float | None = None
    reference_label: str | None = None
    set_category: str | None = None
    eol_status: str | None = None
    status: str
    is_active: bool
    notes: str | None = None
    last_warning: str | None = None
    last_checked_at: str | None = None
    last_alerted_at: str | None = None
    check_count: int


def _serialize(item: AuctionWatchItem, lego_set: LegoSet) -> AuctionWatchResponse:
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
        current_bid_gap=item.current_bid_gap,
        current_bid_status=item.current_bid_status,
        current_bid_recommendation=item.current_bid_recommendation,
        expected_roi_at_current_bid=item.expected_roi_at_current_bid,
        expected_profit_at_current_bid=item.expected_profit_at_current_bid,
        expected_roi_at_max_bid=item.expected_roi_at_max_bid,
        expected_profit_at_max_bid=item.expected_profit_at_max_bid,
        total_purchase_cost_at_current_bid=item.total_purchase_cost_at_current_bid,
        total_purchase_cost_at_max_bid=item.total_purchase_cost_at_max_bid,
        buyer_fee_at_current_bid=item.buyer_fee_at_current_bid,
        buyer_fee_at_max_bid=item.buyer_fee_at_max_bid,
        market_price=item.market_price,
        reference_price=item.reference_price,
        reference_label=item.reference_label,
        set_category=item.set_category,
        eol_status=item.eol_status,
        status=item.status,
        is_active=item.is_active,
        notes=item.notes,
        last_warning=item.last_warning,
        last_checked_at=item.last_checked_at.isoformat() if item.last_checked_at else None,
        last_alerted_at=item.last_alerted_at.isoformat() if item.last_alerted_at else None,
        check_count=item.check_count,
    )


async def _apply_evaluation(item: AuctionWatchItem, lego_set: LegoSet) -> None:
    evaluation = await evaluate_auction(
        set_number=lego_set.set_number,
        current_bid=item.current_bid,
        purchase_shipping=item.purchase_shipping,
        source_platform=item.source_platform,
        source_url=item.source_url,
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
    item.current_bid_gap = evaluation.current_bid_gap
    item.current_bid_status = evaluation.current_bid_status
    item.current_bid_recommendation = evaluation.current_bid_recommendation
    item.expected_roi_at_current_bid = evaluation.current_roi
    item.expected_profit_at_current_bid = evaluation.current_profit
    item.expected_roi_at_max_bid = evaluation.bid_result.expected_roi_at_max_bid
    item.expected_profit_at_max_bid = evaluation.bid_result.expected_profit_at_max_bid
    item.total_purchase_cost_at_current_bid = evaluation.current_total_purchase_cost
    item.total_purchase_cost_at_max_bid = evaluation.bid_result.total_purchase_cost_at_max_bid
    item.buyer_fee_at_current_bid = evaluation.current_buyer_fee
    item.buyer_fee_at_max_bid = evaluation.bid_result.buyer_fee_at_max_bid
    item.market_price = evaluation.analysis.market_consensus.consensus_price
    item.reference_price = evaluation.analysis.reference_price
    item.reference_label = evaluation.analysis.reference_label
    item.set_category = evaluation.analysis.category
    item.eol_status = lego_set.eol_status
    item.last_warning = evaluation.warnings[0] if evaluation.warnings else None
    item.last_checked_at = datetime.now(timezone.utc)
    item.check_count = (item.check_count or 0) + 1
    item.status = "ACTIVE" if evaluation.can_bid_now else "OVER_LIMIT"
    if evaluation.analysis.market_consensus.consensus_price > 0:
        lego_set.current_market_price = evaluation.analysis.market_consensus.consensus_price
        lego_set.market_price_updated_at = datetime.now(timezone.utc)


@router.get("/", response_model=list[AuctionWatchResponse])
async def list_auction_watchlist(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(AuctionWatchItem, LegoSet)
        .join(LegoSet, AuctionWatchItem.set_id == LegoSet.id)
        .where(AuctionWatchItem.is_active)
        .order_by(AuctionWatchItem.updated_at.desc())
    )
    return [_serialize(item, lego_set) for item, lego_set in result.all()]


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
    await _apply_evaluation(item, lego_set)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    logger.info("auction_watch.added", set_number=data.set_number, item_id=item.id, platform=item.source_platform)
    return _serialize(item, lego_set)


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
    await _apply_evaluation(item, lego_set)
    await session.commit()
    return _serialize(item, lego_set)


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

    await _apply_evaluation(item, lego_set)
    await session.commit()
    return _serialize(item, lego_set)


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
