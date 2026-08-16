from types import SimpleNamespace

import pytest

from app.tasks import scrape_daily


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, lego_set):
        self._lego_set = lego_set
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _query):
        return _FakeResult(self._lego_set)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_scrape_set_prices_survives_utc_timestamp_setup(monkeypatch):
    # Guards scrape_daily's `datetime.now(UTC)` setup line: with the class-level
    # `datetime.UTC` bug this raised AttributeError before any scraper ran.
    session = _FakeSession(SimpleNamespace(id=1))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    results = await scrape_daily._scrape_set_prices_async("10300")

    assert results == {"set_number": "10300", "prices": 0, "offers": 0, "errors": []}
    assert session.committed is True
