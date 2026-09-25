"""Catawiki-Scan ueber den Heimrechner (siehe app/services/remote_scan.py)."""

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.runtime_settings import get_settings_map
from app.services.remote_scan import (
    TOKEN_KEY,
    RemoteScanResults,
    clear_request,
    request_scan,
    runner_job,
    scan_status,
    verify_runner_token,
)
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()
router = APIRouter()

EVALUATE_TASK = "app.tasks.catawiki_scan.evaluate_remote_scan"


async def require_runner_token(authorization: str | None = Header(default=None)) -> None:
    provided = (authorization or "").removeprefix("Bearer ").strip()
    expected = (await get_settings_map([TOKEN_KEY])).get(TOKEN_KEY)
    if not verify_runner_token(provided, (expected or "").strip()):
        raise HTTPException(status_code=401, detail="Heimrechner-Token fehlt oder ist falsch")


# --- Heimrechner (Token) -------------------------------------------------------


@router.get("/runner/job", dependencies=[Depends(require_runner_token)])
async def get_runner_job(session: AsyncSession = Depends(get_session)):
    return await runner_job(session)


@router.post("/runner/results", status_code=202, dependencies=[Depends(require_runner_token)])
async def post_runner_results(data: RemoteScanResults, session: AsyncSession = Depends(get_session)):
    # Bewertung dauert je Los mehrere Marktabfragen: im Worker, nicht im Request.
    celery_app.send_task(EVALUATE_TASK, args=[data.model_dump()], queue="analysis")
    await clear_request(session)
    logger.info("remote_scan.results_accepted", lots=len(data.lots), errors=len(data.errors))
    return {"accepted": len(data.lots)}


# --- App (Cookie) --------------------------------------------------------------


@router.get("/status")
async def get_scan_status(session: AsyncSession = Depends(get_session)):
    return await scan_status(session)


@router.post("/request")
async def post_scan_request(session: AsyncSession = Depends(get_session)):
    status = await scan_status(session)
    if not status["token_configured"]:
        raise HTTPException(
            status_code=409,
            detail="Kein Heimrechner-Token eingerichtet (Einstellungen > Catawiki > Heimrechner-Token)",
        )
    await request_scan(session)
    return await scan_status(session)
