"""Abhaengigkeits-Pruefungen fuer den /health-Endpoint.

/health ist der einzige Endpoint, den Docker (Compose-Healthcheck),
scripts/deploy-prod.sh und der Autopilot nach einem Deploy fragen. Er meldet
deshalb nur dann "healthy", wenn die API ihre beiden Pflicht-Abhaengigkeiten
erreicht: PostgreSQL (jede fachliche Route) und Redis (Sessions, Login-Limit,
Celery-Broker). Worker und Beat prueft er nicht; die ueberwacht der
pipeline-health-check (siehe docs/deploy.md#pipeline-health).

Jede Pruefung hat ein eigenes, kurzes Zeitlimit, damit ein haengender Dienst
den Endpoint nicht laenger blockiert als den Compose-Healthcheck (timeout 10 s).
Die Antwort nennt pro Dienst nur "ok" oder "error": Fehlertexte koennen
Hostnamen oder DSN-Teile enthalten und gehen nur ins Log.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import text

from app.models import engine
from app.security import redis_store

logger = structlog.get_logger()

CHECK_TIMEOUT_SECONDS = 3.0

CHECK_OK = "ok"
CHECK_ERROR = "error"


async def check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis() -> None:
    # redis_store.get_redis wird zur Laufzeit nachgeschlagen, damit Tests den
    # Client wie bei Sessions und Login-Limit ersetzen koennen.
    await redis_store.get_redis().ping()


CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "database": check_database,
    "redis": check_redis,
}


async def _run_check(name: str, check: Callable[[], Awaitable[None]]) -> str:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - jeder Fehler heisst "Dienst nicht nutzbar"
        logger.warning("health.check_failed", check=name, error=type(exc).__name__, detail=str(exc)[:200])
        return CHECK_ERROR
    return CHECK_OK


async def run_health_checks() -> dict[str, str]:
    """Fuehrt alle Pruefungen parallel aus und liefert {dienst: "ok"|"error"}."""
    names = list(CHECKS)
    results = await asyncio.gather(*(_run_check(name, CHECKS[name]) for name in names))
    return dict(zip(names, results, strict=True))
