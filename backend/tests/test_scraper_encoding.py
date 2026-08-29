"""Der gemeinsame Client darf keine Kompression anfordern, die er nicht entpacken kann.

BrickEconomy antwortete fuenf Monate lang mit HTTP 200 und einem Koerper, den
niemand lesen konnte: der Client verlangte brotli, das Image hatte keinen
Decoder, httpx reichte die Rohbytes durch. `_search_set` fand null Treffer und
`get_price` stieg ohne Logzeile aus.
"""

import httpx
import pytest
import structlog

from app.scrapers.base import UndecodableResponseError, looks_undecoded
from app.scrapers.brickeconomy import BrickEconomyScraper


@pytest.fixture
def _caplog_sees_structlog():
    """Ohne diese Bruecke sieht `caplog` keine Zeile, obwohl sie geschrieben wird.

    `caplog` haengt an der stdlib-`logging`-Root; die App-Logger laufen aber
    ueberall (bestaetigt per `structlog.get_config()` und Grep nach
    `structlog.configure` im ganzen `app`-Baum) auf structlogs Standard —
    ein PrintLogger, der direkt auf stdout schreibt und nie durch stdlib
    `logging` geht. Nur fuer den einen Test, der `caplog` braucht, umleiten
    und danach wieder auf die App-Defaults zurueckstellen, damit kein anderer
    Testlauf im selben Prozess die umgeleitete Konfiguration mitbekommt.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


def test_real_html_is_not_flagged_as_undecoded():
    html = "<!doctype html><html><body><h1>LEGO 75414</h1><p>29,74 €</p></body></html>"
    assert looks_undecoded(html) is False


def test_empty_body_is_not_flagged():
    # Eine leere Antwort ist ein anderes Problem und hat ihre eigene Behandlung.
    assert looks_undecoded("") is False


def test_undecoded_compression_is_flagged():
    # So sah die BrickEconomy-Antwort in Produktion aus: Rohbytes, als Zeichen gelesen.
    garbage = bytes(range(1, 200)).decode("latin-1") * 20
    assert looks_undecoded(garbage) is True


def test_client_does_not_claim_encodings_it_cannot_decode():
    # httpx setzt Accept-Encoding selbst — passend zu den installierten Decodern.
    # Ein selbstgesetzter Header ist eine Behauptung, die niemand prueft.
    scraper = BrickEconomyScraper()
    assert "accept-encoding" not in {k.lower() for k in scraper._base_headers()}


@pytest.mark.asyncio
async def test_fetch_rejects_an_undecodable_body(monkeypatch):
    garbage = bytes(range(1, 200)).decode("latin-1") * 20

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, text=garbage, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with BrickEconomyScraper() as scraper:
        with pytest.raises(UndecodableResponseError):
            await scraper._fetch("https://www.brickeconomy.com/search?query=75414")


@pytest.mark.asyncio
async def test_fetch_passes_real_html_through(monkeypatch):
    html = "<!doctype html><html><body>LEGO 75414</body></html>"

    async def fake_get(self, url, **kwargs):
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with BrickEconomyScraper() as scraper:
        body = await scraper._fetch("https://www.brickeconomy.com/search?query=75414")

    assert body == html


@pytest.mark.asyncio
async def test_brickeconomy_says_when_the_search_finds_nothing(monkeypatch, caplog, _caplog_sees_structlog):
    # Die einzige stumme Rueckgabe im ganzen Preis-Pfad: ohne Treffer stieg
    # get_price aus, ohne eine Zeile zu hinterlassen.
    async def no_hit(self, set_number):
        return None

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", no_hit)

    async with BrickEconomyScraper() as scraper:
        with caplog.at_level("WARNING"):
            price = await scraper.get_price("75414")

    assert price is None
    assert "brickeconomy.set_not_found" in caplog.text
