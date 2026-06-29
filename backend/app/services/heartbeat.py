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

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.heartbeat import TaskHeartbeat

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
}

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


async def load_heartbeats(session: AsyncSession) -> list[TaskHeartbeat]:
    """Load all stored heartbeats using the caller's session."""
    result = await session.execute(select(TaskHeartbeat))
    return list(result.scalars().all())


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
