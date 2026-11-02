from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from app.scrapers.kleinanzeigen import (
    KLEINANZEIGEN_USER_AGENT,
    KleinanzeigenScraper,
    _parse_ka_price,
    parse_search_page,
)


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


# ── Trefferliste gegen die echte Seite (Layout vom 02.11.2026) ─────────
#
# Bis 01.09.2026 hing der Parser an der Klasse "aditem". Kleinanzeigen hat die
# Suchseite umgebaut; danach meldete die 2h-Lane 63 Laeufe lang
# "offers=0 errors=0", HTTP 200, kein Logeintrag. Die Fixtures sind gekuerzte,
# anonymisierte Kopien der Live-Seite in beiden Varianten (Desktop/Mobil).

FIXTURES = Path(__file__).parent / "fixtures"
DESKTOP = (FIXTURES / "kleinanzeigen_search_10331_desktop.html").read_text(encoding="utf-8")
MOBILE = (FIXTURES / "kleinanzeigen_search_10331_mobile.html").read_text(encoding="utf-8")
NO_RESULTS = (
    "<!doctype html><html><head><title>LEGO 98765432 kleinanzeigen.de</title></head><body>"
    "<main id='srchrslt-content'><h2>Ergebnisse</h2>"
    "<p>Es wurden leider keine Ergebnisse für „LEGO 98765432“ gefunden.</p></main></body></html>"
)


def test_desktop_layout_yields_every_card():
    page = parse_search_page(DESKTOP)

    assert page.cards == 5 and page.no_results is False
    assert [(hit.title, hit.price_text) for hit in page.hits] == [
        ("Lego Eisvogel (10331)", "30 €"),
        ("Lego Botanicals 10331 Orchidee", "19 € VB"),
        # Preissenkung: der alte Preis steht durchgestrichen dahinter.
        ("LEGO 10331 Eisvogel Kingfisher Set", "20 € VB"),
        # "Versand für 6 €" / "für 25,-€" in der Beschreibung ist kein Preis.
        ("Lego ICONS 10331 - Kingfisher - Eisvogel Set", "25 €"),
        ("LEGO 10331 Icons Eisvogel", "18 €"),
    ]
    first = page.hits[0]
    assert first.url == "https://www.kleinanzeigen.de/s-anzeige/lego-eisvogel-10331-/1000000001-23-3511"
    assert first.location == "01234 Musterstadt"


def test_mobile_layout_survives_the_truncated_description():
    # Karte 2 endet live mit "<br ..." -- der HTML-Parser schachtelt die
    # Karten 3 und 4 deshalb IN Karte 2. Jede Karte behaelt trotzdem ihren
    # eigenen Titel und Preis.
    page = parse_search_page(MOBILE)

    assert page.cards == 4
    assert [(hit.title, hit.price_text, hit.location) for hit in page.hits] == [
        ("LEGO 10331 Icons Eisvogel", "18 €", "Musterstadt"),
        ("Lego 10331 Icons Eisvogel gebraucht", "25 € VB", "Hamburg"),
        ("Lego 10331 Kingfisher Eisvogel Set Weihnachten OVP Top", "22 €", "München"),
        ("Lego 10331 Icons Kingfisher", "30 €", "Köln"),
    ]
    assert page.hits[1].url.endswith("/2000000002-23-2155")


def test_an_empty_search_is_not_a_drift():
    page = parse_search_page(NO_RESULTS)
    assert page.hits == [] and page.cards == 0 and page.no_results is True


def _scraper_serving(html, seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=html, headers={"content-type": "text/html;charset=utf-8"}, request=request)

    scraper = KleinanzeigenScraper()
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _no_delay():
        return None

    scraper._delay = _no_delay
    return scraper


@pytest.mark.asyncio
async def test_get_offers_reads_the_live_layout_with_a_desktop_user_agent():
    seen = []
    scraper = _scraper_serving(DESKTOP, seen)

    offers = await scraper.get_offers("10331")
    await scraper.close()

    assert str(seen[0].url) == "https://www.kleinanzeigen.de/s-LEGO+10331/k0"
    # Mit Mobil-UA kommt ein anderes Layout und eine Detailseite ohne "Zustand".
    assert seen[0].headers["User-Agent"] == KLEINANZEIGEN_USER_AGENT
    assert not any(token in KLEINANZEIGEN_USER_AGENT for token in ("Mobile", "iPhone", "Android"))
    assert len(offers) == 5
    assert {offer.platform for offer in offers} == {"KLEINANZEIGEN"}
    assert [offer.price_eur for offer in offers] == [30.0, 19.0, 20.0, 25.0, 18.0]
    assert offers[2].offer_url == "https://www.kleinanzeigen.de/s-anzeige/lego-10331-eisvogel-kingfisher-set/1000000003-23-21286"
    assert offers[1].seller_location == "20095 Hamburg"


@pytest.mark.asyncio
async def test_get_price_uses_the_same_cards():
    scraper = _scraper_serving(DESKTOP, [])

    price = await scraper.get_price("10331")
    await scraper.close()

    assert price is not None
    assert (price.min_price, price.median_price, price.max_price, price.sold_count) == (18.0, 20.0, 30.0, 5)


@pytest.mark.asyncio
async def test_an_unreadable_result_page_is_logged_not_swallowed():
    # Die alte Seite ohne data-adid: genau dieser Fall lief zwei Monate stumm.
    old_layout = (
        "<!doctype html><html><body><ul><li class='ad-listitem'>"
        "<article class='aditem'>x</article></li></ul></body></html>"
    )
    scraper = _scraper_serving(old_layout, [])

    with capture_logs() as logs:
        offers = await scraper.get_offers("10331")
    await scraper.close()

    assert offers == []
    assert [entry["event"] for entry in logs if entry["log_level"] == "warning"] == ["kleinanzeigen.search_unreadable"]


@pytest.mark.asyncio
async def test_an_empty_search_logs_no_warning():
    scraper = _scraper_serving(NO_RESULTS, [])

    with capture_logs() as logs:
        offers = await scraper.get_offers("98765432")
    await scraper.close()

    assert offers == []
    assert not [entry for entry in logs if entry["log_level"] == "warning"]
