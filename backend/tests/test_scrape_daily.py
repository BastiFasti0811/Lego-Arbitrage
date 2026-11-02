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

    assert results == {
        "set_number": "10300",
        "prices": 0,
        "offers": 0,
        "errors": [],
        "blocked": False,
        "blocked_scrapers": [],
    }
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

    async def _no_known_urls(_session, _lego_set, _platform):
        return set()

    monkeypatch.setattr(scrape_daily, "_known_offer_urls", _no_known_urls)

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
async def test_a_blocked_source_is_skipped_for_the_rest_of_the_run(monkeypatch):
    # Prod 01.11.2026: eBay antwortet 403, der Lauf brach nach dem ersten Set ab
    # ({'total_sets': 31, 'success': 0, 'errors': 1, 'aborted': True}).
    # Jetzt bekommt eBay keinen Abruf mehr, die anderen Quellen laufen weiter.
    session = _WatchlistSession(["75388", "76934", "75300"])
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)

    calls = []

    async def _fake_scrape(set_number, skip_scrapers=frozenset()):
        calls.append((set_number, skip_scrapers))
        if set_number == "75388":
            return {"errors": ["EbaySoldScraper: HTTP 403"], "blocked": True, "blocked_scrapers": ["EbaySoldScraper"]}
        return {"errors": [], "blocked": False, "blocked_scrapers": []}

    monkeypatch.setattr(scrape_daily, "_scrape_set_prices_async", _fake_scrape)

    summary = await scrape_daily._scrape_all_watched_async()

    assert calls == [
        ("75388", frozenset()),
        ("76934", frozenset({"EbaySoldScraper"})),
        ("75300", frozenset({"EbaySoldScraper"})),
    ]
    assert summary == {
        "total_sets": 3,
        "success": 2,
        "errors": 1,
        "aborted": False,
        "blocked_sources": ["EbaySoldScraper"],
    }


@pytest.mark.asyncio
async def test_the_run_stops_once_every_source_is_blocked(monkeypatch):
    class _Price:
        pass

    class _Offer:
        pass

    session = _WatchlistSession(["10300", "10331", "42143"])
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [_Price])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [_Price])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [_Offer])

    visited = []

    async def _fake_scrape(set_number, skip_scrapers=frozenset()):
        visited.append(set_number)
        newly = {"10300": ["_Price"], "10331": ["_Offer"]}.get(set_number, [])
        return {"errors": [f"{n}: HTTP 403" for n in newly], "blocked": bool(newly), "blocked_scrapers": newly}

    monkeypatch.setattr(scrape_daily, "_scrape_set_prices_async", _fake_scrape)

    summary = await scrape_daily._scrape_all_watched_async()

    assert visited == ["10300", "10331"], "42143 haette nur noch gesperrte Quellen gefragt"
    assert summary["aborted"] is True
    assert summary["blocked_sources"] == ["_Price", "_Offer"]


@pytest.mark.asyncio
async def test_a_block_on_the_last_set_is_not_an_abort(monkeypatch):
    session = _WatchlistSession(["10300"])
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    async def _fake_scrape(set_number, skip_scrapers=frozenset()):
        return {"errors": ["X: HTTP 429"], "blocked": True, "blocked_scrapers": ["X"]}

    monkeypatch.setattr(scrape_daily, "_scrape_set_prices_async", _fake_scrape)

    summary = await scrape_daily._scrape_all_watched_async()

    assert summary["aborted"] is False
    assert summary["blocked_sources"] == ["X"]


class _CountingScraper:
    """Zaehlt Abrufe je Klasse; kann fuer get_price einen Status werfen."""

    calls: list = []
    price_status: int | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_price(self, _set_number):
        type(self).calls.append((type(self).__name__, "price"))
        if type(self).price_status:
            raise _blocked_error(type(self).price_status)
        return None

    async def get_set_info(self, _set_number):
        type(self).calls.append((type(self).__name__, "info"))
        return None

    async def get_offers(self, _set_number):
        type(self).calls.append((type(self).__name__, "offers"))
        return []


@pytest.mark.asyncio
async def test_a_source_blocked_on_prices_gets_no_offer_request_for_the_same_set(monkeypatch):
    # EbaySoldScraper steht in PRICE_SCRAPERS und OFFER_SCRAPERS: nach dem 403
    # im Preisabruf darf die Angebotssuche denselben Host nicht noch einmal fragen.
    class _Ebay(_CountingScraper):
        price_status = 403

    class _Amazon(_CountingScraper):
        pass

    _CountingScraper.calls = []
    session = _FakeSession(SimpleNamespace(id=1, set_number="75388", uvp_eur=None))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [_Ebay])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [_Ebay, _Amazon])

    async def _fake_upsert(_session, _lego_set, offers, _now):
        return len(offers)

    monkeypatch.setattr(scrape_daily, "_upsert_offers", _fake_upsert)

    results = await scrape_daily._scrape_set_prices_async("75388")

    assert _CountingScraper.calls == [("_Ebay", "price"), ("_Amazon", "offers")]
    assert results["blocked_scrapers"] == ["_Ebay"]
    assert results["errors"] == ["_Ebay: HTTP 403"]


@pytest.mark.asyncio
async def test_skipped_sources_get_no_request_at_all(monkeypatch):
    class _Ebay(_CountingScraper):
        pass

    class _BrickMerge(_CountingScraper):
        pass

    _CountingScraper.calls = []
    session = _FakeSession(SimpleNamespace(id=1, set_number="76934", uvp_eur=None))
    monkeypatch.setattr(scrape_daily, "async_session", lambda: session)
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [_Ebay, _BrickMerge])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [_BrickMerge])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [_Ebay, _BrickMerge])

    async def _fake_upsert(_session, _lego_set, offers, _now):
        return len(offers)

    monkeypatch.setattr(scrape_daily, "_upsert_offers", _fake_upsert)

    results = await scrape_daily._scrape_set_prices_async("76934", skip_scrapers=frozenset({"_Ebay"}))

    assert [name for name, _ in _CountingScraper.calls] == ["_BrickMerge"] * 3
    assert results["errors"] == [] and results["blocked"] is False


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
