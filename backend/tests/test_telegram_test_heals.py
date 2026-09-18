from types import SimpleNamespace

import pytest

from app.api.routes import settings as settings_route


class _Response:
    status_code = 200
    text = "ok"


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *_args, **_kwargs):
        return _Response()


class _Session:
    def __init__(self, failing_tasks):
        self._failing = failing_tasks

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self._failing)))


@pytest.mark.asyncio
async def test_successful_test_message_requeues_failed_reports(monkeypatch):
    # Review-Finding: nach dem Korrigieren der Zugangsdaten blieb der Status
    # bis zum naechsten Sonntagslauf rot, und der nun funktionierende Watchdog
    # schickte bis dahin rund 26-mal "Zugangsdaten pruefen".
    async def fake_settings(_keys):
        return {"telegram_bot_token": "123:abc", "telegram_chat_id": "42"}

    queued = []
    monkeypatch.setattr(settings_route, "get_settings_map", fake_settings)
    monkeypatch.setattr(settings_route.httpx, "AsyncClient", lambda: _Client())
    monkeypatch.setattr(settings_route.celery_app, "send_task", lambda name: queued.append(name))

    failing = ["app.tasks.weekly_report.send_weekly_report_task"]
    result = await settings_route.test_telegram(session=_Session(failing))

    assert result["success"] is True
    assert queued == failing
    assert result["requeued"] == failing


@pytest.mark.asyncio
async def test_successful_test_message_requeues_nothing_when_reports_are_fine(monkeypatch):
    async def fake_settings(_keys):
        return {"telegram_bot_token": "123:abc", "telegram_chat_id": "42"}

    queued = []
    monkeypatch.setattr(settings_route, "get_settings_map", fake_settings)
    monkeypatch.setattr(settings_route.httpx, "AsyncClient", lambda: _Client())
    monkeypatch.setattr(settings_route.celery_app, "send_task", lambda name: queued.append(name))

    result = await settings_route.test_telegram(session=_Session([]))

    assert queued == []
    assert result["requeued"] == []
