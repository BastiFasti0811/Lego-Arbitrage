"""Inventory API — portfolio tracking and sell-signal management."""

from datetime import date, datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.models.inventory import InventoryItem, InventoryStatus

logger = structlog.get_logger()
router = APIRouter()


# ── Request/Response Models ────────────────────────────────

class InventoryAdd(BaseModel):
    set_number: str
    set_name: str
    theme: str | None = None
    image_url: str | None = None
    buy_price: float
    buy_shipping: float = 0.0
    buy_date: date
    buy_platform: str | None = None
    buy_url: str | None = None
    condition: str = "NEW_SEALED"
    notes: str | None = None


class InventoryUpdate(BaseModel):
    buy_price: float | None = None
    buy_shipping: float | None = None
    buy_date: date | None = None
    buy_platform: str | None = None
    condition: str | None = None
    notes: str | None = None


class SellRequest(BaseModel):
    sell_price: float
    sell_date: date | None = None
    sell_platform: str | None = None


class InventoryResponse(BaseModel):
    id: int
    set_number: str
    set_name: str
    theme: str | None
    image_url: str | None
    buy_price: float
    buy_shipping: float
    total_invested: float
    buy_date: date
    buy_platform: str | None
    condition: str
    notes: str | None
    current_market_price: float | None
    market_price_updated_at: datetime | None
    unrealized_profit: float | None
    unrealized_roi_percent: float | None
    sell_signal_active: bool
    sell_signal_reason: str | None
    status: str
    sell_price: float | None
    sell_date: date | None
    sell_platform: str | None
    realized_profit: float | None
    realized_roi_percent: float | None
    holding_days: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    total_items: int
    holding_items: int
    sold_items: int
    total_invested: float
    current_value: float
    unrealized_profit: float
    unrealized_roi_percent: float
    total_realized_profit: float
    sell_signals_active: int


# ── Routes ────────────────────────────────────────────────

@router.get("/", response_model=list[InventoryResponse])
async def list_inventory(
    status: str | None = Query(default=None),
    sort_by: str = Query(default="buy_date"),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    query = select(InventoryItem)
    if status:
        query = query.where(InventoryItem.status == status)
    query = query.order_by(InventoryItem.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    items = result.scalars().all()
    return [_to_response(item) for item in items]


@router.post("/", response_model=InventoryResponse)
async def add_inventory_item(data: InventoryAdd, session: AsyncSession = Depends(get_session)):
    item = InventoryItem(
        set_number=data.set_number,
        set_name=data.set_name,
        theme=data.theme,
        image_url=data.image_url,
        buy_price=data.buy_price,
        buy_shipping=data.buy_shipping,
        buy_date=data.buy_date,
        buy_platform=data.buy_platform,
        buy_url=data.buy_url,
        condition=data.condition,
        notes=data.notes,
        status=InventoryStatus.HOLDING.value,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    logger.info("inventory.added", set_number=data.set_number, buy_price=data.buy_price)
    return _to_response(item)


@router.patch("/{item_id}", response_model=InventoryResponse)
async def update_inventory_item(
    item_id: int,
    data: InventoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    item = await _get_item(item_id, session)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/sell", response_model=InventoryResponse)
async def mark_as_sold(
    item_id: int,
    data: SellRequest,
    session: AsyncSession = Depends(get_session),
):
    item = await _get_item(item_id, session)
    if item.status == InventoryStatus.SOLD.value:
        raise HTTPException(status_code=400, detail="Item already sold")

    total_invested = item.buy_price + item.buy_shipping
    selling_costs = data.sell_price * 0.129 + 0.35 + data.sell_price * 0.019 + 0.35
    realized_profit = data.sell_price - total_invested - selling_costs

    item.status = InventoryStatus.SOLD.value
    item.sell_price = data.sell_price
    item.sell_date = data.sell_date or date.today()
    item.sell_platform = data.sell_platform
    item.realized_profit = round(realized_profit, 2)
    item.realized_roi_percent = round((realized_profit / total_invested) * 100, 1) if total_invested > 0 else 0
    item.sell_signal_active = False

    await session.commit()
    await session.refresh(item)
    logger.info("inventory.sold", set_number=item.set_number, profit=item.realized_profit)
    return _to_response(item)


@router.delete("/{item_id}")
async def delete_inventory_item(item_id: int, session: AsyncSession = Depends(get_session)):
    item = await _get_item(item_id, session)
    await session.delete(item)
    await session.commit()
    return {"status": "deleted", "id": item_id}


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(InventoryItem))
    items = result.scalars().all()

    holding = [i for i in items if i.status == InventoryStatus.HOLDING.value]
    sold = [i for i in items if i.status == InventoryStatus.SOLD.value]

    total_invested = sum(i.buy_price + i.buy_shipping for i in holding)
    current_value = sum(i.current_market_price or (i.buy_price + i.buy_shipping) for i in holding)
    unrealized = current_value - total_invested

    return PortfolioSummary(
        total_items=len(items),
        holding_items=len(holding),
        sold_items=len(sold),
        total_invested=round(total_invested, 2),
        current_value=round(current_value, 2),
        unrealized_profit=round(unrealized, 2),
        unrealized_roi_percent=round((unrealized / total_invested) * 100, 1) if total_invested > 0 else 0,
        total_realized_profit=round(sum(i.realized_profit or 0 for i in sold), 2),
        sell_signals_active=sum(1 for i in holding if i.sell_signal_active),
    )


@router.get("/history", response_model=list[InventoryResponse])
async def inventory_history(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(InventoryItem)
        .where(InventoryItem.status == InventoryStatus.SOLD.value)
        .order_by(InventoryItem.sell_date.desc())
    )
    return [_to_response(item) for item in result.scalars().all()]


# ── Helpers ────────────────────────────────────────────────

async def _get_item(item_id: int, session: AsyncSession) -> InventoryItem:
    result = await session.execute(
        select(InventoryItem).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return item


def _to_response(item: InventoryItem) -> InventoryResponse:
    total_invested = item.buy_price + (item.buy_shipping or 0)
    holding_days = (date.today() - item.buy_date).days if item.buy_date else 0

    return InventoryResponse(
        id=item.id,
        set_number=item.set_number,
        set_name=item.set_name,
        theme=item.theme,
        image_url=item.image_url,
        buy_price=item.buy_price,
        buy_shipping=item.buy_shipping or 0,
        total_invested=round(total_invested, 2),
        buy_date=item.buy_date,
        buy_platform=item.buy_platform,
        condition=item.condition,
        notes=item.notes,
        current_market_price=item.current_market_price,
        market_price_updated_at=item.market_price_updated_at,
        unrealized_profit=item.unrealized_profit,
        unrealized_roi_percent=item.unrealized_roi_percent,
        sell_signal_active=item.sell_signal_active,
        sell_signal_reason=item.sell_signal_reason,
        status=item.status,
        sell_price=item.sell_price,
        sell_date=item.sell_date,
        sell_platform=item.sell_platform,
        realized_profit=item.realized_profit,
        realized_roi_percent=item.realized_roi_percent,
        holding_days=holding_days,
        created_at=item.created_at,
    )
