import importlib

import pytest

from app.tasks import async_runner


class _FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1


async def _sample(value):
    return value


def test_run_async_returns_result_and_disposes_pool(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(async_runner, "engine", fake)

    result = async_runner.run_async(_sample("ok"))

    assert result == "ok"
    assert fake.dispose_calls == 1


def test_run_async_disposes_pool_when_coro_raises(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(async_runner, "engine", fake)

    async def _boom():
        raise RuntimeError("kaputt")

    with pytest.raises(RuntimeError, match="kaputt"):
        async_runner.run_async(_boom())

    assert fake.dispose_calls == 1


@pytest.mark.parametrize(
    "module_name",
    [
        "app.tasks.analyze_new",
        "app.tasks.auction_watch",
        "app.tasks.catawiki_scan",
        "app.tasks.health_check",
        "app.tasks.scrape_daily",
        "app.tasks.update_inventory",
    ],
)
def test_task_modules_share_pool_resetting_runner(module_name):
    # Every Celery task module must bridge through the shared runner so pooled
    # connections never leak across the per-task event loops.
    mod = importlib.import_module(module_name)
    assert mod._run_async is async_runner.run_async
