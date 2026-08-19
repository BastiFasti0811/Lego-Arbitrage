from pathlib import Path

import pytest

from app.scrapers.brickmerge import BrickMergeScraper, _parse_de_price
from app.scrapers.ebay_sold import EbaySoldScraper


def test_brickmerge_de_price_never_matches_mid_number():
    # Review-Finding F6: '1499,99 €' ohne Tausenderpunkt wurde als 499.99
    # geparst (Match mitten in der Zahl) und verfälschte den Konsens.
    assert _parse_de_price("701,36 €") == 701.36
    assert _parse_de_price("1.499,99 €") == 1499.99
    assert _parse_de_price("1499,99 €") == 1499.99
    assert _parse_de_price("kein Preis") is None

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_brickmerge_price_uses_detail_page_and_ignores_savings(monkeypatch):
    # Der ?sn=-Weg liefert in Produktion unentpackte Kompression (Mojibake) —
    # der Preis muss von der ?find=-Detailseite kommen und darf weder den
    # Ersparnis-Betrag (148,63) noch die UVP (849,99) als Bestpreis melden.
    garbage = "��\x02#\x11&�� kein preis hier"
    detail = _load("brickmerge_detail_75192.html")

    async def fake_fetch(self, url):
        return garbage

    async def fake_detail(self, set_number):
        return detail

    monkeypatch.setattr(BrickMergeScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(BrickMergeScraper, "_fetch_detail_page", fake_detail)

    async with BrickMergeScraper() as scraper:
        price = await scraper.get_price("75192")

    assert price is not None
    assert price.price_eur == 701.36
    assert price.source == "BRICKMERGE"


@pytest.mark.asyncio
async def test_ebay_offers_parse_current_s_card_layout(monkeypatch):
    active = _load("ebay_active_75192.html")

    async def fake_fetch(self, url):
        return active

    monkeypatch.setattr(EbaySoldScraper, "_fetch", fake_fetch)

    async with EbaySoldScraper() as scraper:
        offers = await scraper.get_offers("75192")

    assert len(offers) >= 3
    for offer in offers:
        assert offer.price_eur and offer.price_eur > 5.0
        assert offer.offer_title
        assert offer.offer_url.startswith("https://www.ebay.de/itm/")
        assert "?" not in offer.offer_url  # Tracking-Parameter raus → stabiler Upsert-Key
    conditions = {offer.condition for offer in offers}
    assert "USED_COMPLETE" in conditions
    assert "NEW_SEALED" in conditions
    assert all("Shop on eBay" not in offer.offer_title for offer in offers)


@pytest.mark.asyncio
async def test_ebay_price_falls_back_to_active_when_sold_is_challenged(monkeypatch):
    challenge = _load("ebay_challenge.html")
    active = _load("ebay_active_75192.html")

    async def fake_fetch(self, url):
        return active if "LH_BIN=1" in url else challenge

    monkeypatch.setattr(EbaySoldScraper, "_fetch", fake_fetch)

    async with EbaySoldScraper() as scraper:
        price = await scraper.get_price("75192")

    assert price is not None
    assert price.source == "EBAY_ACTIVE"
    assert price.is_reliable is False
    # Median der 6 echten Fixture-Listungen (450–650) — Platzhalter dürfen ihn nicht verzerren.
    assert 400.0 < price.price_eur < 600.0


@pytest.mark.asyncio
async def test_ebay_price_falls_back_when_sold_fetch_raises(monkeypatch):
    # Die Wall leitet teils auf signin.ebay.de um und antwortet 403 — der
    # Fallback muss auch greifen, wenn die Sold-Fetches per Exception sterben.
    import httpx

    active = _load("ebay_active_75192.html")

    async def fake_fetch(self, url):
        if "LH_BIN=1" in url:
            return active
        raise httpx.HTTPStatusError(
            "403",
            request=httpx.Request("GET", url),
            response=httpx.Response(403, request=httpx.Request("GET", url)),
        )

    monkeypatch.setattr(EbaySoldScraper, "_fetch", fake_fetch)

    async with EbaySoldScraper() as scraper:
        price = await scraper.get_price("75192")

    assert price is not None
    assert price.source == "EBAY_ACTIVE"
