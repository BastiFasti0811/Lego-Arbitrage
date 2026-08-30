"""Pipeline-health heartbeat service.

Records when Celery tasks last ran / succeeded and evaluates whether the
scheduled pipeline is healthy. A dedicated ``NullPool`` engine is used for
writes because Celery bridges async work via repeated ``asyncio.run(...)``
calls (a fresh event loop per task); a pooled connection bound to a previous
loop would otherwise raise "attached to a different loop".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.heartbeat import TaskHeartbeat

# Nur für den Typ gebraucht: heartbeat.py ist Service-Schicht, ValuationRun
# ist Modell-Schicht. Ein echter Import würde das zur Laufzeit koppeln, ohne
# dass die Funktion darunter je etwas anderes als den Typ braucht.
if TYPE_CHECKING:
    from app.models.valuation_run import ValuationRun

logger = structlog.get_logger()

# Maximum age (hours) of a successful run before a scheduled task counts as
# "stale". Derived from each task's beat cadence + a tolerance buffer. The
# weekly retrain placeholder is intentionally NOT monitored.
MONITORED_TASKS: dict[str, int] = {
    "app.tasks.scrape_daily.scrape_all_watched_sets": 8,       # every 6h
    "app.tasks.analyze_new.analyze_new_offers": 2,             # every 30min
    "app.tasks.analyze_new.send_daily_summary_task": 26,       # daily 20:00
    "app.tasks.scrape_daily.refresh_known_set_metadata": 26,   # daily 04:15
    "app.tasks.auction_watch.refresh_auction_watchlist": 26,   # daily 08:20
    "app.tasks.catawiki_scan.scan_configured_categories": 26,  # daily 08:40
    "app.tasks.update_inventory.update_inventory_valuations": 8,  # every 6h
    "app.tasks.scrape_daily.scrape_kleinanzeigen_watched": 5,     # every 2h
    "app.tasks.weekly_report.send_weekly_report_task": 194,       # weekly Sunday 18:00
}

# Die Tasks, die Angebote in die DB schreiben. Der Live Feed meldet ihren
# letzten Erfolg als "Letzter Scan" — was ein Wochenreport oder ein
# Metadaten-Refresh treibt, sagt über die Frische der Angebote nichts.
SCAN_TASKS: frozenset[str] = frozenset(
    {
        "app.tasks.scrape_daily.scrape_all_watched_sets",
        "app.tasks.scrape_daily.scrape_kleinanzeigen_watched",
    }
)

# Nutzarbeit threshold: a full day of 6h scrape cycles plus slack. Past this,
# a green pipeline that writes no prices counts as dead.
PRICE_DATA_MAX_AGE_HOURS = 26

_DETAIL_MAX_LEN = 2000

# Dedicated NullPool engine/session for cross-event-loop safety (see module docstring).
_hb_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_hb_session = async_sessionmaker(_hb_engine, class_=AsyncSession, expire_on_commit=False)


@dataclass
class TaskHealth:
    task_name: str
    status: str  # ok | stale | failing | pending
    last_success_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    age_seconds: float | None
    max_age_seconds: int
    detail: str | None


@dataclass
class HealthReport:
    healthy: bool
    checked_at: datetime
    tasks: list[TaskHealth]

    @property
    def problems(self) -> list[TaskHealth]:
        """Tasks in an alertable state (stale or failing). 'pending' never alerts."""
        return [t for t in self.tasks if t.status in ("stale", "failing")]


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    return value if len(value) <= _DETAIL_MAX_LEN else value[: _DETAIL_MAX_LEN - 1] + "…"


async def record_heartbeat(task_name: str, *, success: bool, detail: object | None = None) -> None:
    """Upsert a task's heartbeat. Never raises — failures here must not break tasks.

    Uses a PostgreSQL ``ON CONFLICT`` upsert (atomic, no read-modify-write race)
    so concurrent first-time inserts for the same task cannot collide.
    """
    now = datetime.now(UTC)
    detail_str = _truncate(str(detail)) if detail is not None else None
    try:
        if success:
            insert_values = {
                "task_name": task_name,
                "last_run_at": now,
                "last_success_at": now,
                "last_status": "success",
                "last_detail": detail_str,
                "consecutive_failures": 0,
            }
            update_values = {
                "last_run_at": now,
                "last_success_at": now,
                "last_status": "success",
                "last_detail": detail_str,
                "consecutive_failures": 0,
            }
        else:
            insert_values = {
                "task_name": task_name,
                "last_run_at": now,
                "last_status": "error",
                "last_detail": detail_str,
                "consecutive_failures": 1,
            }
            update_values = {
                "last_run_at": now,
                "last_status": "error",
                "last_detail": detail_str,
                # increment the stored count; leave last_success_at untouched
                "consecutive_failures": TaskHeartbeat.consecutive_failures + 1,
            }
        stmt = pg_insert(TaskHeartbeat).values(**insert_values)
        stmt = stmt.on_conflict_do_update(index_elements=["task_name"], set_=update_values)
        async with _hb_session() as session:
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — heartbeat must never break a task
        logger.warning("heartbeat.record_failed", task=task_name, error=str(exc))


async def load_scan_heartbeats(session: AsyncSession) -> list[TaskHeartbeat]:
    """Only the scrape lanes — the live feed polls this every 30 seconds."""
    result = await session.execute(select(TaskHeartbeat).where(TaskHeartbeat.task_name.in_(SCAN_TASKS)))
    return list(result.scalars().all())


async def load_heartbeats(session: AsyncSession) -> list[TaskHeartbeat]:
    """Load all stored heartbeats using the caller's session."""
    result = await session.execute(select(TaskHeartbeat))
    return list(result.scalars().all())


def latest_scan_success(heartbeats: Sequence[TaskHeartbeat]) -> datetime | None:
    """When a scrape task last reported completion, or None.

    Reports that the pipeline RAN, not that it brought anything back. Both
    scrape tasks catch a 403, set ``aborted`` and return normally, so Celery's
    ``task_success`` fires and ``last_success_at`` moves forward for a run that
    fetched zero offers. It is also a max over two independent lanes, so a dead
    six-hour lane hides behind a live two-hour one, and the analyze task that
    writes recommendations lags the scrape by up to 30 minutes.

    Three ways to read too fresh, which is why ``ScoutResponse`` carries
    ``last_offer_seen_at`` beside it — that one is measured on the offers
    themselves and cannot inherit any of this.

    ``last_success_at`` rather than ``last_run_at`` all the same: an errored run
    should not push the number forward, and an earlier success still counts —
    the offers it stored are still in the database.
    """
    successes = [
        hb.last_success_at for hb in heartbeats if hb.task_name in SCAN_TASKS and hb.last_success_at is not None
    ]
    return max(successes) if successes else None


def evaluate_health(heartbeats: Sequence[TaskHeartbeat], now: datetime) -> HealthReport:
    """Pure classification of the monitored pipeline tasks. No I/O — unit-testable.

    - pending: task has never reported a run (e.g. fresh deploy) → surfaced, not alerted.
    - failing: most recent run errored.
    - stale:   last success older than the task's allowed max age.
    - ok:      recent successful run.
    """
    by_name = {hb.task_name: hb for hb in heartbeats}
    tasks: list[TaskHealth] = []

    for name, max_hours in MONITORED_TASKS.items():
        max_age = max_hours * 3600
        hb = by_name.get(name)

        if hb is None or hb.last_run_at is None:
            tasks.append(TaskHealth(name, "pending", None, None, None, None, max_age, None))
            continue

        if hb.last_status == "error":
            age = (now - hb.last_run_at).total_seconds()
            status = "failing"
        else:
            reference = hb.last_success_at or hb.last_run_at
            age = (now - reference).total_seconds()
            status = "stale" if age > max_age else "ok"

        tasks.append(
            TaskHealth(
                task_name=name,
                status=status,
                last_success_at=hb.last_success_at,
                last_run_at=hb.last_run_at,
                last_status=hb.last_status,
                age_seconds=age,
                max_age_seconds=max_age,
                detail=hb.last_detail,
            )
        )

    healthy = not any(t.status in ("stale", "failing") for t in tasks)
    return HealthReport(healthy=healthy, checked_at=now, tasks=tasks)


def evaluate_data_freshness(
    latest_price_at: datetime | None,
    active_watchlist: int,
    now: datetime,
) -> TaskHealth | None:
    """Nutzarbeit check: an active watchlist must produce recent price writes.

    Catches the failure mode heartbeats cannot see — tasks reporting success
    while writing nothing. Pure (no I/O); returns a synthetic problem or None.
    """
    if active_watchlist <= 0:
        return None

    max_age = PRICE_DATA_MAX_AGE_HOURS * 3600
    if latest_price_at is None:
        return TaskHealth(
            task_name="pipeline.price_data_freshness",
            status="stale",
            last_success_at=None,
            last_run_at=None,
            last_status=None,
            age_seconds=None,
            max_age_seconds=max_age,
            detail="Watchlist aktiv, aber noch nie ein Preis geschrieben",
        )

    if latest_price_at.tzinfo is None:
        latest_price_at = latest_price_at.replace(tzinfo=UTC)
    age = (now - latest_price_at).total_seconds()
    if age <= max_age:
        return None
    return TaskHealth(
        task_name="pipeline.price_data_freshness",
        status="stale",
        last_success_at=latest_price_at,
        last_run_at=None,
        last_status=None,
        age_seconds=age,
        max_age_seconds=max_age,
        detail=f"Letzter Preis vor {age / 3600:.1f} h",
    )


# Unterhalb dieser Anzahl sagt der Anteil nichts: bei zwei Sets ist ein
# Übersprung schon die Hälfte.
MIN_ITEMS_FOR_COVERAGE_CHECK = 5
MAX_SKIPPED_SHARE = 0.5


def evaluate_valuation_coverage(run: "ValuationRun | None", now: datetime) -> TaskHealth | None:
    """Nutzarbeit der Bewertung: ein Lauf muss Werte erzeugen, nicht nur laufen.

    Der Heartbeat sieht nur, dass der Task durchlief. Ein Set bleibt auf zwei
    Wegen ohne Marktwert — übersprungen (Quellenlage reicht nicht) oder
    fehlgeschlagen (Exception im Bewertungscode) —, und beide zählen hier
    gegen den Anteil: ein Lauf, der nur noch fehlschlägt statt überspringt,
    ist derselbe Ausfall wie der, der fünf Monate unbemerkt blieb, nur mit
    anderer Fehlerart. Rein (kein I/O); gibt ein synthetisches Problem oder
    None zurück.
    """
    if run is None or run.items_total < MIN_ITEMS_FOR_COVERAGE_CHECK:
        return None

    without_value = run.items_skipped + run.items_failed
    share = without_value / run.items_total
    if share <= MAX_SKIPPED_SHARE:
        return None

    return TaskHealth(
        task_name="pipeline.valuation_coverage",
        status="stale",
        last_success_at=run.started_at,
        last_run_at=run.started_at,
        last_status=None,
        age_seconds=(now - run.started_at).total_seconds() if run.started_at else None,
        max_age_seconds=0,
        detail=(
            f"Letzter Bewertungslauf: {without_value} von {run.items_total} Sets ohne "
            f"Marktwert ({run.items_skipped} übersprungen, {run.items_failed} "
            f"fehlgeschlagen) — Quellenlage im Protokoll prüfen"
        ),
    )


def filter_unthrottled(
    problems: Sequence[TaskHealth],
    by_name: dict[str, TaskHeartbeat],
    cutoff: datetime,
) -> list[TaskHealth]:
    """Return the problems eligible to alert now — i.e. not alerted since ``cutoff``.

    Pure (no I/O). Callers persist ``last_alerted_at`` only AFTER a successful
    send, so a failed/no-op notification does not burn the re-alert window.
    """
    eligible: list[TaskHealth] = []
    for problem in problems:
        hb = by_name.get(problem.task_name)
        last_alerted = hb.last_alerted_at if hb is not None else None
        if last_alerted is not None and last_alerted.tzinfo is None:
            last_alerted = last_alerted.replace(tzinfo=UTC)
        if last_alerted is None or last_alerted < cutoff:
            eligible.append(problem)
    return eligible
