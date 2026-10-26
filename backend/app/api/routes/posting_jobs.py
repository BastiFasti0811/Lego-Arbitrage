"""Einstell-Auftraege fuer Kleinanzeigen (Nachtrag ADR 0002).

Die App legt Auftraege an, gibt sie frei oder bricht sie ab. Eingestellt wird
nicht hier: das macht ein Agent im Browser (Skill `kleinanzeigen-einstellen`)
ueber `python -m app.tools.ka_posting`. Einen Loesch-Endpunkt gibt es nicht.
"""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.models.inventory import InventoryItem
from app.models.listing import Listing
from app.models.listing_posting_job import ListingPostingJob
from app.services import ka_posting
from app.services.ka_posting import PostingError

logger = structlog.get_logger()
# Je Entwurf, gemountet unter /api/inventory.
router = APIRouter()
# Freigabe und Uebersicht, gemountet unter /api/listings/posting-jobs.
jobs_router = APIRouter()


class PostingJobCreate(BaseModel):
    dry_run: bool = False


class PostingJobResponse(BaseModel):
    id: int
    listing_id: int | None  # NULL, wenn das Listing geloescht wurde (Historie bleibt)
    action: str
    status: str
    dry_run: bool
    approved_by: str | None
    requested_at: datetime
    approved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result_url: str | None
    error: str | None

    model_config = {"from_attributes": True}


class PostingOverviewResponse(BaseModel):
    jobs: list[PostingJobResponse]
    posted_count: int
    autopost_enabled: bool
    approval_required: bool
    starts_today: int
    max_per_day: int
    block: str | None


def _bad_request(exc: PostingError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.detail)


async def _get_listing_with_item(item_id: int, listing_id: int, session: AsyncSession) -> tuple[Listing, InventoryItem]:
    result = await session.execute(select(Listing).where(Listing.id == listing_id, Listing.item_id == item_id))
    listing = result.scalar_one_or_none()
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing nicht gefunden")
    result = await session.execute(select(InventoryItem).where(InventoryItem.id == item_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Inventory item {item_id} not found")
    return listing, item


async def _get_job(job_id: int, session: AsyncSession) -> ListingPostingJob:
    try:
        return await ka_posting.get_job(session, job_id)
    except PostingError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc


@router.post("/{item_id}/listings/{listing_id}/posting-jobs", response_model=PostingJobResponse)
async def request_posting(
    item_id: int, listing_id: int, data: PostingJobCreate, session: AsyncSession = Depends(get_session)
):
    """Entwurf zum Einstellen anmelden. Ohne Freigabe geht nur ein Testlauf."""
    listing, item = await _get_listing_with_item(item_id, listing_id, session)
    try:
        job = await ka_posting.request_posting(session, listing, item, dry_run=data.dry_run, now=datetime.now(UTC))
    except PostingError as exc:
        raise _bad_request(exc) from exc
    try:
        await session.commit()
    except IntegrityError as exc:
        # Doppelklick: der partielle Unique-Index laesst nur einen offenen Auftrag zu.
        await session.rollback()
        raise HTTPException(status_code=409, detail="Für diesen Entwurf läuft schon ein Einstell-Auftrag") from exc
    await session.refresh(job)
    return job


@router.get("/{item_id}/listings/{listing_id}/posting-jobs", response_model=list[PostingJobResponse])
async def list_posting_jobs(item_id: int, listing_id: int, session: AsyncSession = Depends(get_session)):
    await _get_listing_with_item(item_id, listing_id, session)
    result = await session.execute(
        select(ListingPostingJob)
        .where(ListingPostingJob.listing_id == listing_id)
        .order_by(ListingPostingJob.requested_at.desc(), ListingPostingJob.id.desc())
        .limit(10)
    )
    return list(result.scalars())


@jobs_router.get("", response_model=PostingOverviewResponse)
async def posting_overview(session: AsyncSession = Depends(get_session)):
    """Offene und die letzten abgeschlossenen Auftraege samt Leitplanken."""
    result = await session.execute(
        select(ListingPostingJob).order_by(ListingPostingJob.requested_at.desc(), ListingPostingJob.id.desc()).limit(30)
    )
    status = await ka_posting.guardrail_status(session, datetime.now(UTC))
    return PostingOverviewResponse(
        jobs=[PostingJobResponse.model_validate(job) for job in result.scalars()],
        posted_count=status.posted_count,
        autopost_enabled=status.autopost_enabled,
        approval_required=status.approval_required,
        starts_today=status.starts_today,
        max_per_day=status.max_per_day,
        block=status.block,
    )


@jobs_router.post("/{job_id}/approve", response_model=PostingJobResponse)
async def approve_posting(job_id: int, session: AsyncSession = Depends(get_session)):
    """Sebastians Freigabe fuer genau diese Anzeige."""
    job = await _get_job(job_id, session)
    try:
        ka_posting.approve(job, datetime.now(UTC))
    except PostingError as exc:
        raise _bad_request(exc) from exc
    await session.commit()
    await session.refresh(job)
    logger.info("ka_posting.approved", job_id=job.id, listing_id=job.listing_id)
    return job


@jobs_router.post("/{job_id}/cancel", response_model=PostingJobResponse)
async def cancel_posting(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await _get_job(job_id, session)
    try:
        ka_posting.cancel(job, datetime.now(UTC), reason="In der App abgebrochen")
    except PostingError as exc:
        raise _bad_request(exc) from exc
    await session.commit()
    await session.refresh(job)
    logger.info("ka_posting.cancelled", job_id=job.id, listing_id=job.listing_id)
    return job
