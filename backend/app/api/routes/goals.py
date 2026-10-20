"""Zielzaehler fuer die App (Inventar-Kopf), Quelle: app.services.goal_progress."""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.services.goal_progress import GoalProgress, WeekNumbers, load_goal_progress

router = APIRouter()


class WeekResponse(BaseModel):
    label: str
    iso_week: int
    first_day: date
    last_day: date
    deals: int
    deals_go_star: int
    deals_go: int
    deals_auctions: int
    checks: int
    listed: int
    sold: int


class GoalProgressResponse(BaseModel):
    lego_items: int
    lego_listed: int
    lego_sold: int
    goal_listed: int
    goal_sold: int
    goal_deals_per_week: int
    deadline: date
    days_left: int
    current_week: WeekResponse
    previous_week: WeekResponse


def _week(numbers: WeekNumbers) -> WeekResponse:
    deals, sales = numbers.deals, numbers.sales
    return WeekResponse(
        label=numbers.week.label,
        iso_week=numbers.week.first_day.isocalendar().week,
        first_day=numbers.week.first_day,
        last_day=numbers.week.last_day,
        deals=deals.total,
        deals_go_star=deals.go_star,
        deals_go=deals.go,
        deals_auctions=deals.auctions,
        checks=deals.checks,
        listed=sales.listed,
        sold=sales.sold,
    )


def to_response(progress: GoalProgress) -> GoalProgressResponse:
    return GoalProgressResponse(
        lego_items=progress.lego_items,
        lego_listed=progress.lego_listed,
        lego_sold=progress.lego_sold,
        goal_listed=progress.goal_listed,
        goal_sold=progress.goal_sold,
        goal_deals_per_week=progress.goal_deals_per_week,
        deadline=progress.deadline,
        days_left=progress.days_left,
        current_week=_week(progress.current),
        previous_week=_week(progress.previous),
    )


@router.get("/progress", response_model=GoalProgressResponse)
async def goal_progress(session: AsyncSession = Depends(get_session)) -> GoalProgressResponse:
    """Gesamtstand Verkauf und Deals der laufenden und der letzten Woche."""
    return to_response(await load_goal_progress(session))
