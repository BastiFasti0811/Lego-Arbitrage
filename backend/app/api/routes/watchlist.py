"""Watchlist API — monitor specific sets for deals."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WatchlistItem, LegoSet, get_session

router = APIRouter()


class WatchlistAdd(BaseModel):
    set_number: str
    target_price: float | None = None
    min_roi: float | None = None
    max_risk: int | None = None
    notes: str | None = None


class WatchlistResponse(BaseModel):
    id: int
    set_number: str
    set_name: str | None = None
    target_price: float | None
    min_roi: float | None
    max_risk: int | None
    is_active: bool
    notes: str | None


@router.get("/", response_model=list[WatchlistResponse])
async def list_watchlist(session: AsyncSession = Depends(get_session)):
    """Get all active watchlist items."""
    result = await session.execute(
        select(WatchlistItem, LegoSet)
        .join(LegoSet, WatchlistItem.set_id == LegoSet.id)
        .where(WatchlistItem.is_active == True)
    )
    items = []
    for wl, ls in result.all():
        items.append(WatchlistResponse(
            id=wl.id,
            set_number=ls.set_number,
            set_name=ls.set_name,
            target_price=wl.target_price,
            min_roi=wl.min_roi,
            max_risk=wl.max_risk,
            is_active=wl.is_active,
            notes=wl.notes,
        ))
    return items


@router.post("/", response_model=WatchlistResponse)
async def add_to_watchlist(data: WatchlistAdd, session: AsyncSession = Depends(get_session)):
    """Add a set to the watchlist."""
    # Find the set
    result = await session.execute(
        select(LegoSet).where(LegoSet.set_number == data.set_number)
    )
    lego_set = result.scalar_one_or_none()
    if not lego_set:
        raise HTTPException(status_code=404, detail=f"Set {data.set_number} not found in database. Add it first via /api/sets/")

    item = WatchlistItem(
        set_id=lego_set.id,
        target_price=data.target_price,
        min_roi=data.min_roi,
        max_risk=data.max_risk,
        notes=data.notes,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    return WatchlistResponse(
        id=item.id,
        set_number=lego_set.set_number,
        set_name=lego_set.set_name,
        target_price=item.target_price,
        min_roi=item.min_roi,
        max_risk=item.max_risk,
        is_active=item.is_active,
        notes=item.notes,
    )


@router.delete("/{item_id}")
async def remove_from_watchlist(item_id: int, session: AsyncSession = Depends(get_session)):
    """Remove a set from the watchlist (soft delete)."""
    result = await session.execute(
        select(WatchlistItem).where(WatchlistItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    item.is_active = False
    await session.commit()
    return {"status": "removed", "id": item_id}
