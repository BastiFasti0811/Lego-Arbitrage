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


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeListResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeWatchlistSession:
    def __init__(self, sets):
        self._sets = sets
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _query):
        return _FakeListResult(self._sets)

    async def commit(self):
        self.committed = True


class _FakeKleinanzeigenScraper:
    responses: dict = {}
    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_offers(self, set_number):
        type(self).calls.append(set_number)
        value = type(self).responses[set_number]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_kleinanzeigen_lane_upserts_offers_per_watched_set(monkeypatch):
    import httpx  # noqa: F401 — ensures parity with abort test imports

    sets = [SimpleNamespace(id=1, set_number="75300"), SimpleNamespace(id=2, set_number="75322")]
    session = _FakeWatchlistSession(sets)
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)

    _FakeKleinanzeigenScraper.responses = {"75300": [object()], "75322": [object(), object()]}
    _FakeKleinanzeigenScraper.calls = []
    monkeypatch.setattr(scrape_daily, "KleinanzeigenScraper", _FakeKleinanzeigenScraper)

    upserted = []

    async def _fake_upsert(_session, lego_set, offers, _now):
        upserted.append((lego_set.set_number, len(offers)))
        return len(offers)

    monkeypatch.setattr(scrape_daily, "_upsert_offers", _fake_upsert)

    result = await scrape_daily._scrape_kleinanzeigen_async()

    assert upserted == [("75300", 1), ("75322", 2)]
    assert result["offers"] == 3
    assert result["aborted"] is False
    assert session.committed is True


@pytest.mark.asyncio
async def test_kleinanzeigen_lane_aborts_run_on_block_status(monkeypatch):
    import httpx

    sets = [SimpleNamespace(id=1, set_number="75300"), SimpleNamespace(id=2, set_number="75322")]
    session = _FakeWatchlistSession(sets)
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)

    block = httpx.HTTPStatusError(
        "blocked",
        request=httpx.Request("GET", "https://www.kleinanzeigen.de/s-LEGO+75300/k0"),
        response=httpx.Response(429, request=httpx.Request("GET", "https://www.kleinanzeigen.de")),
    )
    _FakeKleinanzeigenScraper.responses = {"75300": block, "75322": [object()]}
    _FakeKleinanzeigenScraper.calls = []
    monkeypatch.setattr(scrape_daily, "KleinanzeigenScraper", _FakeKleinanzeigenScraper)

    async def _fake_upsert(_session, lego_set, offers, _now):
        return len(offers)

    monkeypatch.setattr(scrape_daily, "_upsert_offers", _fake_upsert)

    result = await scrape_daily._scrape_kleinanzeigen_async()

    assert result["aborted"] is True
    assert _FakeKleinanzeigenScraper.calls == ["75300"]
    assert result["offers"] == 0
