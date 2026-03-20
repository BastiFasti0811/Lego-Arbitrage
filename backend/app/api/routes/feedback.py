"""Feedback API — track actual deal outcomes for self-improvement."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DealFeedback, LegoSet, get_session

router = APIRouter()


class FeedbackCreate(BaseModel):
    """Log a completed deal."""

    set_number: str
    purchase_price: float
    purchase_shipping: float = 0.0
    purchase_date: date
    purchase_platform: str
    # Sale data (can be added later)
    sale_price: float | None = None
    sale_fees: float | None = None
    sale_shipping: float | None = None
    sale_packaging: float | None = None
    sale_date: date | None = None
    sale_platform: str | None = None
    # What the system predicted
    predicted_roi: float | None = None
    predicted_risk_score: int | None = None
    notes: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    set_number: str
    purchase_price: float
    sale_price: float | None
    actual_profit: float | None
    actual_roi: float | None
    predicted_roi: float | None
    roi_deviation: float | None
    holding_months: float | None
    notes: str | None

    model_config = {"from_attributes": True}


class PerformanceStats(BaseModel):
    """System performance metrics."""

    total_deals: int
    completed_deals: int
    avg_actual_roi: float | None
    avg_predicted_roi: float | None
    avg_roi_deviation: float | None
    success_rate: float | None  # % of deals with positive ROI
    total_profit: float | None


@router.post("/", response_model=FeedbackResponse)
async def log_feedback(data: FeedbackCreate, session: AsyncSession = Depends(get_session)):
    """Log a deal outcome for the self-improvement loop."""
    result = await session.execute(
        select(LegoSet).where(LegoSet.set_number == data.set_number)
    )
    lego_set = result.scalar_one_or_none()
    if not lego_set:
        raise HTTPException(status_code=404, detail=f"Set {data.set_number} not found")

    fb = DealFeedback(
        set_id=lego_set.id,
        purchase_price=data.purchase_price,
        purchase_shipping=data.purchase_shipping,
        purchase_date=data.purchase_date,
        purchase_platform=data.purchase_platform,
        sale_price=data.sale_price,
        sale_fees=data.sale_fees,
        sale_shipping=data.sale_shipping,
        sale_packaging=data.sale_packaging,
        sale_date=data.sale_date,
        sale_platform=data.sale_platform,
        predicted_roi=data.predicted_roi,
        predicted_risk_score=data.predicted_risk_score,
        notes=data.notes,
    )

    # Calculate outcomes if sale data is complete
    fb.calculate_outcomes()

    session.add(fb)
    await session.commit()
    await session.refresh(fb)

    return FeedbackResponse(
        id=fb.id,
        set_number=data.set_number,
        purchase_price=fb.purchase_price,
        sale_price=fb.sale_price,
        actual_profit=fb.actual_profit,
        actual_roi=fb.actual_roi,
        predicted_roi=fb.predicted_roi,
        roi_deviation=fb.roi_deviation,
        holding_months=fb.holding_months,
        notes=fb.notes,
    )


@router.get("/performance", response_model=PerformanceStats)
async def get_performance(session: AsyncSession = Depends(get_session)):
    """Get system performance stats from completed deals."""
    # Total deals
    total = await session.scalar(select(func.count(DealFeedback.id)))

    # Completed deals (with sale data)
    completed = await session.scalar(
        select(func.count(DealFeedback.id)).where(DealFeedback.sale_price.isnot(None))
    )

    # Averages for completed deals
    avg_actual = await session.scalar(
        select(func.avg(DealFeedback.actual_roi)).where(DealFeedback.actual_roi.isnot(None))
    )
    avg_predicted = await session.scalar(
        select(func.avg(DealFeedback.predicted_roi)).where(DealFeedback.predicted_roi.isnot(None))
    )
    avg_deviation = await session.scalar(
        select(func.avg(DealFeedback.roi_deviation)).where(DealFeedback.roi_deviation.isnot(None))
    )
    total_profit = await session.scalar(
        select(func.sum(DealFeedback.actual_profit)).where(DealFeedback.actual_profit.isnot(None))
    )

    # Success rate
    profitable = await session.scalar(
        select(func.count(DealFeedback.id)).where(DealFeedback.actual_profit > 0)
    )
    success_rate = (profitable / completed * 100) if completed and completed > 0 else None

    return PerformanceStats(
        total_deals=total or 0,
        completed_deals=completed or 0,
        avg_actual_roi=round(avg_actual, 1) if avg_actual else None,
        avg_predicted_roi=round(avg_predicted, 1) if avg_predicted else None,
        avg_roi_deviation=round(avg_deviation, 1) if avg_deviation else None,
        success_rate=round(success_rate, 1) if success_rate else None,
        total_profit=round(total_profit, 2) if total_profit else None,
    )
