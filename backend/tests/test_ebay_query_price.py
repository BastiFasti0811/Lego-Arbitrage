"""Tests for EbaySoldScraper.get_price_for_query — freie Suchbegriffe (PR 2, Task 4)."""

from pathlib import Path

import pytest

from app.scrapers.ebay_sold import EbaySoldScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sold_card(title: str, price: str) -> str:
    return (
        '<li class="s-card">'
        f'<div class="s-card__title">{title}</div>'
        f'<div class="s-card__price">{price}</div>'
        '<a href="https://www.ebay.de/itm/123456">link</a>'
        "</li>"
    )


# Minimales li.s-card-Layout mit 5 echten Preisen — die vorhandene
# Fixture (ebay_active_75192.html) traegt 6 Treffer und ist fuer den
# Sold-Pfad ohnehin die falsche Suchart (aktive BIN-Listungen statt
# Verkauft-Angebote), darum hier bewusst eine eigene Sold-Fixture.
SOLD_HTML = (
    "<html><body><ul class='srp-results'>"
    + _sold_card("Bosch PSB 500 RE Schlagbohrmaschine", "EUR 45,00")
    + _sold_card("Bosch PSB 500 RE Bohrmaschine gebraucht", "EUR 48,50")
    + _sold_card("Bosch PSB 500 RE 2 Schlagbohrmaschine defekt", "EUR 42,00")
    + _sold_card("Bosch PSB 500 RE Werkzeugkoffer komplett", "EUR 50,00")
    + _sold_card("Bosch PSB 500 RE Set mit Zubehoer", "EUR 47,00")
    + "</ul></body></html>"
)

EMPTY_HTML = "<html><body><ul class='srp-results'></ul></body></html>"


@pytest.mark.asyncio
async def test_query_price_uses_sold_median_when_five_or_more_results(monkeypatch):
    async def fake_fetch(url):
        return SOLD_HTML

    scraper = EbaySoldScraper()
    monkeypatch.setattr(scraper, "_fetch", fake_fetch)

    price = await scraper.get_price_for_query("Bosch PSB 500")

    assert price is not None
    assert price.source == "EBAY_SOLD"
    assert price.sold_count == 5
    assert price.is_reliable is True
    assert price.median_price == 47.0
    assert price.min_price == 42.0
    assert price.max_price == 50.0


@pytest.mark.asyncio
async def test_query_price_falls_back_to_active_when_sold_search_is_empty(monkeypatch):
    # Sold-Suche liefert nichts (0 Treffer, keine Bot-Wall) — Fallback auf die
    # aktiven BIN-Listungen der bestehenden Karten-Fixture.
    active = _load("ebay_active_75192.html")

    async def fake_fetch(url):
        return active if "LH_BIN=1" in url else EMPTY_HTML

    scraper = EbaySoldScraper()
    monkeypatch.setattr(scraper, "_fetch", fake_fetch)

    price = await scraper.get_price_for_query("LEGO 75192")

    assert price is not None
    assert price.source == "EBAY_ACTIVE"
    assert price.is_reliable is False
    assert price.sold_count is not None and price.sold_count >= 3


@pytest.mark.asyncio
async def test_query_price_returns_none_when_both_searches_are_empty(monkeypatch):
    async def fake_fetch(url):
        return EMPTY_HTML

    scraper = EbaySoldScraper()
    monkeypatch.setattr(scraper, "_fetch", fake_fetch)

    price = await scraper.get_price_for_query("Nonexistent Query Xyzzy")

    assert price is None


def test_query_sold_url_has_no_lego_prefix_or_condition_filter():
    scraper = EbaySoldScraper()
    url = scraper._build_query_sold_url("Bosch PSB 500")

    assert "_nkw=Bosch+PSB+500" in url
    assert "LEGO" not in url
    assert "LH_ItemCondition" not in url
    assert "LH_Complete=1" in url
    assert "LH_Sold=1" in url


def test_query_active_url_has_no_lego_prefix_or_condition_filter():
    scraper = EbaySoldScraper()
    url = scraper._build_query_active_url("Bosch PSB 500")

    assert "_nkw=Bosch+PSB+500" in url
    assert "LEGO" not in url
    assert "LH_ItemCondition" not in url
    assert "LH_BIN=1" in url
