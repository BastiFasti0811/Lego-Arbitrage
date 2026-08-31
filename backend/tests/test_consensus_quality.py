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


def test_ebay_active_is_not_a_consensus_source():
    # eBays Verkaufspreis-Suche antwortet 403; was einspringt, ist der Median
    # offener Angebote — Forderungen, keine Verkaeufe. Als zweites Standbein
    # eines Konsenses, der die Geldzahlen des Bestands schreibt, taugt das nicht.
    #
    # Frueher zaehlte die Quelle mit (Finding M1), und das war damals richtig:
    # BrickEconomy lieferte seit Projektbeginn nichts, eBay-aktiv war die einzige
    # verfuegbare zweite Quelle, und ohne sie waere `current_market_price`
    # dauerhaft leer geblieben. Seit BrickEconomy wieder antwortet, faellt diese
    # Begruendung weg — gemessen an einem echten Lauf kostet der Ausbau ein Set.
    result = calculate_consensus([_price("EBAY_ACTIVE", 380.0), _price("BRICKECONOMY", 420.0)])
    assert "EBAY_ACTIVE" not in result.source_prices
    assert result.num_sources == 1
    assert result.is_reliable is False


def test_excluding_a_source_is_not_the_same_as_weighting_it_zero():
    # Die Falle beim Ausbauen: eine Quelle mit Gewicht 0.0 steht weiterhin in
    # `_source_weights()`, landet damit in `raw_prices` und zaehlt sowohl fuer
    # `num_sources` als auch fuer den Median. Das Gewicht wirkt erst im
    # gewichteten Mittel. Nur wer ganz aus der Gewichtstabelle faellt, ist
    # wirklich draussen — deshalb reicht ZERO_WEIGHT_SOURCES hier nicht.
    zero_weighted = calculate_consensus([_price("AMAZON", 380.0), _price("BRICKECONOMY", 420.0)])
    assert zero_weighted.num_sources == 2

    excluded = calculate_consensus([_price("EBAY_ACTIVE", 380.0), _price("BRICKECONOMY", 420.0)])
    assert excluded.num_sources == 1


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
    # Review-Finding M1: eine Quelle mit is_reliable=False wurde vor der
    # Gewichtung aussortiert — ihr Gewicht war damit wirkungslos. Unsicherheit
    # ist ein Qualitaetsmerkmal, kein Ausschlusskriterium; sie markiert den
    # Konsens, statt die Quelle verschwinden zu lassen.
    #
    # Das Beispiel war frueher EBAY_ACTIVE. Diese Quelle ist inzwischen ganz aus
    # dem Konsens genommen, der hier gepruefte Mechanismus gilt unveraendert.
    soft = ScrapedPrice(source="EBAY_SOLD", price_eur=380.0, is_reliable=False)
    result = calculate_consensus([soft, _price("BRICKECONOMY", 420.0)])
    assert "EBAY_SOLD" in result.source_prices
    assert result.num_sources == 2
    assert result.is_reliable is False


@pytest.mark.asyncio
async def test_two_source_consensus_is_persisted_even_if_one_source_is_soft(monkeypatch):
    # Produktionsbefund: mit einer sicheren und einer unsicheren Quelle wurde NIE
    # ein Marktpreis geschrieben — die Sperre auf is_reliable war zu grob und
    # haette current_market_price dauerhaft leer gelassen.
    #
    # Das Paar war frueher BRICKMERGE + EBAY_ACTIVE. Letzteres zaehlt seit dem
    # Ausbau nicht mehr im Konsens, also steht hier ein anderes unsicheres Paar;
    # geprueft wird weiterhin, dass Unsicherheit die Speicherung nicht blockiert.
    from types import SimpleNamespace

    lego_set = SimpleNamespace(id=1, set_number="42143", uvp_eur=None, current_market_price=None,
                               market_price_updated_at=None)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, _q):
            return SimpleNamespace(scalar_one_or_none=lambda: lego_set)

        def add(self, _obj):
            pass

        async def commit(self):
            pass

    reliable = ScrapedPrice(source="BRICKMERGE", price_eur=376.99)
    soft = ScrapedPrice(source="EBAY_SOLD", price_eur=390.0, is_reliable=False)

    monkeypatch.setattr(scrape_daily, "async_session", lambda: _Session())
    monkeypatch.setattr(scrape_daily, "PRICE_SCRAPERS", [lambda: _Scraper(reliable), lambda: _Scraper(soft)])
    monkeypatch.setattr(scrape_daily, "METADATA_SCRAPERS", [])
    monkeypatch.setattr(scrape_daily, "OFFER_SCRAPERS", [])

    await scrape_daily._scrape_set_prices_async("42143")

    assert lego_set.current_market_price is not None
