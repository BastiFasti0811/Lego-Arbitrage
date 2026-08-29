"""Der gemeinsame Client darf keine Kompression anfordern, die er nicht entpacken kann.

BrickEconomy antwortete fuenf Monate lang mit HTTP 200 und einem Koerper, den
niemand lesen konnte: der Client verlangte brotli, das Image hatte keinen
Decoder, httpx reichte die Rohbytes durch. `_search_set` fand null Treffer und
`get_price` stieg ohne Logzeile aus.
"""

import gzip
from datetime import UTC, datetime

import httpx
import pytest
import structlog

from app.scrapers import brickeconomy
from app.scrapers.base import UndecodableResponseError, looks_undecoded
from app.scrapers.brickeconomy import BrickEconomyScraper
from app.services.fx import FxRate


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


def test_gzip_compressed_html_decoded_as_latin1_is_flagged():
    # Der reale Fehlerpfad aus dem Review-Finding: ein Server deklariert eine
    # Single-Byte-Kodierung (z.B. ISO-8859-1), httpx decodiert den noch
    # gzip-komprimierten Koerper als latin-1. Der Steuerzeichenanteil allein
    # lag hier je nach Seitengroesse gemessen zwischen 7,4 % und 19,5 % —
    # mal drunter, mal drueber der alten 10-%-Grenze. Was nie schwankt: der
    # komprimierte Koerper enthaelt kein einziges HTML-Markertoken, das waere
    # ein Zufallstreffer mit Wahrscheinlichkeit < 1e-8.
    para = (
        "<div class='listing'><span class='price'>{p},99 &euro;</span> "
        "<a href='/set/{n}-1/LEGO-Star-Wars'>LEGO Star Wars Set {n}</a> "
        "Baujahr 20{y}, {pc} Teile, {mf} Minifiguren, Wachstum {g} Prozent.</div>\n"
    )
    rows = "".join(
        para.format(p=100 + i, n=75000 + i, y=15 + (i % 9), pc=200 + i * 7, mf=1 + i % 6, g=i % 40)
        for i in range(400)
    )
    html = (
        "<!doctype html><html><head><title>LEGO Suche - BrickEconomy</title>"
        "<meta charset='utf-8'><link rel='stylesheet' href='/site.css'></head>"
        "<body><header><nav>Home | Sets | Search</nav></header>"
        f"<main>{rows}</main>"
        "<footer>&copy; 2026 BrickEconomy</footer></body></html>"
    ).encode()

    garbage = gzip.compress(html).decode("latin-1")

    assert looks_undecoded(garbage) is True


def test_binary_looking_body_without_html_markers_is_flagged_even_at_zero_ratio():
    # Der Steuerzeichenanteil ist nicht die einzige Verteidigungslinie: ein
    # Koerper kann fast nur aus druckbaren Zeichen bestehen (Ratio 0 %) und
    # trotzdem keine Seite sein — z.B. ein anderes Kompressionsformat als
    # gzip/brotli (etwa ein kuenftiges zstd, das im Image ebenfalls keinen
    # Decoder hat) muss nicht Richtung Steuerzeichen verteilt sein. Fehlt
    # jedes HTML-Markertoken, wird trotzdem verworfen — unabhaengig vom
    # Anteil. Eine reine Anteils-Pruefung haette diesen Fall durchgelassen.
    base64_like = (
        "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5"
        "ejAxMjM0NTY3ODkrLw=="
    ) * 40
    assert looks_undecoded(base64_like) is True


def test_html_with_heavy_umlauts_is_not_flagged():
    # Akzentzeichen sind gewoehnliche Zeichen, keine Steuerzeichen — ein
    # Textanteil mit vielen Umlauten darf den Detektor nicht triggern.
    html = (
        "<!doctype html><html><body><h1>Fruehstueck in Zuerich</h1>"
        "<p>Ein Café bietet Croissants, Kaffee und ein Müsli mit Naïvität.</p>"
        "<p>Straße, Öffnungszeiten, überraschend günstig, groß, Fußgänger.</p>"
        "</body></html>"
    )
    assert looks_undecoded(html) is False


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


@pytest.mark.asyncio
async def test_get_price_constructs_a_real_scraped_price_on_a_hit(monkeypatch):
    # Regression: ScrapedPrice (app/scrapers/base.py) hat kein price_original-
    # Feld. get_price rief es trotzdem mit price_original=usd_price auf — das
    # warf TypeError bei jedem Treffer, das breite `except Exception` schluckte
    # ihn, und get_price gab lautlos None zurueck. Der Encoding-Bug oben hatte
    # das fuenf Monate verdeckt, weil die Ausfuehrung schon vorher abbrach; erst
    # mit dessen Fix war diese Zeile ueberhaupt erreichbar. Ein reiner
    # "is not None"-Check haette den Konstruktor-Fehler durchgelassen, solange
    # er zufaellig doch ein Objekt zurueckgibt — darum werden hier die Werte
    # selbst geprueft.
    html = (
        "<!doctype html><html><body>"
        "<h1>75414 Some Set</h1>\n"
        "<p>Value New: $120.50</p>\n"
        "<p>Growth: +12.3%</p>\n"
        "</body></html>"
    )

    async def fake_search(self, set_number):
        return "https://www.brickeconomy.com/set/75414-1/Some-Set"

    async def fake_fetch(self, url):
        return html

    async def fake_fx():
        return FxRate(usd_to_eur=0.9, as_of=None, is_fallback=True)

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", fake_search)
    monkeypatch.setattr(BrickEconomyScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(brickeconomy, "get_usd_to_eur", fake_fx)

    async with BrickEconomyScraper() as scraper:
        price = await scraper.get_price("75414")

    assert price is not None
    assert price.source == "BRICKECONOMY"
    assert price.currency == "USD"
    assert price.price_eur == pytest.approx(round(120.50 * 0.9, 2))
    # Der USD-Originalbetrag hat kein eigenes Feld mehr auf ScrapedPrice —
    # er muss trotzdem lesbar bleiben, statt beim Runden zu verschwinden.
    assert "USD 120.50" in price.notes
    assert "Growth: +12.3%" in price.notes
    assert "Ersatzkurs" in price.notes


@pytest.mark.asyncio
async def test_get_price_notes_hold_the_usd_amount_without_growth_or_fallback(monkeypatch):
    # Ohne Wachstumsangabe und mit einem gemessenen (nicht Ersatz-)Kurs bleibt
    # notes trotzdem gefuellt: der USD-Betrag wird jetzt unbedingt angehaengt,
    # nicht nur als Nebenprodukt eines Growth-Treffers.
    html = "<!doctype html><html><body><h1>75414 Some Set</h1>\n<p>Value New: $50.00</p></body></html>"

    async def fake_search(self, set_number):
        return "https://www.brickeconomy.com/set/75414-1/Some-Set"

    async def fake_fetch(self, url):
        return html

    as_of = datetime(2026, 8, 25, tzinfo=UTC)

    async def fake_fx():
        return FxRate(usd_to_eur=0.85, as_of=as_of, is_fallback=False)

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", fake_search)
    monkeypatch.setattr(BrickEconomyScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(brickeconomy, "get_usd_to_eur", fake_fx)

    async with BrickEconomyScraper() as scraper:
        price = await scraper.get_price("75414")

    assert price is not None
    assert price.notes == "USD 50.00"


@pytest.mark.asyncio
async def test_get_price_notes_carry_a_stale_rate_warning(monkeypatch):
    # Review-Finding (Critical): FxRate unterschied bisher nur zwei Zustaende
    # (Ersatzwert / alles andere). Ein echter, aber laenger als MAX_AGE alter
    # Cache-Kurs kam mit is_fallback=False und damit note=None zurueck — vom
    # Objekt allein war das nicht zu fangen, denn FxRate.note war fuer diesen
    # Fall schlicht "richtig" im Sinne der (unvollstaendigen) alten Logik. Der
    # Verlust passierte am Uebergang: brickeconomy.get_price haette den
    # Vermerk weiterreichen muessen, egal was FxRate zurueckgibt. Darum hier
    # der Weg durch den echten Aufruf bis in ScrapedPrice.notes, nicht nur ein
    # isolierter Test gegen FxRate.note.
    html = "<!doctype html><html><body><h1>75414 Some Set</h1>\n<p>Value New: $80.00</p></body></html>"

    async def fake_search(self, set_number):
        return "https://www.brickeconomy.com/set/75414-1/Some-Set"

    async def fake_fetch(self, url):
        return html

    as_of = datetime(2026, 6, 1, tzinfo=UTC)

    async def fake_fx():
        return FxRate(usd_to_eur=0.8, as_of=as_of, is_fallback=False, is_stale=True)

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", fake_search)
    monkeypatch.setattr(BrickEconomyScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(brickeconomy, "get_usd_to_eur", fake_fx)

    async with BrickEconomyScraper() as scraper:
        price = await scraper.get_price("75414")

    assert price is not None
    assert price.price_eur == pytest.approx(round(80.00 * 0.8, 2))
    assert "USD 80.00" in price.notes
    assert "Kurs veraltet" in price.notes
    assert "2026-06-01" in price.notes


@pytest.mark.asyncio
async def test_get_set_info_logs_a_dropped_fx_note_with_attribution(monkeypatch, caplog, _caplog_sees_structlog):
    # Review-Finding (Important): ScrapedSetInfo hat kein notes-Feld, also
    # verschwand fx_rate.note hier komplett — auch im "echten" Ersatzwert-Fall,
    # den der Reviewer als Beispiel nannte. Die Ruling war, ScrapedSetInfo
    # nicht zu erweitern (geteilte Form, nicht Teil dieser Aufgabe); die Spur
    # muss also ueber das Log kommen, mit Set-Nummer, damit sie auffindbar ist.
    html = "<!doctype html><html><body><h1>75414 Some Set</h1>\n<p>Retail: $80.00</p></body></html>"

    async def fake_search(self, set_number):
        return "https://www.brickeconomy.com/set/75414-1/Some-Set"

    async def fake_fetch(self, url):
        return html

    async def fake_fx():
        return FxRate(usd_to_eur=0.8, as_of=None, is_fallback=True)

    monkeypatch.setattr(BrickEconomyScraper, "_search_set", fake_search)
    monkeypatch.setattr(BrickEconomyScraper, "_fetch", fake_fetch)
    monkeypatch.setattr(brickeconomy, "get_usd_to_eur", fake_fx)

    async with BrickEconomyScraper() as scraper:
        with caplog.at_level("WARNING"):
            info = await scraper.get_set_info("75414")

    assert info is not None
    assert info.uvp_eur == pytest.approx(round(80.00 * 0.8, 2))
    assert "brickeconomy.uvp_fx_note" in caplog.text
    assert "75414" in caplog.text
    assert "Ersatzkurs" in caplog.text
