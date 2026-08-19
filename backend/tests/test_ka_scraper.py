import httpx
import pytest

from app.scrapers.kleinanzeigen import KleinanzeigenScraper, _parse_ka_price


def test_ka_price_parses_german_formats():
    # Review-Finding F5: '129,99 €' wurde zu 12999.0 — jedes Cent-Angebot
    # landete 100-fach überteuert in offers und wurde als NO_GO verworfen.
    assert _parse_ka_price("850 €") == 850.0
    assert _parse_ka_price("1.200 € VB") == 1200.0
    assert _parse_ka_price("129,99 €") == 129.99
    assert _parse_ka_price("1.234,56 €") == 1234.56
    assert _parse_ka_price("1499,99 €") == 1499.99
    assert _parse_ka_price("VB") is None


@pytest.mark.asyncio
async def test_ka_get_offers_propagates_block_status(monkeypatch):
    # Review-Finding F2: get_offers schluckte jede Exception → der 403/429-
    # Abort der 2h-Lane war toter Code und die Lane hämmerte weiter.
    async def fake_fetch(self, url):
        raise httpx.HTTPStatusError(
            "429",
            request=httpx.Request("GET", url),
            response=httpx.Response(429, request=httpx.Request("GET", url)),
        )

    monkeypatch.setattr(KleinanzeigenScraper, "_fetch", fake_fetch)

    async with KleinanzeigenScraper() as scraper:
        with pytest.raises(httpx.HTTPStatusError):
            await scraper.get_offers("75192")
