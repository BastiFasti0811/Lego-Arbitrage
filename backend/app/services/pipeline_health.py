"""Was der stuendliche Watchdog und /api/system/status ueber die Pipeline wissen.

Beide lasen frueher Verschiedenes: der Watchdog meldete per Telegram auch
Nutzarbeits-Probleme (Preisdaten, Bewertungsabdeckung, Catawiki), der
Status-Endpoint zeigte nur die Task-Heartbeats. Bei totem Telegram-Token sah
damit niemand die Nutzarbeits-Probleme. Jetzt sammelt eine Funktion fuer beide.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceRecord, WatchlistItem
from app.models.heartbeat import TaskHeartbeat
from app.models.valuation_run import ValuationRun
from app.services.heartbeat import (
    HealthReport,
    TaskHealth,
    evaluate_catawiki_scan,
    evaluate_data_freshness,
    evaluate_health,
    evaluate_valuation_coverage,
    load_heartbeats,
)
from app.services.remote_scan import load_scan_watch
from app.services.source_watch import (
    SourceStatus,
    catawiki_source_status,
    evaluate_offer_sources,
    load_offer_activity,
)

logger = structlog.get_logger()


@dataclass
class PipelineState:
    checked_at: datetime
    report: HealthReport  # je ueberwachtem Task
    heartbeats: list[TaskHeartbeat]
    problems: list[TaskHealth]  # Task-Probleme plus synthetische Nutzarbeits-Probleme (pipeline.*)
    sources: list[SourceStatus]  # je Angebotsquelle: letzter Scan, letztes Angebot


async def collect_pipeline_state(session: AsyncSession, now: datetime) -> PipelineState:
    """Nur lesend. Catawiki kommt zuletzt: scheitert dessen Abfrage, ist die
    Transaktion der Session verbraucht, und jede Abfrage danach schluege fehl."""
    heartbeats = await load_heartbeats(session)
    latest_price_at = (await session.execute(select(func.max(PriceRecord.scraped_at)))).scalar_one_or_none()
    active_watchlist = (
        await session.execute(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.is_active))
    ).scalar_one()
    latest_run = (
        await session.execute(
            select(ValuationRun)
            .where(ValuationRun.finished_at.is_not(None))
            .order_by(ValuationRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    offer_activity = await load_offer_activity(session, now)
    try:
        catawiki_watch = await load_scan_watch(session)
    except Exception as exc:  # noqa: BLE001 -- Catawiki darf den restlichen Watchdog nicht verhindern
        logger.warning("health.catawiki_watch_failed", error=repr(exc)[:300])
        catawiki_watch = None

    report = evaluate_health(heartbeats, now)

    # Nutzarbeit on top of task success: a green pipeline that stops writing
    # price data is exactly the failure mode heartbeats alone cannot see.
    problems = list(report.problems)
    freshness_problem = evaluate_data_freshness(latest_price_at, active_watchlist, now)
    if freshness_problem is not None:
        problems.append(freshness_problem)

    coverage_problem = evaluate_valuation_coverage(latest_run, now)
    if coverage_problem is not None:
        problems.append(coverage_problem)

    # Je Quelle: Scans laufen "erfolgreich", aber kommt auch etwas an?
    sources = evaluate_offer_sources(heartbeats, offer_activity, active_watchlist, now)
    problems.extend(source.problem for source in sources if source.problem is not None)

    # Catawiki kommt vom Heimscanner, nicht aus einem Beat-Task: dessen Ausfall
    # sieht kein Heartbeat (Kritiker #4/#9 der Feedback-Runde 20.10.2026).
    if catawiki_watch is not None:
        problems.extend(evaluate_catawiki_scan(catawiki_watch, now))
        sources.append(catawiki_source_status(catawiki_watch))

    return PipelineState(
        checked_at=now,
        report=report,
        heartbeats=heartbeats,
        problems=problems,
        sources=sources,
    )
