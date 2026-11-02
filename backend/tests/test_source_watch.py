"""Nutzarbeits-Watchdog je Angebotsquelle (Ticket #27).

Die Eingaben sind der Prod-Stand vom 02.11.2026, 09:15 Berlin: Heartbeats
wortgleich aus task_heartbeats, Angebotszeiten aus offers. Die Abfrage laeuft
gegen echte SQLite-Zeilen, damit Spalten, Gruppierung und Fenster stimmen.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import system
from app.models import LegoSet, Offer
from app.models.heartbeat import TaskHeartbeat
from app.services import pipeline_health
from app.services.heartbeat import CatawikiScanWatch
from app.services.source_watch import (
    SCRAPE_ALL_TASK,
    SCRAPE_KLEINANZEIGEN_TASK,
    OfferActivity,
    activity_from_rows,
    catawiki_source_status,
    evaluate_offer_sources,
    offer_activity_query,
)
from app.tasks import health_check

NOW = datetime(2026, 11, 2, 8, 15, 2, tzinfo=UTC)  # check_pipeline_health, 09:15 Berlin


def _heartbeat(name, at, detail):
    hb = TaskHeartbeat(task_name=name)
    hb.last_run_at = at
    hb.last_success_at = at
    hb.last_status = "success"
    hb.last_detail = detail
    hb.consecutive_failures = 0
    hb.last_alerted_at = None
    return hb


PROD_HEARTBEATS = [
    _heartbeat(
        SCRAPE_KLEINANZEIGEN_TASK,
        datetime(2026, 11, 2, 7, 11, 57, 265401, tzinfo=UTC),
        "{'total_sets': 31, 'offers': 0, 'errors': [], 'aborted': False}",
    ),
    _heartbeat(
        SCRAPE_ALL_TASK,
        datetime(2026, 11, 2, 5, 0, 51, 631551, tzinfo=UTC),
        "{'total_sets': 31, 'success': 0, 'errors': 1, 'aborted': True}",
    ),
]

# Prod 20.10.2026: weder Token noch URLs.
CATAWIKI_OFF = CatawikiScanWatch(
    home_runner=True, token_configured=False, scan_urls_configured=False, frequency="off",
    config_changed_at=None, runner_seen_at=None, last_scan_at=None, last_scan_status=None,
    last_scan_lots=0, last_scan_errors=[],
)


# ── Abfrage gegen echte Zeilen ───────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in (LegoSet.__table__, Offer.__table__):
        table.create(engine)
    session = Session(engine, expire_on_commit=False)
    yield session
    session.close()
    engine.dispose()


def _offer(lego_set, platform, url, discovered_at, last_seen_at):
    return Offer(
        set_id=lego_set.id, platform=platform, offer_url=url, offer_title="LEGO", price_eur=20.0,
        discovered_at=discovered_at, last_seen_at=last_seen_at,
    )


def _prod_rows(db):
    """Die letzten Zeilen je Plattform, wie sie auf Prod am 02.11. standen."""
    lego_set = LegoSet(set_number="75388", set_name="Jedi Bob's Starfighter", theme="Star Wars", release_year=2024)
    db.add(lego_set)
    db.flush()
    db.add_all([
        # Kleinanzeigen: letzter Fund am 01.09., danach zwei Monate nichts.
        _offer(lego_set, "KLEINANZEIGEN", "https://www.kleinanzeigen.de/s-anzeige/a/1-23-1",
               datetime(2026, 8, 30, 10, 0, tzinfo=UTC), datetime(2026, 9, 1, 8, 10, 0, 244146, tzinfo=UTC)),
        # eBay: zuletzt am 19.10. gesehen.
        _offer(lego_set, "EBAY", "https://www.ebay.de/itm/1", datetime(2026, 10, 19, 4, 0, tzinfo=UTC),
               datetime(2026, 10, 19, 4, 0, 0, 97197, tzinfo=UTC)),
        # Amazon: alt entdeckt, aber gestern Abend wiedergesehen -- und einer neu.
        _offer(lego_set, "AMAZON", "https://www.amazon.de/dp/B0CW1", datetime(2026, 10, 14, 5, 0, tzinfo=UTC),
               datetime(2026, 11, 1, 18, 47, 50, 216996, tzinfo=UTC)),
        _offer(lego_set, "AMAZON", "https://www.amazon.de/dp/B0CW2", datetime(2026, 11, 1, 18, 47, 50, tzinfo=UTC),
               datetime(2026, 11, 1, 18, 47, 50, 216996, tzinfo=UTC)),
    ])
    db.commit()


def _activity(db, now=NOW):
    rows = db.execute(offer_activity_query(now - timedelta(hours=24))).all()
    return activity_from_rows(rows)


def test_the_query_reads_last_seen_and_counts_per_platform(db):
    _prod_rows(db)

    activity = _activity(db)

    assert set(activity) == {"KLEINANZEIGEN", "EBAY", "AMAZON"}
    ka = activity["KLEINANZEIGEN"]
    assert ka.last_seen_at == datetime(2026, 9, 1, 8, 10, 0, 244146, tzinfo=UTC)
    assert (ka.seen_in_window, ka.new_in_window) == (0, 0)
    amazon = activity["AMAZON"]
    assert (amazon.seen_in_window, amazon.new_in_window) == (2, 1)
    assert amazon.last_new_at == datetime(2026, 11, 1, 18, 47, 50, tzinfo=UTC)


# ── Bewertung ────────────────────────────────────────────────────────


def _by_platform(statuses):
    return {status.platform: status for status in statuses}


def test_prod_state_of_2_november_alerts_kleinanzeigen_and_ebay(db):
    _prod_rows(db)

    statuses = _by_platform(evaluate_offer_sources(PROD_HEARTBEATS, _activity(db), 31, NOW))

    ka = statuses["KLEINANZEIGEN"]
    assert ka.status == "no_yield"
    # Zuletzt gescannt hat die 2h-Lane, nicht der 6h-Lauf.
    assert ka.last_scan_at == datetime(2026, 11, 2, 7, 11, 57, 265401, tzinfo=UTC)
    assert ka.problem.task_name == "pipeline.offers_kleinanzeigen"
    assert ka.problem.status == "stale"
    assert ka.problem.detail == (
        "Kleinanzeigen: In 24 h gescannt (zuletzt 02.11.2026 08:11), aber kein Angebot gesehen; "
        "letztes: 01.09.2026 10:10"
    )
    assert ka.problem.age_seconds == pytest.approx((NOW - ka.last_offer_seen_at).total_seconds())
    # Telegram schneidet das Detail bei 120 Zeichen ab.
    assert len(ka.problem.detail) <= 120

    ebay = statuses["EBAY"]
    assert ebay.status == "no_yield"
    assert ebay.problem.task_name == "pipeline.offers_ebay"
    assert "letztes: 19.10.2026 06:00" in ebay.problem.detail

    amazon = statuses["AMAZON"]
    assert amazon.status == "ok" and amazon.problem is None
    assert amazon.detail == "2 Angebote in 24 h gesehen, 1 neu"


def test_a_source_that_finds_something_again_is_quiet():
    activity = {"KLEINANZEIGEN": OfferActivity("KLEINANZEIGEN", NOW - timedelta(minutes=50), NOW, 87, 12)}

    statuses = _by_platform(evaluate_offer_sources(PROD_HEARTBEATS, activity, 31, NOW))

    assert statuses["KLEINANZEIGEN"].status == "ok"
    assert statuses["KLEINANZEIGEN"].problem is None


def test_without_a_scan_in_the_window_the_task_check_speaks_not_this_one():
    # Steht die Lane, meldet evaluate_health "stale"; doppelt zu melden hilft nicht.
    old = [_heartbeat(SCRAPE_KLEINANZEIGEN_TASK, NOW - timedelta(hours=30), None),
           _heartbeat(SCRAPE_ALL_TASK, NOW - timedelta(hours=26), None)]

    statuses = _by_platform(evaluate_offer_sources(old, {}, 31, NOW))

    assert {s.status for s in statuses.values()} == {"not_scanned"}
    assert all(s.problem is None for s in statuses.values())


def test_a_failed_run_is_no_scan():
    failed = _heartbeat(SCRAPE_ALL_TASK, None, "boom")
    failed.last_run_at = NOW - timedelta(hours=1)
    failed.last_status = "error"

    statuses = _by_platform(evaluate_offer_sources([failed], {}, 31, NOW))

    assert statuses["EBAY"].status == "not_scanned"


def test_an_empty_watchlist_scans_nothing_and_alerts_nothing():
    statuses = evaluate_offer_sources(PROD_HEARTBEATS, {}, 0, NOW)
    assert {s.status for s in statuses} == {"idle"}
    assert all(s.problem is None for s in statuses)


def test_a_source_that_never_delivered_says_so():
    statuses = _by_platform(evaluate_offer_sources(PROD_HEARTBEATS, {}, 31, NOW))

    amazon = statuses["AMAZON"]
    assert amazon.status == "no_yield"
    assert amazon.problem.age_seconds is None
    assert amazon.problem.detail.endswith("letztes: nie")


def test_naive_timestamps_from_the_database_count_as_utc():
    naive = _heartbeat(SCRAPE_ALL_TASK, datetime(2026, 11, 2, 5, 0, 51), None)
    activity = {"EBAY": OfferActivity("EBAY", datetime(2026, 11, 2, 5, 0, 52), None, 3, 0)}

    statuses = _by_platform(evaluate_offer_sources([naive], activity, 31, NOW))

    assert statuses["EBAY"].status == "ok"


def test_catawiki_shows_up_as_not_configured():
    status = catawiki_source_status(CATAWIKI_OFF)
    assert (status.platform, status.status) == ("CATAWIKI", "not_configured")
    assert status.detail == "Nicht eingerichtet: keine Scan-URLs, kein Heimscanner-Token"
    assert status.problem is None


def test_catawiki_with_lots_counts_the_scan_as_a_find():
    scanned = NOW - timedelta(hours=1)
    watch = CatawikiScanWatch(
        home_runner=True, token_configured=True, scan_urls_configured=True, frequency="daily",
        config_changed_at=None, runner_seen_at=scanned, last_scan_at=scanned, last_scan_status="OK",
        last_scan_lots=14, last_scan_errors=[],
    )
    status = catawiki_source_status(watch)
    assert status.status == "ok" and status.last_offer_seen_at == scanned


# ── Watchdog und /api/system/status mit demselben Stand ─────────────


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _ProdSession:
    """Beantwortet die drei Einzelabfragen des Sammlers mit dem Prod-Stand."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        sql = str(statement)
        if "count(*)" in sql:
            return _Scalar(31)
        if "max(price_records.scraped_at)" in sql:
            return _Scalar(datetime(2026, 11, 2, 5, 0, 0, 100407, tzinfo=UTC))
        if "valuation_runs" in sql:
            return _Scalar(SimpleNamespace(items_total=49, items_skipped=2, items_failed=0,
                                           started_at=datetime(2026, 11, 2, 5, 30, tzinfo=UTC)))
        raise AssertionError(f"unerwartete Abfrage: {sql[:80]}")


@pytest.fixture
def prod_state(db, monkeypatch):
    _prod_rows(db)
    activity = _activity(db)
    monkeypatch.setattr(pipeline_health, "load_heartbeats", AsyncMock(return_value=PROD_HEARTBEATS))
    monkeypatch.setattr(pipeline_health, "load_offer_activity", AsyncMock(return_value=activity))
    monkeypatch.setattr(pipeline_health, "load_scan_watch", AsyncMock(return_value=CATAWIKI_OFF))


async def test_the_watchdog_alerts_per_source(prod_state, monkeypatch):
    sent = []

    async def _send(payload):
        sent.append(payload)
        return False  # Prod-Token ist tot (401): dann wird nichts als gemeldet markiert

    monkeypatch.setattr(health_check, "async_session", _ProdSession)
    monkeypatch.setattr(health_check, "send_pipeline_health_alert", _send)
    monkeypatch.setattr(health_check, "datetime", SimpleNamespace(now=lambda tz=None: NOW))

    result = await health_check._check_async()

    alerted = {problem["task_name"]: problem for problem in sent[0]}
    assert {"pipeline.offers_kleinanzeigen", "pipeline.offers_ebay"} <= set(alerted)
    assert "pipeline.offers_amazon" not in alerted
    assert alerted["pipeline.offers_kleinanzeigen"]["age_hours"] == pytest.approx(1488.1, abs=0.1)
    assert result["healthy"] is False and result["notification_sent"] is False


async def test_the_status_endpoint_shows_last_scan_and_last_offer_per_source(prod_state, monkeypatch):
    monkeypatch.setattr(system, "datetime", SimpleNamespace(now=lambda tz=None: NOW))

    response = await system.pipeline_status(_ProdSession())

    assert response.healthy is False
    sources = {source.platform: source for source in response.sources}
    assert list(sources) == ["KLEINANZEIGEN", "EBAY", "AMAZON", "CATAWIKI"]
    ka = sources["KLEINANZEIGEN"]
    assert ka.status == "no_yield"
    assert ka.last_scan_at == datetime(2026, 11, 2, 7, 11, 57, 265401, tzinfo=UTC)
    assert ka.last_offer_seen_at == datetime(2026, 9, 1, 8, 10, 0, 244146, tzinfo=UTC)
    assert sources["CATAWIKI"].status == "not_configured"
    # Die Warnung steht auch in tasks, wo die Statusanzeige im Kopf sie liest.
    by_name = {task.task_name: task for task in response.tasks}
    assert by_name["pipeline.offers_kleinanzeigen"].status == "stale"
    assert by_name["pipeline.offers_ebay"].detail.startswith("eBay: In 24 h gescannt")
    # Die ueberwachten Tasks stehen weiter vollstaendig vorne.
    assert response.tasks[0].task_name.startswith("app.tasks.")
    assert date(2026, 11, 2) == response.checked_at.date()
