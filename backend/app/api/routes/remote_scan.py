"""Catawiki-Scan ueber den Heimrechner (siehe app/services/remote_scan.py)."""

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import get_session
from app.runtime_settings import get_settings_map
from app.services.remote_scan import (
    TOKEN_KEY,
    JobRejectedError,
    RemoteScanResults,
    accept_results,
    request_scan,
    runner_job,
    scan_status,
    verify_runner_token,
)
from app.tasks.celery_app import celery_app

logger = structlog.get_logger()
EVALUATE_TASK = "app.tasks.catawiki_scan.evaluate_remote_scan"


async def require_runner_token(authorization: str | None = Header(default=None)) -> None:
    provided = (authorization or "").removeprefix("Bearer ").strip()
    expected = (await get_settings_map([TOKEN_KEY])).get(TOKEN_KEY)
    if not verify_runner_token(provided, (expected or "").strip()):
        raise HTTPException(status_code=401, detail="Heimrechner-Token fehlt oder ist falsch")


router = APIRouter()
# Alles unter /runner laeuft ohne Login-Cookie (app/main.py); die Token-Pflicht
# haengt am Router selbst, damit keine kuenftige Route versehentlich offen ist.
runner = APIRouter(prefix="/runner", dependencies=[Depends(require_runner_token)])


@runner.get("/job")
async def get_runner_job(session: AsyncSession = Depends(get_session)):
    return await runner_job(session)


@runner.post("/results", status_code=202)
async def post_runner_results(data: RemoteScanResults, session: AsyncSession = Depends(get_session)):
    try:
        await accept_results(session, data)
    except JobRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    # Bewertung dauert je Los mehrere Marktabfragen: im Worker, nicht im Request.
    # Erst einreihen, dann committen -- faellt der Broker aus, bleibt der Auftrag offen.
    try:
        celery_app.send_task(EVALUATE_TASK, args=[data.model_dump()], queue="analysis")
    except Exception as exc:  # noqa: BLE001 -- Broker-Fehler jeder Art
        await session.rollback()
        logger.error("remote_scan.enqueue_failed", job_id=data.job_id, error=repr(exc)[:300])
        raise HTTPException(status_code=503, detail="Bewertung nicht startbar, spaeter erneut senden") from exc
    await session.commit()
    logger.info("remote_scan.results_accepted", job_id=data.job_id, lots=len(data.lots), errors=len(data.errors))
    return {"accepted": len(data.lots)}


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


router.include_router(runner)
