"""The scraper reads the listing page instead of guessing from the title.

The fixture is a reduced copy of the real kingfisher ad — same class names,
same id, same text. It was worth 10 EUR against a 37,89 EUR market price only
as long as nobody read "Ohne Anleitung und Karton".
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.scrapers.base import OfferDetails
from app.scrapers.kleinanzeigen import KleinanzeigenScraper
from app.tasks import scrape_daily

FIXTURE = Path(__file__).parent / "fixtures" / "kleinanzeigen_detail_10331.html"
URL = "https://www.kleinanzeigen.de/s-anzeige/lego-eisvogel-10331/3489320542-23-1324"


def _now():
    return datetime.now(UTC)


def _scraper_returning(html_or_error, monkeypatch):
    scraper = KleinanzeigenScraper()

    async def _fake_fetch(_self, _url):
        if isinstance(html_or_error, Exception):
            raise html_or_error
        return html_or_error

    monkeypatch.setattr(KleinanzeigenScraper, "_fetch", _fake_fetch)
    return scraper


def _http_error(status):
    request = httpx.Request("GET", URL)
    return httpx.HTTPStatusError("blocked", request=request, response=httpx.Response(status, request=request))


@pytest.mark.asyncio
async def test_reads_condition_and_description_from_the_real_page(monkeypatch):
    scraper = _scraper_returning(FIXTURE.read_text(encoding="utf-8"), monkeypatch)

    details = await scraper.fetch_offer_details(URL)

    assert details is not None
    assert details.condition == "USED_INCOMPLETE", "Zustand 'Sehr Gut' plus 'ohne Karton' ist unvollstaendig"
    assert details.box_damage is False
    assert "Ohne Anleitung und Karton" in details.description


@pytest.mark.asyncio
async def test_page_without_condition_field_stays_unknown(monkeypatch):
    html = "<html><body><p id='viewad-description-text'>Schoenes Set</p></body></html>"
    scraper = _scraper_returning(html, monkeypatch)

    details = await scraper.fetch_offer_details(URL)

    assert details.condition == "UNKNOWN"


@pytest.mark.asyncio
async def test_rate_limit_is_raised_so_the_caller_can_back_off(monkeypatch):
    for status in (403, 429):
        scraper = _scraper_returning(_http_error(status), monkeypatch)
        with pytest.raises(httpx.HTTPStatusError):
            await scraper.fetch_offer_details(URL)


@pytest.mark.asyncio
async def test_other_failures_leave_the_offer_unenriched(monkeypatch):
    # A single broken listing must not abort the run.
    scraper = _scraper_returning(_http_error(500), monkeypatch)
    assert await scraper.fetch_offer_details(URL) is None

    scraper = _scraper_returning(RuntimeError("boom"), monkeypatch)
    assert await scraper.fetch_offer_details(URL) is None


# ── Anreicherung im Scrape-Lauf ──────────────────────────────────────


class _RecordingScraper:
    """Zaehlt, fuer welche Angebote die Detailseite geholt wurde."""

    def __init__(self, details=None):
        self.requested = []
        self._details = details or OfferDetails(condition="USED_INCOMPLETE", box_damage=False)

    async def fetch_offer_details(self, url):
        self.requested.append(url)
        return self._details


def _offer(title, price, url):
    return SimpleNamespace(
        offer_title=title, price_eur=price, offer_url=url, condition="UNKNOWN", box_damage=False, sealed=True
    )


def _lego_set():
    return SimpleNamespace(
        id=1, set_number="10331", set_name="Eisvogel", current_market_price=37.89, uvp_eur=19.99
    )


@pytest.mark.asyncio
async def test_only_plausible_set_offers_cost_a_request():
    # Der Wandhalterungs-Treffer darf keinen Detail-Request ausloesen.
    scraper = _RecordingScraper()
    offers = [
        _offer("Lego Eisvogel 10331", 10.0, "https://www.kleinanzeigen.de/s-anzeige/a/1-2-3"),
        _offer("Wandhalterung für Lego 10331", 9.99, "https://www.kleinanzeigen.de/s-anzeige/b/4-5-6"),
    ]

    enriched = await scrape_daily._enrich_offer_details(scraper, _lego_set(), offers)

    assert enriched == 1
    assert scraper.requested == ["https://www.kleinanzeigen.de/s-anzeige/a/1-2-3"]


@pytest.mark.asyncio
async def test_condition_from_the_page_lands_on_the_offer():
    scraper = _RecordingScraper(OfferDetails(condition="USED_INCOMPLETE", box_damage=True))
    offer = _offer("Lego Eisvogel 10331", 10.0, "https://www.kleinanzeigen.de/s-anzeige/a/1-2-3")

    await scrape_daily._enrich_offer_details(scraper, _lego_set(), [offer])

    assert offer.condition == "USED_INCOMPLETE"
    assert offer.box_damage is True
    assert offer.sealed is False


@pytest.mark.asyncio
async def test_scrapers_without_the_capability_are_skipped():
    # Amazon und eBay liefern keine Detailseiten-Methode.
    offer = _offer("Lego Eisvogel 10331", 10.0, "https://www.amazon.de/dp/B0CJ2X5Q1T")

    enriched = await scrape_daily._enrich_offer_details(SimpleNamespace(), _lego_set(), [offer])

    assert enriched == 0
    assert offer.condition == "UNKNOWN"


@pytest.mark.asyncio
async def test_rate_limit_propagates_so_the_lane_aborts():
    class _BlockedScraper:
        async def fetch_offer_details(self, _url):
            raise _http_error(429)

    offer = _offer("Lego Eisvogel 10331", 10.0, "https://www.kleinanzeigen.de/s-anzeige/a/1-2-3")

    with pytest.raises(httpx.HTTPStatusError):
        await scrape_daily._enrich_offer_details(_BlockedScraper(), _lego_set(), [offer])


# ── Begrenzung des Fussabdrucks ──────────────────────────────────────


def _many_offers(count, first_price=30.0):
    # Absteigende Preise: das billigste Angebot steht am Ende der Liste,
    # damit ein Cap, der stumpf nach Listenreihenfolge schneidet, auffliegt.
    return [
        _offer(
            "Lego Eisvogel 10331",
            first_price - i,
            f"https://www.kleinanzeigen.de/s-anzeige/a/{i}",
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_the_detail_fetch_is_capped_per_set(monkeypatch):
    # Ein Set mit vielen Treffern darf den Lauf nicht in dutzende Requests
    # ziehen: 1 Listen-Request plus N Detailseiten, alle 6 h, mal Watchlist.
    monkeypatch.setattr(scrape_daily.settings, "scraper_detail_max_per_set", 3)
    scraper = _RecordingScraper()

    enriched = await scrape_daily._enrich_offer_details(scraper, _lego_set(), _many_offers(10))

    assert enriched == 3
    assert len(scraper.requested) == 3


@pytest.mark.asyncio
async def test_the_cheapest_offers_get_the_budget(monkeypatch):
    # Wird gekappt, muss das Budget dorthin, wo der Zustand ueber GO/NO-GO
    # entscheidet — ein Angebot weit unter Marktpreis. Ein Cap nach
    # Listenreihenfolge wuerfe genau den Fund weg, wegen dem es das Feature gibt.
    monkeypatch.setattr(scrape_daily.settings, "scraper_detail_max_per_set", 2)
    scraper = _RecordingScraper()

    await scrape_daily._enrich_offer_details(scraper, _lego_set(), _many_offers(5))

    assert scraper.requested == [
        "https://www.kleinanzeigen.de/s-anzeige/a/4",
        "https://www.kleinanzeigen.de/s-anzeige/a/3",
    ]


@pytest.mark.asyncio
async def test_a_page_without_a_result_still_costs_budget(monkeypatch):
    # Der Cap begrenzt Requests, nicht Treffer: eine Detailseite, die nichts
    # hergibt, hat den Host trotzdem belastet.
    monkeypatch.setattr(scrape_daily.settings, "scraper_detail_max_per_set", 2)

    class _EmptyScraper(_RecordingScraper):
        async def fetch_offer_details(self, url):
            self.requested.append(url)
            return None

    scraper = _EmptyScraper()

    enriched = await scrape_daily._enrich_offer_details(scraper, _lego_set(), _many_offers(5))

    assert enriched == 0
    assert len(scraper.requested) == 2


@pytest.mark.asyncio
async def test_offers_without_a_price_still_get_enriched(monkeypatch):
    # Ein fehlender Preis darf nicht durch die Sortierung ans Ende rutschen und
    # unter den Tisch fallen — er ist unbekannt, nicht teuer.
    monkeypatch.setattr(scrape_daily.settings, "scraper_detail_max_per_set", 5)
    scraper = _RecordingScraper()
    offer = _offer("Lego Eisvogel 10331", None, "https://www.kleinanzeigen.de/s-anzeige/vb/9")

    enriched = await scrape_daily._enrich_offer_details(scraper, _lego_set(), [offer])

    assert enriched == 1


# ── Ein geprueftes Ergebnis darf nicht zurueckfallen ─────────────────


class _RowsResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _RecordingSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []

    async def execute(self, _query):
        return _RowsResult(self.rows)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _stored_offer(url, condition, box_damage=False, sealed=False):
    return SimpleNamespace(
        platform="KLEINANZEIGEN", offer_url=url, offer_title="Lego Eisvogel 10331",
        price_eur=10.0, shipping_eur=None, total_price_eur=10.0,
        condition=condition, box_damage=box_damage, sealed=sealed,
        seller_name=None, seller_rating=None, seller_location=None,
        status="ACTIVE", last_seen_at=None, is_auction=False, auction_end=None,
    )


def _from_list(url, condition="UNKNOWN"):
    """Wie ein Angebot aus der Ergebnisliste aussieht: geraten, nicht gelesen."""
    return SimpleNamespace(
        platform="KLEINANZEIGEN", offer_url=url, offer_title="Lego Eisvogel 10331",
        price_eur=10.0, shipping_eur=None, condition=condition, box_damage=False,
        sealed=False, seller_name=None, seller_rating=None, seller_location=None,
        is_auction=False, auction_end=None, details_verified=False,
    )


URL_A = "https://www.kleinanzeigen.de/s-anzeige/a/1-2-3"


@pytest.mark.asyncio
async def test_an_unverified_run_does_not_overwrite_a_verified_condition():
    # Faellt ein Angebot beim naechsten Lauf aus dem Detail-Cap, liefert die
    # Liste wieder "UNKNOWN". Das darf ein gelesenes USED_INCOMPLETE nicht
    # ersetzen — der erwartete Erloes spraenge sonst von 0.5 auf 0.7.
    existing = _stored_offer(URL_A, "USED_INCOMPLETE", box_damage=True)
    session = _RecordingSession([existing])

    await scrape_daily._upsert_offers(session, _lego_set(), [_from_list(URL_A)], _now())

    assert existing.condition == "USED_INCOMPLETE"
    assert existing.box_damage is True


@pytest.mark.asyncio
async def test_a_verified_run_does_overwrite():
    existing = _stored_offer(URL_A, "USED_INCOMPLETE")
    session = _RecordingSession([existing])
    fresh = _from_list(URL_A, "NEW_SEALED")
    fresh.details_verified = True

    await scrape_daily._upsert_offers(session, _lego_set(), [fresh], _now())

    assert existing.condition == "NEW_SEALED"


@pytest.mark.asyncio
async def test_enrichment_marks_the_offer_as_verified():
    scraper = _RecordingScraper(OfferDetails(condition="USED_INCOMPLETE", box_damage=False))
    offer = _offer("Lego Eisvogel 10331", 10.0, URL_A)
    offer.details_verified = False

    await scrape_daily._enrich_offer_details(scraper, _lego_set(), [offer])

    assert offer.details_verified is True


def test_the_capability_probe_matches_the_real_method_name():
    # _enrich_offer_details erkennt die Faehigkeit per hasattr. Ein Tippfehler
    # im Methodennamen waere sonst stillschweigend "Scraper kann das nicht".
    assert hasattr(KleinanzeigenScraper, "fetch_offer_details")
