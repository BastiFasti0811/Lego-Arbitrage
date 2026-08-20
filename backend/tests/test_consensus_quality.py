from app.engine.market_consensus import calculate_consensus
from app.scrapers.base import ScrapedPrice


def _price(source, value):
    return ScrapedPrice(source=source, price_eur=value)


def test_median_of_two_sources_is_the_midpoint_not_the_higher():
    # Review-Finding: sorted(prices)[len//2] ist bei zwei Werten der hoehere —
    # ein systematischer Aufwaertsdrift, und mit nur noch drei Preisquellen
    # ist "zwei Quellen" der Regelfall.
    result = calculate_consensus([_price("EBAY_SOLD", 400.0), _price("BRICKMERGE", 420.0)])
    assert result.consensus_price == 410.0


def test_ebay_active_fallback_counts_as_a_source():
    # Bei blockierter Sold-Suche liefert der Scraper EBAY_ACTIVE. Ohne Gewicht
    # verwarf der Konsens diese Quelle lautlos.
    result = calculate_consensus([_price("EBAY_ACTIVE", 380.0), _price("BRICKECONOMY", 420.0)])
    assert result.num_sources == 2
    assert "EBAY_ACTIVE" in result.source_prices
    assert result.consensus_price > 0


def test_single_source_consensus_is_flagged_unreliable():
    result = calculate_consensus([_price("BRICKECONOMY", 420.0)])
    assert result.num_sources == 1
    assert result.is_reliable is False


import pytest  # noqa: E402

from app.tasks import scrape_daily  # noqa: E402


class _Scraper:
    def __init__(self, price):
        self._price = price

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_price(self, _set_number):
        return self._price

    async def get_set_info(self, _set_number):
        return None

    async def get_offers(self, _set_number):
        return []


@pytest.mark.asyncio
async def test_unreliable_consensus_is_not_persisted_as_market_price(monkeypatch):
    # Eine einzige Quelle liefert laut calculate_consensus ausdruecklich
    # is_reliable=False. Wird sie trotzdem als current_market_price gespeichert,
    # geht die Unsicherheit verloren und ROI/Bestandsbewertung rechnen damit
    # wie mit einem gesicherten Marktpreis.
    from types import SimpleNamespace

    lego_set = SimpleNamespace(id=1, set_number="42143", uvp_eur=449.99, current_market_price=None,
                               market_price_updated_at=None)

    class _Session:
        committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, _q):
            return SimpleNamespace(scalar_one_or_none=lambda: lego_set)

        def add(self, _obj):
            pass

        async def commit(self):
            type(self).committed = True

    monkeypatch.setattr(scrape_daily, "async_session", lambda: _Session())
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [lambda: _Scraper(_price("BRICKECONOMY", 420.0))])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    await scrape_daily._scrape_set_prices_async("42143")

    assert lego_set.current_market_price is None


def test_unreliable_source_is_included_but_flags_the_consensus():
    # Review-Finding M1: EBAY_ACTIVE traegt is_reliable=False und wurde
    # deshalb vor der Gewichtung aussortiert — das Gewicht war wirkungslos.
    active = ScrapedPrice(source="EBAY_ACTIVE", price_eur=380.0, is_reliable=False)
    result = calculate_consensus([active, _price("BRICKECONOMY", 420.0)])
    assert "EBAY_ACTIVE" in result.source_prices
    assert result.num_sources == 2
    assert result.is_reliable is False
