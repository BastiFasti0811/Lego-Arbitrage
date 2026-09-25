"""Catawiki-Scan ueber den Heimrechner: Token, Nutzdaten, Zeitplan, Auswertung."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import auctions
from app.api.routes import remote_scan as remote_scan_routes
from app.services import remote_scan
from app.services.catawiki import PARSER_VERSION
from app.tasks import catawiki_scan

JOB = "b" * 32

TOKEN = "x" * 32
LOT = {
    "lot_id": "106997010",
    "title": "Lego Set - 10352 - The Simpsons - Krusty Burger",
    "url": "https://www.catawiki.com/de/l/106997010-lego-set-10352",
    "category_url": "https://www.catawiki.com/de/a/1256209",
    "current_bid": 94.0,
    "shipping_eur": 12.0,
    "set_numbers": ["10352"],
    "condition": "NEW_SEALED",
    "details_verified": True,
    "buyer_fee_rate": 0.09,
    "buyer_fee_fixed": 3.0,
}


# --- Token ---------------------------------------------------------------------


@pytest.mark.parametrize(("provided", "expected", "ok"), [
    (TOKEN, TOKEN, True),
    ("falsch" * 6, TOKEN, False),
    (None, TOKEN, False),
    (TOKEN, None, False),
    ("kurz", "kurz", False),  # zu kurzes Token oeffnet nie etwas
])
def test_verify_runner_token(provided, expected, ok):
    assert remote_scan.verify_runner_token(provided, expected) is ok


async def test_runner_endpoints_reject_wrong_token(monkeypatch):
    monkeypatch.setattr(remote_scan_routes, "get_settings_map", AsyncMock(return_value={"remote_scan_token": TOKEN}))
    await remote_scan_routes.require_runner_token(f"Bearer {TOKEN}")
    with pytest.raises(HTTPException) as exc:
        await remote_scan_routes.require_runner_token("Bearer falsch-falsch-falsch-falsch")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException):
        await remote_scan_routes.require_runner_token(None)


def test_only_runner_paths_skip_the_cookie_check():
    from app.main import RUNNER_PATH_PREFIX

    assert RUNNER_PATH_PREFIX == "/api/remote-scan/runner/"
    # /status und /request brauchen weiter das Login-Cookie.
    assert not "/api/remote-scan/status".startswith(RUNNER_PATH_PREFIX)
    assert not "/api/remote-scan/request".startswith(RUNNER_PATH_PREFIX)


# --- Nutzdaten -----------------------------------------------------------------


def test_remote_lot_is_canonicalised_and_converted():
    lot = remote_scan.RemoteLot(**LOT)
    assert lot.url == "https://www.catawiki.com/de/l/106997010"
    candidate = lot.to_candidate()
    assert (candidate.current_bid, candidate.shipping_eur, candidate.condition) == (94.0, 12.0, "NEW_SEALED")


@pytest.mark.parametrize("changes", [
    {"url": "https://evil.example/de/l/106997010"},
    {"url": "https://www.catawiki.com/de/l/999"},  # andere Losnummer
    {"set_numbers": ["abc"]},
    {"condition": "MINT"},
    {"current_bid": -1},
    {"buyer_fee_rate": 2},
])
def test_remote_lot_rejects_foreign_or_implausible_data(changes):
    with pytest.raises(ValidationError):
        remote_scan.RemoteLot(**{**LOT, **changes})


def _payload(**changes):
    return {"job_id": JOB, "parser_version": PARSER_VERSION, "lots": [], "errors": [], **changes}


def test_results_payload_is_bounded():
    with pytest.raises(ValidationError):
        remote_scan.RemoteScanResults(**_payload(lots=[LOT] * 501))
    with pytest.raises(ValidationError):
        remote_scan.RemoteScanResults(**_payload(job_id="nicht-hex"))
    assert len(remote_scan.RemoteScanResults(**_payload(errors=["e" * 1000])).errors[0]) == 300


# --- Zeitplan ------------------------------------------------------------------


@pytest.mark.parametrize(("frequency", "last_scan", "now", "due"), [
    ("daily", None, datetime(2026, 9, 25, 7, 0, tzinfo=UTC), True),     # 09:00 Berlin
    ("daily", None, datetime(2026, 9, 25, 6, 0, tzinfo=UTC), False),    # 08:00 Berlin, zu frueh
    ("daily", datetime(2026, 9, 25, 6, 50, tzinfo=UTC), datetime(2026, 9, 25, 12, 0, tzinfo=UTC), False),
    ("daily", datetime(2026, 9, 24, 7, 0, tzinfo=UTC), datetime(2026, 9, 25, 7, 0, tzinfo=UTC), True),
    ("off", None, datetime(2026, 9, 25, 7, 0, tzinfo=UTC), False),
    ("weekly", None, datetime(2026, 9, 27, 7, 0, tzinfo=UTC), True),    # Sonntag
    ("weekly", None, datetime(2026, 9, 25, 7, 0, tzinfo=UTC), False),   # Freitag
])
def test_scheduled_scan_due(frequency, last_scan, now, due):
    assert remote_scan.scheduled_scan_due(frequency, last_scan, now) is due


# --- Auswertung auf Prod -------------------------------------------------------


async def test_evaluate_remote_scan_saves_and_notifies(monkeypatch):
    evaluated = auctions.AuctionDiscoverResult(
        source_platform="CATAWIKI", category_url=LOT["category_url"], lot_title=LOT["title"],
        source_url="https://www.catawiki.com/de/l/106997010", set_number="10352", current_bid=94.0,
        recommended_max_bid=150.0, can_bid_now=True,
    )
    evaluate = AsyncMock(return_value=evaluated)
    monkeypatch.setattr(catawiki_scan, "_evaluate_lot", evaluate)
    save = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "save_scan", save)
    monkeypatch.setattr(catawiki_scan, "get_settings_map",
                        AsyncMock(return_value={"telegram_bot_token": "t", "telegram_chat_id": "c"}))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(side_effect=lambda platform, rows: rows))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(catawiki_scan, "send_auction_discovery_summary", send)
    monkeypatch.setattr(catawiki_scan, "mark_notified", AsyncMock())
    finish = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "_finish_remote_job", finish)

    summary = await catawiki_scan._evaluate_remote_scan_async(
        _payload(lots=[LOT], errors=["Heimrechner: 1 Los 403"]),
    )
    finish.assert_awaited_once_with(JOB)

    lot_arg = evaluate.call_args.kwargs["lot"]
    assert lot_arg.details_verified and lot_arg.url == "https://www.catawiki.com/de/l/106997010"
    assert save.call_args.args[0] == "CATAWIKI"
    assert save.call_args.args[2] == ["Heimrechner: 1 Los 403"]
    assert (summary["discovered"], summary["notified"]) == (1, 1)


async def test_one_failing_lot_does_not_drop_the_others(monkeypatch):
    ok = auctions.AuctionDiscoverResult(
        source_platform="CATAWIKI", category_url=LOT["category_url"], lot_title="LEGO", source_url="x",
    )
    monkeypatch.setattr(catawiki_scan, "_evaluate_lot", AsyncMock(side_effect=[RuntimeError("boom"), ok]))
    save = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "save_scan", save)
    monkeypatch.setattr(catawiki_scan, "get_settings_map", AsyncMock(return_value={}))
    monkeypatch.setattr(catawiki_scan, "unnotified_results", AsyncMock(return_value=[]))
    monkeypatch.setattr(catawiki_scan, "_finish_remote_job", AsyncMock())

    second = {**LOT, "lot_id": "107053796", "url": "https://www.catawiki.com/de/l/107053796"}
    summary = await catawiki_scan._evaluate_remote_scan_async(_payload(lots=[LOT, second]))

    assert len(save.call_args.args[1]) == 1
    assert any("106997010" in error for error in summary["errors"])


# --- Auftrag, Anforderung, Status gegen echte Tabellen -------------------------


class _AsyncSession:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        self._session.commit()


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield _AsyncSession(session)


async def test_lease_is_released_even_when_evaluation_crashes(monkeypatch):
    monkeypatch.setattr(catawiki_scan, "_evaluate_lot", AsyncMock(side_effect=KeyboardInterrupt))
    finish = AsyncMock()
    monkeypatch.setattr(catawiki_scan, "_finish_remote_job", finish)
    with pytest.raises(KeyboardInterrupt):
        await catawiki_scan._evaluate_remote_scan_async(_payload(lots=[LOT]))
    finish.assert_awaited_once_with(JOB)


async def test_request_then_job_then_results_clear_the_request(db, monkeypatch):
    from app.models import AppSetting

    monkeypatch.setattr(remote_scan, "get_settings_map", AsyncMock(return_value={
        "catawiki_scan_urls": "https://www.catawiki.com/de/a/1256209\n",
        "catawiki_scan_frequency": "off", "catawiki_max_results_per_url": "30",
    }))
    db.add(AppSetting(key="remote_scan_token", value=TOKEN, is_secret=True, category="catawiki"))
    await db.commit()

    job = await remote_scan.runner_job(db)
    assert job["run"] is False and job["reason"] is None

    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    await remote_scan.request_scan(db, now=t0)
    job = await remote_scan.runner_job(db, now=t0)
    assert (job["run"], job["reason"], job["max_results_per_url"]) == (True, "requested", 30)
    assert job["scan_urls"] == ["https://www.catawiki.com/de/a/1256209"]
    assert job["job_id"] and job["parser_version"] == PARSER_VERSION

    # Review #33, Blocker: solange der Auftrag laeuft, kein zweiter (Folge-Poll nach 10 Min.).
    again = await remote_scan.runner_job(db, now=t0 + timedelta(minutes=10))
    assert again["run"] is False and again["job_id"] is None

    # Mit fester Uhr: ohne `now` lief der Auftrag gegen die echte Zeit ab, und
    # der Test fiel ab 25.09.2026 ~11:00 UTC auf jedem Rechner um.
    status = await remote_scan.scan_status(db, now=t0 + timedelta(minutes=10))
    assert status["token_configured"] and status["requested_at"] and status["runner_seen_at"]
    assert status["job"]["reason"] == "requested"

    # Neue Anforderung waehrend des Scans: bleibt nach der Lieferung stehen.
    await remote_scan.request_scan(db, now=t0 + timedelta(minutes=5))
    data = remote_scan.RemoteScanResults(**_payload(job_id=job["job_id"]))
    await remote_scan.accept_results(db, data, now=t0 + timedelta(minutes=12))
    await db.commit()
    assert (await remote_scan.scan_status(db))["requested_at"] is not None

    # Doppelte Lieferung wird abgelehnt.
    with pytest.raises(remote_scan.JobRejectedError):
        await remote_scan.accept_results(db, data, now=t0 + timedelta(minutes=13))

    # Geliefert nach 55 Min.: die Bewertung darf noch 45 Min. laufen (Review #33, a).
    assert remote_scan._job_active({"issued_at": t0.isoformat(),
                                    "delivered_at": (t0 + timedelta(minutes=55)).isoformat()},
                                   t0 + timedelta(minutes=70))

    # Erst nach der Bewertung (finish_job) gibt es den naechsten Auftrag.
    assert (await remote_scan.runner_job(db, now=t0 + timedelta(minutes=20)))["run"] is False
    await remote_scan.finish_job(db, job["job_id"])
    nxt = await remote_scan.runner_job(db, now=t0 + timedelta(minutes=21))
    assert nxt["run"] is True and nxt["job_id"] != job["job_id"]


async def test_results_without_matching_job_or_version_are_rejected(db, monkeypatch):
    monkeypatch.setattr(remote_scan, "get_settings_map", AsyncMock(return_value={
        "catawiki_scan_urls": "https://www.catawiki.com/de/a/1\n", "catawiki_scan_frequency": "off",
    }))
    t0 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    with pytest.raises(remote_scan.JobRejectedError):
        # Ohne ausgegebenen Auftrag nimmt Prod nichts an (Missbrauch mit gueltigem Token).
        await remote_scan.accept_results(db, remote_scan.RemoteScanResults(**_payload()), now=t0)
    await remote_scan.request_scan(db, now=t0)
    job = await remote_scan.runner_job(db, now=t0)
    with pytest.raises(remote_scan.JobRejectedError, match="Parser"):
        await remote_scan.accept_results(
            db, remote_scan.RemoteScanResults(**_payload(job_id=job["job_id"], parser_version="alt")), now=t0,
        )
    # Abgelaufene Sperre (Heimrechner nie zurueckgekommen): Ergebnis zu spaet, neuer Auftrag moeglich.
    later = t0 + remote_scan.JOB_LEASE + timedelta(minutes=1)
    with pytest.raises(remote_scan.JobRejectedError):
        await remote_scan.accept_results(db, remote_scan.RemoteScanResults(**_payload(job_id=job["job_id"])), now=later)
    assert (await remote_scan.runner_job(db, now=later))["run"] is True


async def test_request_without_token_is_refused(db):
    with pytest.raises(HTTPException) as exc:
        await remote_scan_routes.post_scan_request(session=db)
    assert exc.value.status_code == 409


async def test_internal_state_is_hidden_from_the_settings_page(db):
    from app.api.routes import settings as settings_routes

    await remote_scan.request_scan(db)
    listed = await settings_routes.list_settings(session=db)
    assert all(item.key != remote_scan.REQUESTED_KEY for item in listed)
    assert any(item.key == "remote_scan_token" for item in listed)



async def test_results_stay_open_when_the_broker_is_down(db, monkeypatch):
    # Review #33, b: ohne eingereihten Task bleibt der Auftrag offen, Antwort 503.
    monkeypatch.setattr(remote_scan, "get_settings_map", AsyncMock(return_value={
        "catawiki_scan_urls": "https://www.catawiki.com/de/a/1\n", "catawiki_scan_frequency": "off",
    }))

    class _Broken:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("broker down")

    rollback = AsyncMock()
    db.rollback = rollback
    monkeypatch.setattr(remote_scan_routes, "celery_app", _Broken())
    await remote_scan.request_scan(db)
    job = await remote_scan.runner_job(db)
    with pytest.raises(HTTPException) as exc:
        await remote_scan_routes.post_runner_results(
            remote_scan.RemoteScanResults(**_payload(job_id=job["job_id"])), session=db,
        )
    assert exc.value.status_code == 503
    rollback.assert_awaited_once()
