"""Verkaufs-Cockpit im Inventar-Kopf, Quelle: app.services.sales_cockpit.

Eigener Endpunkt statt Erweiterung von /api/goals/progress: der Zielzaehler
speist auch Montags-Digest und Laufprotokoll und bleibt unveraendert.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.services.sales_cockpit import SalesCockpit, load_cockpit

# Gemountet unter /api/inventory.
router = APIRouter()


class PostingTodayResponse(BaseModel):
    posted: int
    started: int
    max_per_day: int
    waiting_approval: int
    queued: int
    failed: int
    block: str | None


class CandidateResponse(BaseModel):
    item_id: int
    set_number: str | None
    set_name: str
    suggested_price: float
    photo_count: int
    storage_location: str | None


class SalesCockpitResponse(BaseModel):
    lego_holding: int
    lego_without_photo: int
    drafts_ready: int
    posting_today: PostingTodayResponse
    candidates: list[CandidateResponse]


def to_response(cockpit: SalesCockpit) -> SalesCockpitResponse:
    return SalesCockpitResponse(
        lego_holding=cockpit.lego_holding,
        lego_without_photo=cockpit.lego_without_photo,
        drafts_ready=cockpit.drafts_ready,
        posting_today=PostingTodayResponse(**vars(cockpit.posting_today)),
        candidates=[CandidateResponse(**vars(c)) for c in cockpit.candidates],
    )


@router.get("/cockpit", response_model=SalesCockpitResponse)
async def sales_cockpit(session: AsyncSession = Depends(get_session)) -> SalesCockpitResponse:
    """Ohne Foto, Entwuerfe bereit, Einstell-Auftraege heute, die naechsten drei Kandidaten."""
    return to_response(await load_cockpit(session))
