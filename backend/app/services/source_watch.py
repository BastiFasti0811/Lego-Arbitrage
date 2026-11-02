"""Nutzarbeit je Angebotsquelle: liefert ein Scan, der laeuft, auch Angebote?

Der Heartbeat sieht nur, ob die Scrape-Tasks durchlaufen. Beide fangen ihre
Fehler selbst und melden Erfolg: vom 01.09. bis 02.11.2026 lief die
Kleinanzeigen-Lane zwoelfmal am Tag "erfolgreich" mit 0 Angeboten, weil der
Parser das umgebaute Seitenlayout nicht mehr las, und eBay lieferte seit dem
19.10. nichts mehr. Diese Pruefung misst deshalb an den Angeboten selbst.

Gemessen wird "gesehen" (``last_seen_at``), nicht "neu" (``discovered_at``):
ein Scan, der dieselben Anzeigen wiederfindet, arbeitet. Amazon brachte vom
25.10. bis 01.11.2026 zwei neue Angebote, sah aber bis zum Abend des 01.11.
weiter Angebote; eine Pruefung auf "neu" haette an den meisten dieser Tage
falsch alarmiert. Die Zahl der neuen Angebote steht trotzdem im Status, als
Hinweis auf den Marktfluss.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.heartbeat import TaskHeartbeat
from app.models.offer import Offer
from app.services.heartbeat import CatawikiScanWatch, TaskHealth

BERLIN = ZoneInfo("Europe/Berlin")

SCRAPE_ALL_TASK = "app.tasks.scrape_daily.scrape_all_watched_sets"
SCRAPE_KLEINANZEIGEN_TASK = "app.tasks.scrape_daily.scrape_kleinanzeigen_watched"

# Ein Tag: zwoelf Kleinanzeigen-Laeufe, vier volle Laeufe fuer eBay und Amazon.
YIELD_WINDOW_HOURS = 24


@dataclass(frozen=True)
class OfferSource:
    platform: str  # Wert in offers.platform
    label: str
    scan_tasks: tuple[str, ...]  # Celery-Tasks, die diese Quelle abfragen


# BrickMerge steht zwar in OFFER_SCRAPERS, hat auf Prod aber nie ein Angebot
# gespeichert (0 Zeilen, Stand 02.11.2026); ein Waechter darauf alarmierte
# nur dauerhaft. Catawiki laeuft ueber den Heimscanner und hat eigene
# Pruefungen (evaluate_catawiki_scan), hier steht es nur in der Uebersicht.
OFFER_SOURCES: tuple[OfferSource, ...] = (
    OfferSource("KLEINANZEIGEN", "Kleinanzeigen", (SCRAPE_KLEINANZEIGEN_TASK, SCRAPE_ALL_TASK)),
    OfferSource("EBAY", "eBay", (SCRAPE_ALL_TASK,)),
    OfferSource("AMAZON", "Amazon", (SCRAPE_ALL_TASK,)),
)


@dataclass(frozen=True)
class OfferActivity:
    """Was die offers-Tabelle je Plattform ueber das Zeitfenster sagt."""

    platform: str
    last_seen_at: datetime | None
    last_new_at: datetime | None
    seen_in_window: int
    new_in_window: int


@dataclass(frozen=True)
class SourceStatus:
    platform: str
    label: str
    status: str  # ok | no_yield | not_scanned | idle | not_configured
    last_scan_at: datetime | None
    last_offer_seen_at: datetime | None
    last_offer_new_at: datetime | None
    offers_seen_24h: int | None
    offers_new_24h: int | None
    detail: str
    problem: TaskHealth | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _when(value: datetime | None) -> str:
    if value is None:
        return "nie"
    return value.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")


def offer_activity_query(since: datetime):
    """Je Plattform: zuletzt gesehen, zuletzt neu, Anzahl gesehen/neu seit ``since``."""
    return select(
        Offer.platform,
        func.max(Offer.last_seen_at),
        func.max(Offer.discovered_at),
        func.sum(case((Offer.last_seen_at >= since, 1), else_=0)),
        func.sum(case((Offer.discovered_at >= since, 1), else_=0)),
    ).group_by(Offer.platform)


def activity_from_rows(rows: Iterable[Sequence]) -> dict[str, OfferActivity]:
    return {
        row[0]: OfferActivity(
            platform=row[0],
            last_seen_at=_aware(row[1]),
            last_new_at=_aware(row[2]),
            seen_in_window=int(row[3] or 0),
            new_in_window=int(row[4] or 0),
        )
        for row in rows
    }


async def load_offer_activity(session: AsyncSession, now: datetime) -> dict[str, OfferActivity]:
    since = now - timedelta(hours=YIELD_WINDOW_HOURS)
    return activity_from_rows((await session.execute(offer_activity_query(since))).all())


def _last_scan(source: OfferSource, heartbeats: Mapping[str, TaskHeartbeat]) -> datetime | None:
    """Letzter ERFOLGREICHE Lauf eines Tasks, der die Quelle abfragt.

    Erfolg ist hier genau der Fall, um den es geht: der Task meldet "lief",
    und die Frage ist, ob dabei etwas herauskam. Ein Fehlschlag ist Sache der
    Task-Pruefung (status failing).
    """
    times = [
        _aware(heartbeats[name].last_success_at)
        for name in source.scan_tasks
        if name in heartbeats and heartbeats[name].last_success_at is not None
    ]
    return max(times) if times else None


def evaluate_offer_sources(
    heartbeats: Sequence[TaskHeartbeat],
    activity: Mapping[str, OfferActivity],
    active_watchlist: int,
    now: datetime,
) -> list[SourceStatus]:
    """Je Quelle: lief ein Scan im Fenster, und wurde dabei ein Angebot gesehen?

    Problem (``pipeline.offers_<plattform>``) nur bei: aktive Watchlist,
    mindestens ein erfolgreicher Scan in den letzten 24 h, aber kein Angebot
    der Plattform in denselben 24 h gesehen. Rein (kein I/O).
    """
    by_name = {hb.task_name: hb for hb in heartbeats}
    window = timedelta(hours=YIELD_WINDOW_HOURS)
    statuses: list[SourceStatus] = []
    for source in OFFER_SOURCES:
        act = activity.get(source.platform) or OfferActivity(source.platform, None, None, 0, 0)
        last_scan = _last_scan(source, by_name)
        problem = None
        if active_watchlist <= 0:
            status, detail = "idle", "Watchlist leer, nichts zu scannen"
        elif last_scan is None or now - last_scan > window:
            status = "not_scanned"
            detail = f"Kein erfolgreicher Scan in {YIELD_WINDOW_HOURS} h (letzter: {_when(last_scan)})"
        elif act.seen_in_window > 0:
            status = "ok"
            detail = f"{act.seen_in_window} Angebote in {YIELD_WINDOW_HOURS} h gesehen, {act.new_in_window} neu"
        else:
            status = "no_yield"
            detail = (
                f"In {YIELD_WINDOW_HOURS} h gescannt (zuletzt {_when(last_scan)}), aber kein Angebot "
                f"gesehen; letztes: {_when(act.last_seen_at)}"
            )
            problem = TaskHealth(
                task_name=f"pipeline.offers_{source.platform.lower()}",
                status="stale",
                last_success_at=act.last_seen_at,
                last_run_at=last_scan,
                last_status=None,
                age_seconds=(now - act.last_seen_at).total_seconds() if act.last_seen_at else None,
                max_age_seconds=int(window.total_seconds()),
                detail=f"{source.label}: {detail}",
            )
        statuses.append(
            SourceStatus(
                platform=source.platform,
                label=source.label,
                status=status,
                last_scan_at=last_scan,
                last_offer_seen_at=act.last_seen_at,
                last_offer_new_at=act.last_new_at,
                offers_seen_24h=act.seen_in_window,
                offers_new_24h=act.new_in_window,
                detail=detail,
                problem=problem,
            )
        )
    return statuses


def catawiki_source_status(watch: CatawikiScanWatch) -> SourceStatus:
    """Catawiki fuer die Uebersicht. Alarme kommen aus evaluate_catawiki_scan."""
    last_scan_at = _aware(watch.last_scan_at)
    if not watch.scan_urls_configured:
        missing = ["keine Scan-URLs"]
        if watch.home_runner and not watch.token_configured:
            missing.append("kein Heimscanner-Token")
        status, detail = "not_configured", "Nicht eingerichtet: " + ", ".join(missing)
    elif last_scan_at is None:
        status, detail = "not_scanned", "Noch nie ein Scan angekommen"
    elif watch.last_scan_lots > 0:
        status, detail = "ok", f"Letzter Scan {_when(last_scan_at)}: {watch.last_scan_lots} Lose"
    else:
        status, detail = "no_yield", f"Letzter Scan {_when(last_scan_at)} ohne Lose ({watch.last_scan_status})"
    return SourceStatus(
        platform="CATAWIKI",
        label="Catawiki",
        status=status,
        last_scan_at=last_scan_at,
        # Die Lose eines Scans sind zum Scanzeitpunkt gesehen; ein eigenes
        # "zuletzt gesehen" fuehrt Catawiki nicht.
        last_offer_seen_at=last_scan_at if watch.last_scan_lots > 0 else None,
        last_offer_new_at=None,
        offers_seen_24h=None,
        offers_new_24h=None,
        detail=detail,
    )
