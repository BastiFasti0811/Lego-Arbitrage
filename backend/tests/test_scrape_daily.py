from types import SimpleNamespace

import httpx
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

    assert results == {"set_number": "10300", "prices": 0, "offers": 0, "errors": [], "blocked": False}
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
async def test_upsert_skips_offers_without_url(monkeypatch):
    # Review-Finding F7: Offers ohne Link unterlaufen den (platform, url)-
    # Upsert-Key und würden jede Runde neu inseriert — für immer.
    class _NoRowsResult:
        def scalars(self):
            return _FakeScalars([])

    class _RecordingSession:
        def __init__(self):
            self.added = []

        async def execute(self, _query):
            return _NoRowsResult()

        def add(self, obj):
            self.added.append(obj)

    from datetime import UTC, datetime

    session = _RecordingSession()
    offers = [SimpleNamespace(offer_url="", platform="EBAY")]
    count = await scrape_daily._upsert_offers(session, SimpleNamespace(id=1), offers, datetime.now(UTC))

    assert count == 0
    assert session.added == []


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


@pytest.mark.asyncio
async def test_upsert_rejects_accessory_listings():
    # Zubehoer mit Setnummer im Titel darf gar nicht erst in offers landen —
    # sonst bewertet analyze_new es gegen den Setpreis (869 % Phantom-ROI).
    class _NoRowsResult:
        def scalars(self):
            return _FakeScalars([])

    class _RecordingSession:
        def __init__(self):
            self.added = []

        async def execute(self, _query):
            return _NoRowsResult()

        def add(self, obj):
            self.added.append(obj)

    from datetime import UTC, datetime

    session = _RecordingSession()
    lego_set = SimpleNamespace(id=1, set_number="42143", current_market_price=400.0, uvp_eur=449.99)
    offers = [
        SimpleNamespace(
            offer_url="https://amazon.de/dp/1",
            platform="AMAZON",
            offer_title="Wandhalterung Haken für Lego Ferrari Daytona SP3 42143",
            price_eur=9.99,
            shipping_eur=None,
            condition="UNKNOWN",
            box_damage=False,
            sealed=False,
            seller_name=None,
            seller_rating=None,
            seller_location=None,
            is_auction=False,
            auction_end=None,
        ),
        SimpleNamespace(
            offer_url="https://kleinanzeigen.de/s-anzeige/2",
            platform="KLEINANZEIGEN",
            offer_title="LEGO Technic 42143 Ferrari Daytona SP3 - neu und original verpackt",
            price_eur=320.0,
            shipping_eur=None,
            condition="NEW_SEALED",
            box_damage=False,
            sealed=True,
            seller_name=None,
            seller_rating=None,
            seller_location=None,
            is_auction=False,
            auction_end=None,
        ),
    ]

    count = await scrape_daily._upsert_offers(session, lego_set, offers, datetime.now(UTC))

    assert count == 1
    assert len(session.added) == 1
    assert "Wandhalterung" not in session.added[0].offer_title


# ── Rate-Limit muss den Lauf anhalten, nicht nur eine Zeile fuellen ──


def _blocked_error(status):
    request = httpx.Request("GET", "https://www.kleinanzeigen.de/s-lego/k0")
    return httpx.HTTPStatusError(
        "blocked", request=request, response=httpx.Response(status, request=request)
    )


class _AllRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _WatchlistSession:
    def __init__(self, set_numbers):
        self._rows = [(n,) for n in set_numbers]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _query):
        return _AllRowsResult(self._rows)


@pytest.mark.asyncio
async def test_a_blocked_offer_scraper_marks_the_run(monkeypatch):
    # Vorher schluckte das generische "except Exception" den 403, und der
    # Watchlist-Lauf haemmerte ueber alle restlichen Sets weiter.
    class _BlockedScraper:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get_offers(self, _set_number):
            raise _blocked_error(429)

    session = _FakeSession(SimpleNamespace(id=1, set_number="10300", uvp_eur=None))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [_BlockedScraper])

    results = await scrape_daily._scrape_set_prices_async("10300")

    assert results["blocked"] is True
    assert results["errors"] == ["_BlockedScraper offers: HTTP 429"]


@pytest.mark.asyncio
async def test_the_watchlist_run_stops_at_the_blocked_set(monkeypatch):
    session = _WatchlistSession(["10300", "10331", "42143"])
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)

    visited = []

    async def _fake_scrape(set_number):
        visited.append(set_number)
        return {"errors": [], "blocked": set_number == "10331"}

    monkeypatch.setattr(scrape_daily, "_scrape_set_prices_async", _fake_scrape)

    summary = await scrape_daily._scrape_all_watched_async()

    assert visited == ["10300", "10331"], "42143 haette den blockenden Host weiter belastet"
    assert summary["aborted"] is True


@pytest.mark.asyncio
async def test_a_blocked_price_scraper_marks_the_run(monkeypatch):
    # Derselbe Fehler wie in der Offer-Schleife, eine Schleife weiter oben:
    # nur dort gab es einen HTTPStatusError-Zweig.
    class _BlockedPriceScraper:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get_price(self, _set_number):
            raise _blocked_error(403)

    session = _FakeSession(SimpleNamespace(id=1, set_number="10300", uvp_eur=None))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [_BlockedPriceScraper])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    results = await scrape_daily._scrape_set_prices_async("10300")

    assert results["blocked"] is True


@pytest.mark.asyncio
async def test_a_blocked_metadata_scraper_marks_the_run(monkeypatch):
    class _BlockedMetadataScraper:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get_set_info(self, _set_number):
            raise _blocked_error(429)

    session = _FakeSession(SimpleNamespace(id=1, set_number="10300", uvp_eur=None))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [_BlockedMetadataScraper])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    results = await scrape_daily._scrape_set_prices_async("10300")

    assert results["blocked"] is True
