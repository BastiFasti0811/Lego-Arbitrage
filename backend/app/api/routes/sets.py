"""LEGO Sets API — CRUD operations for set database."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LegoSet, get_session

router = APIRouter()


class SetResponse(BaseModel):
    """Public response model for a LEGO set."""

    id: int
    set_number: str
    set_name: str
    theme: str
    subtheme: str | None = None
    release_year: int
    piece_count: int | None = None
    minifigure_count: int | None = None
    uvp_eur: float | None = None
    eol_status: str
    category: str | None = None
    theme_tier: str | None = None
    current_market_price: float | None = None
    growth_percent: float | None = None
    image_url: str | None = None

    model_config = {"from_attributes": True}


class SetCreate(BaseModel):
    """Input model for creating/updating a set."""

    set_number: str
    set_name: str
    theme: str
    subtheme: str | None = None
    release_year: int
    piece_count: int | None = None
    minifigure_count: int | None = None
    uvp_eur: float | None = None
    eol_status: str = "UNKNOWN"
    is_exclusive: bool = False


@router.get("/", response_model=list[SetResponse])
async def list_sets(
    theme: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    eol_status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List LEGO sets with optional filters."""
    query = select(LegoSet)
    if theme:
        query = query.where(LegoSet.theme.ilike(f"%{theme}%"))
    if year_from:
        query = query.where(LegoSet.release_year >= year_from)
    if year_to:
        query = query.where(LegoSet.release_year <= year_to)
    if eol_status:
        query = query.where(LegoSet.eol_status == eol_status)

    query = query.order_by(LegoSet.release_year.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{set_number}", response_model=SetResponse)
async def get_set(set_number: str, session: AsyncSession = Depends(get_session)):
    """Get a specific LEGO set by number."""
    result = await session.execute(
        select(LegoSet).where(LegoSet.set_number == set_number)
    )
    lego_set = result.scalar_one_or_none()
    if not lego_set:
        raise HTTPException(status_code=404, detail=f"Set {set_number} not found")
    return lego_set


@router.post("/", response_model=SetResponse)
async def create_set(data: SetCreate, session: AsyncSession = Depends(get_session)):
    """Create or update a LEGO set."""
    # Check if exists
    result = await session.execute(
        select(LegoSet).where(LegoSet.set_number == data.set_number)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(existing, key, value)
        existing.category = existing.compute_category().value
        await session.commit()
        await session.refresh(existing)
        return existing
    else:
        # Create
        lego_set = LegoSet(**data.model_dump())
        lego_set.category = lego_set.compute_category().value
        session.add(lego_set)
        await session.commit()
        await session.refresh(lego_set)
        return lego_set
