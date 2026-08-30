"""Der Knopf darf keinen zweiten Lauf starten — und ein toter Lauf darf ihn
nicht fuer immer sperren.

Ein Lauf dauert gemessen elf Minuten. Zwei parallele Laeufe wuerden dieselben
Quellen doppelt befragen und den Block beschleunigen. Ein abgestuerzter Lauf
bleibt dagegen auf `running` stehen und wuerde die Bewertung dauerhaft
blockieren, wenn ihn nichts abloest.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import inventory
from app.api.routes.inventory import STALE_RUN_MINUTES, is_run_blocking
from app.models.valuation_run import ValuationRunStatus, ValuationTrigger
from app.tasks.celery_app import celery_app

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _running(minutes_ago: int):
    return SimpleNamespace(
        id=7, status="running", started_at=NOW - timedelta(minutes=minutes_ago)
    )


def test_no_run_does_not_block():
    assert is_run_blocking(None, NOW) is False


def test_a_fresh_running_run_blocks():
    assert is_run_blocking(_running(5), NOW) is True


def test_a_run_at_the_limit_still_blocks():
    assert is_run_blocking(_running(STALE_RUN_MINUTES - 1), NOW) is True


def test_a_stale_running_run_no_longer_blocks():
    # Sonst sperrt ein abgestuerzter Worker den Knopf fuer immer.
    assert is_run_blocking(_running(STALE_RUN_MINUTES + 1), NOW) is False


def test_a_finished_run_does_not_block():
    finished = SimpleNamespace(id=7, status="success", started_at=NOW - timedelta(minutes=2))
    assert is_run_blocking(finished, NOW) is False


# ---------------------------------------------------------------------------
# start_valuation_run: die drei Faelle, die den Knopf am Leben halten.
#
# Der Handler ist ein gewoehnliches `async def` mit `session` als Parameter —
# das Depends() im Signatur-Default wird nur ausgewertet, wenn FastAPI selbst
# die Route aufloest. Reicht man die Session direkt hinein, braucht es weder
# TestClient noch echte Datenbank, genau wie _FakeRunSession in
# test_inventory_valuation_run.py den Task-Body ohne DB durchspielt.
#
# Der Absturz-Pfad (Fall 1) laeuft nur an, wenn ein Worker mitten im Lauf
# gestorben ist — genau dann schaut niemand hin, und ein falscher Ausgang
# kostet am meisten: eine Lauf-Zeile, die fuer immer auf "running" steht und
# im Protokoll als "laeuft noch" angezeigt wird.
# ---------------------------------------------------------------------------


def _running_now(minutes_ago: int):
    # start_valuation_run misst gegen die Wanduhr (datetime.now(UTC)), nicht
    # gegen die feste NOW-Konstante oben — die passt nur zu is_run_blocking
    # als reiner Funktion mit explizitem `now`-Parameter.
    return SimpleNamespace(
        id=7,
        status=ValuationRunStatus.RUNNING.value,
        started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )


class _FakeValuationResult:
    def __init__(self, run):
        self._run = run

    def scalar_one_or_none(self):
        return self._run


class _FakeValuationSession:
    """Reicht start_valuation_run genau eine vorbereitete `latest`-Zeile.

    add()/commit() zeichnen auf, was der Handler tut; refresh() vergibt die
    ID, die sonst die DB beim Insert zuteilen wuerde.
    """

    NEW_RUN_ID = 99

    def __init__(self, latest_run):
        self._latest_run = latest_run
        self.added: list = []
        self.commits = 0

    async def execute(self, _statement):
        return _FakeValuationResult(self._latest_run)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        obj.id = self.NEW_RUN_ID


class _FakeSendTask:
    """Ersetzt celery_app.send_task: zeichnet Aufrufe auf, reiht nichts ein."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, name, kwargs=None, queue=None):
        self.calls.append({"name": name, "kwargs": kwargs, "queue": queue})


@pytest.mark.asyncio
async def test_a_stale_running_run_is_promoted_before_a_new_one_starts(monkeypatch):
    stale = _running_now(STALE_RUN_MINUTES + 1)
    session = _FakeValuationSession(stale)
    send_task = _FakeSendTask()
    monkeypatch.setattr(celery_app, "send_task", send_task)

    result = await inventory.start_valuation_run(session=session)

    assert stale.status == ValuationRunStatus.FAILED.value
    assert stale.finished_at is not None
    assert stale.error  # erklaert, warum der alte Lauf als abgebrochen gilt
    assert session.commits == 1
    assert len(session.added) == 1
    new_run = session.added[0]
    assert new_run.status == ValuationRunStatus.RUNNING.value
    assert new_run.trigger == ValuationTrigger.MANUAL.value
    assert result == {"run_id": _FakeValuationSession.NEW_RUN_ID}
    assert send_task.calls == [
        {
            "name": "app.tasks.update_inventory.update_inventory_valuations",
            "kwargs": {"run_id": _FakeValuationSession.NEW_RUN_ID},
            "queue": "analysis",
        }
    ]


@pytest.mark.asyncio
async def test_a_fresh_running_run_blocks_and_enqueues_nothing(monkeypatch):
    fresh = _running_now(5)
    session = _FakeValuationSession(fresh)
    send_task = _FakeSendTask()
    monkeypatch.setattr(celery_app, "send_task", send_task)

    with pytest.raises(HTTPException) as exc_info:
        await inventory.start_valuation_run(session=session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["run_id"] == fresh.id
    # Nicht befoerdert: der Lauf laeuft ja wirklich noch.
    assert fresh.status == ValuationRunStatus.RUNNING.value
    assert session.added == []
    assert session.commits == 0
    # Die Ablehnung darf den Worker nicht trotzdem anstossen — sonst liefe
    # die Aktualisierung ein zweites Mal, obwohl der Aufruf mit 409 endete.
    assert send_task.calls == []


@pytest.mark.asyncio
async def test_a_finished_run_does_not_block_and_is_left_alone(monkeypatch):
    finished = SimpleNamespace(
        id=7,
        status=ValuationRunStatus.SUCCESS.value,
        started_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    before = vars(finished).copy()
    session = _FakeValuationSession(finished)
    send_task = _FakeSendTask()
    monkeypatch.setattr(celery_app, "send_task", send_task)

    result = await inventory.start_valuation_run(session=session)

    assert vars(finished) == before  # kein Attribut angefasst
    assert result == {"run_id": _FakeValuationSession.NEW_RUN_ID}
    assert send_task.calls  # der neue Lauf wird trotzdem angestossen
