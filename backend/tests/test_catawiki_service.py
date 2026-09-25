"""Catawiki-Parser gegen echte Seiten vom 25.09.2026 (tests/fixtures/catawiki_*).

Die HTML-Vorlagen sind unveraendert bis auf die entfernten Uebersetzungstexte
(`pageProps._nextI18Next`), die JSON-Vorlagen sind die rohen API-Antworten.
"""

import json
from pathlib import Path

import pytest

from app.services.catawiki import (
    CatawikiParseError,
    CatawikiScraper,
    condition_from_catawiki,
    lot_review_reasons,
    needs_lot_details,
    parse_category_page,
    parse_commission,
    parse_lot_page,
    parse_shipping_rates,
)

FIXTURES = Path(__file__).parent / "fixtures"
DAILY_BUGLE = "https://www.catawiki.com/de/l/107023178-lego-set-76178-marvel-daily-bugle-no-minifigures"
KRUSTY = "https://www.catawiki.com/de/l/106997010"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- Losseite -----------------------------------------------------------------


def test_real_lot_reads_bid_and_open_state_from_structured_data():
    lot = parse_lot_page(_fixture("catawiki_lot_107023178.html"), DAILY_BUGLE)

    assert lot.lot_id == "107023178"
    assert lot.url == "https://www.catawiki.com/de/l/107023178"
    assert lot.title == "Lego Set - 76178 - Marvel - Daily Bugle - NO MINIFIGURES"
    assert lot.current_bid == 185.0
    assert lot.set_numbers == ["76178"]
    assert lot.details_verified
    # Der alte Textparser las "Verkauft von Klooster Collectables" als beendet.
    assert not lot.is_closed


def test_real_lot_without_minifigures_is_never_sealed():
    # Der Seitentext sagt "ungebraucht und in ungeoeffneter Packung"; die Details
    # sagen "Vollstaendiges Set: Nein" und "Dichtungen defekt". Der alte
    # Textparser machte daraus NEW_SEALED -- die einzige Aufwertung auf 100 %.
    lot = parse_lot_page(_fixture("catawiki_lot_107023178.html"), DAILY_BUGLE)

    assert lot.condition == "USED_INCOMPLETE"
    assert "Nicht als neu und versiegelt bestaetigt: Zustand manuell pruefen." in lot_review_reasons(lot)


def test_real_sealed_lot_is_new_sealed_without_box_damage():
    lot = parse_lot_page(_fixture("catawiki_lot_106997010.html"), KRUSTY)

    assert lot.set_numbers == ["10352"]
    assert (lot.condition, lot.box_damage) == ("NEW_SEALED", False)
    assert lot.current_bid is not None


def test_real_lot_without_bids_uses_start_price_and_unclear_box_is_unknown():
    # Startpreis 1 EUR, noch kein Gebot: live.bid.EUR ist dort 0.
    lot = parse_lot_page(_fixture("catawiki_lot_107063422.html"), "https://www.catawiki.com/de/l/107063422")
    assert lot.current_bid == 1.0
    # "in geschlossener Box" sagt nicht "versiegelt": pruefen statt freigeben.
    assert lot.condition == "UNKNOWN"


def test_lot_page_without_next_data_stays_unverified():
    lot = parse_lot_page("<html><h1>LEGO 10282 versiegelt</h1><p>Aktuelles Gebot 125 EUR</p></html>",
                         "https://www.catawiki.com/de/l/102824557")

    assert not lot.details_verified
    assert lot.current_bid is None
    assert lot.condition == "UNKNOWN"
    assert "Losdetails nicht geladen oder nicht lesbar." in lot_review_reasons(lot)


def test_next_data_of_another_lot_is_not_trusted():
    html = _fixture("catawiki_lot_107023178.html")
    lot = parse_lot_page(html, "https://www.catawiki.com/de/l/999999")
    assert not lot.details_verified


def test_unreadable_lot_page_raises():
    with pytest.raises(CatawikiParseError):
        parse_lot_page("<html><body>Bitte bestaetigen</body></html>", "https://www.catawiki.com/de/l/102824557")


# --- Versand und Gebuehr (JSON) -----------------------------------------------


def test_real_shipping_rates_pick_germany_in_euro():
    payload = json.loads(_fixture("catawiki_shipping_107023178.json"))
    # Die Seite zeigt "13 € aus: Niederlande"; die Tarifliste hat 60+ Laender.
    assert parse_shipping_rates(payload) == 13.0
    assert parse_shipping_rates(payload, "at") == 19.0
    assert parse_shipping_rates({"shipping": {"rates": []}}) is None


def test_real_commission_is_nine_percent_plus_three_euro():
    payload = json.loads(_fixture("catawiki_commission_107023178.json"))
    assert parse_commission(payload) == (0.09, 3.0)
    assert parse_commission({**payload, "currency_code": "USD"}) == (None, None)


async def test_get_lot_combines_page_shipping_and_fee(monkeypatch):
    scraper = CatawikiScraper()
    json_calls = []

    async def fetch(url):
        assert "/de/l/" in url
        return _fixture("catawiki_lot_106997010.html")

    async def fetch_json(url):
        json_calls.append(url)
        name = "shipping" if "/shipping" in url else "commission"
        return json.loads(_fixture(f"catawiki_{name}_107023178.json"))

    monkeypatch.setattr(scraper, "_fetch", fetch)
    monkeypatch.setattr(scraper, "_fetch_json", fetch_json)
    lot = await scraper.get_lot(KRUSTY)

    assert (lot.shipping_eur, lot.buyer_fee_rate, lot.buyer_fee_fixed) == (13.0, 0.09, 3.0)
    assert "/lots/106997010/shipping" in json_calls[0] and "currency_code=EUR" in json_calls[0]
    assert "/lots/106997010/commission" in json_calls[1]
    assert lot_review_reasons(lot) == []


async def test_get_lot_without_shipping_api_stays_blocked(monkeypatch):
    import httpx

    scraper = CatawikiScraper()

    async def fetch(url):
        return _fixture("catawiki_lot_106997010.html")

    async def fetch_json(url):
        raise httpx.ConnectError("blocked")

    monkeypatch.setattr(scraper, "_fetch", fetch)
    monkeypatch.setattr(scraper, "_fetch_json", fetch_json)
    lot = await scraper.get_lot(KRUSTY)

    assert lot.shipping_eur is None
    assert "Versand nach Deutschland fehlt: Kosten am Los pruefen." in lot_review_reasons(lot)


async def test_fetch_json_accepts_json_that_the_html_check_would_reject(monkeypatch):
    # BaseScraper._fetch haelt JSON fuer Binaerdaten (keine HTML-Marker).
    import httpx

    body = _fixture("catawiki_commission_107023178.json")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body, request=request))
    scraper = CatawikiScraper()
    scraper._client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(scraper, "_delay", lambda: _noop())
    payload = await scraper._fetch_json("https://www.catawiki.com/fees/api/v1/buyer/lots/1/commission")
    assert payload["variable"]["percentage"] == 9.0
    await scraper.close()


async def _noop():
    return None


def test_scraper_sends_a_fixed_browser_user_agent():
    # Mit rotierendem Zufalls-UA antwortet Akamai auf Losseiten mit 403.
    assert "Chrome/" in CatawikiScraper().user_agent_override
    assert CatawikiScraper(user_agent="Custom/1").user_agent_override == "Custom/1"


# --- Zustand aus den Detailangaben ------------------------------------------


@pytest.mark.parametrize(("zustand", "verpackung", "complete", "title", "expected"), [
    # Katalogtexte (Losliste, Heimrechner) wie auf den echten Losen.
    ("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", "Ja", "", ("NEW_SEALED", False)),
    ("Unbenutzt", "In beschädigter ungeöffneter Originalverpackung", "Ja", "", ("NEW_SEALED", True)),
    ("Unbenutzt", "ungeöffnete Schachtel Dichtungen defekt", "Ja", "", ("NEW_OPEN_BOX", False)),
    ("Unbenutzt", "In unbeschädigter geöffneter Originalverpackung", "Ja", "", ("NEW_OPEN_BOX", False)),
    ("Unbenutzt", "in geschlossener Box", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "Ohne Originalverpackung", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", "Nein", "", ("USED_INCOMPLETE", False)),
    ("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", None, "Set - NO MINIFIGURES",
     ("USED_INCOMPLETE", False)),
    ("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", None, "LEGO 10352 without minifigures",
     ("USED_INCOMPLETE", False)),
    ("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", None, "Set ohne Figuren",
     ("USED_INCOMPLETE", False)),
    ("Gebraucht", "Mit Original-Kasten", "Ja", "", ("USED_COMPLETE", False)),
    ("Neuwertig", "In unbeschädigter und versiegelter Originalverpackung", "Ja", "", ("USED_COMPLETE", False)),
    (None, None, None, "", ("UNKNOWN", False)),
    # Freitext ausserhalb des Katalogs stuft nie hoch (Review B1 und Delta fc7bf64).
    ("Unbenutzt", "versiegelt", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "nicht versiegelt", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "nicht mehr ganz versiegelt", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "Unversiegelte Originalverpackung", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "Re-sealed", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "OVP versiegelt (Siegel leicht eingerissen)", "Ja", "", ("UNKNOWN", False)),
    ("Unbenutzt", "sealed, seal torn", "Ja", "", ("UNKNOWN", False)),
    ("Neu mit Mängeln", "In unbeschädigter und versiegelter Originalverpackung", "Ja", "", ("UNKNOWN", False)),
])
def test_condition_from_catawiki_specifications(zustand, verpackung, complete, title, expected):
    assert condition_from_catawiki(zustand, verpackung, complete, title) == expected


def test_catalog_ids_win_over_text():
    # Mit ID (Losseite) zaehlt die ID, auch wenn der Text anders aussieht.
    assert condition_from_catawiki("Unbenutzt", "irgendwas", "Ja", "", zustand_id=165212, verpackung_id=80575) == (
        "NEW_SEALED", False,
    )
    assert condition_from_catawiki("Unbenutzt", "In unbeschädigter und versiegelter Originalverpackung", "Ja", "",
                                   zustand_id=165212, verpackung_id=92701) == ("UNKNOWN", False)
    assert condition_from_catawiki("Unbenutzt", "", "Ja", "", zustand_id=99999, verpackung_id=80575) == (
        "UNKNOWN", False,
    )


# --- Auktions- und Kategorieseiten --------------------------------------------


def test_real_auction_page_lists_only_its_lots():
    lots = parse_category_page(_fixture("catawiki_auction_1256209.html"),
                               "https://www.catawiki.com/de/a/1256209-lego-auktion-film-und-fernsehen")

    # Die HTML-Links enthielten auch Auktion 1256209 und die Kategorien 1461/375.
    assert len(lots) == 51
    assert all(lot.url.startswith("https://www.catawiki.com/de/l/1") for lot in lots)
    first = lots[0]
    assert (first.lot_id, first.set_numbers) == ("107023178", ["76178"])
    assert first.condition == "USED_INCOMPLETE"  # "NO MINIFIGURES" im Titel
    assert first.current_bid is None  # Gebote liest erst get_lot


def test_real_category_page_reads_category_lots_not_similar_lots():
    lots = parse_category_page(_fixture("catawiki_category_375.html"), "https://www.catawiki.com/de/c/375-lego")
    assert len(lots) == 24
    assert len({lot.lot_id for lot in lots}) == 24


def test_prefilter_skips_used_and_multi_set_lots_from_the_list():
    lots = {lot.lot_id: lot for lot in parse_category_page(
        _fixture("catawiki_auction_1256209.html"), "https://www.catawiki.com/de/a/1256209")}

    assert needs_lot_details(lots["106997010"])  # 10352, versiegelt
    assert not needs_lot_details(lots["107023178"])  # ohne Minifiguren
    assert not needs_lot_details(lots["107022231"])  # gebraucht
    assert not needs_lot_details(lots["106901231"])  # 3x Batman


def test_list_fallback_without_next_data_dedupes_and_rejects_foreign_links():
    html = """<a href='/de/l/102824557-lego?utm_source=x'>LEGO 10282</a>
    <a href='/en/l/102824557-new-title'>LEGO 10282</a>
    <a href='https://evil.example/de/l/102824558'>LEGO 75313</a>"""
    lots = parse_category_page(html, "https://www.catawiki.com/de/c/708-lego")
    assert [lot.url for lot in lots] == ["https://www.catawiki.com/de/l/102824557"]
    assert lots[0].condition == "UNKNOWN"


def test_list_ignores_years_and_piece_counts_in_titles():
    payload = {"props": {"pageProps": {"lots": [{"id": 102824557, "title": "LEGO 10282 2021 7310 Teile"}]}}}
    lots = parse_category_page(
        f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>', "https://www.catawiki.com/de/c/708-lego",
    )
    assert lots[0].set_numbers == ["10282"]


async def test_unrecognized_page_is_failure_not_empty_success(monkeypatch):
    scraper = CatawikiScraper()

    async def fetch(_url):
        return "<html><body>Access denied</body></html>"

    monkeypatch.setattr(scraper, "_fetch", fetch)
    with pytest.raises(CatawikiParseError):
        await scraper.scan_category("https://www.catawiki.com/de/c/708-lego")


# --- Review S4/S5/N2: Strukturdrift und Waehrung --------------------------------


def _lot_html(**props) -> str:
    base = {"lotDetailsData": {"lotId": 102824557, "lotTitle": "LEGO 10282 Adidas", "specifications": []}}
    base.update(props)
    return f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": base}})}</script>'


def test_changed_data_shape_is_unverified_not_a_crash():
    html = _lot_html(lotDetailsData={"lotId": 102824557, "lotTitle": "LEGO 10282", "specifications": {"x": 1}})
    with pytest.raises(CatawikiParseError):
        # Keine h1 und keine brauchbaren Daten: lesbar ist davon nichts.
        parse_lot_page(html, "https://www.catawiki.com/de/l/102824557")
    lot = parse_lot_page(_lot_html(biddingBlockResponse=["kaputt"], userData="x"),
                         "https://www.catawiki.com/de/l/102824557")
    assert lot.details_verified and lot.current_bid is None


def test_live_bid_must_name_its_currency():
    # Ein nackter Skalar koennte Nutzerwaehrung sein: nicht als EUR lesen.
    lot = parse_lot_page(_lot_html(biddingBlockResponse={"live": {"lot": {"bid": 150}}}),
                         "https://www.catawiki.com/de/l/102824557")
    assert lot.current_bid is None


def test_shipping_takes_cheapest_german_rate_and_survives_odd_shapes():
    payload = {"shipping": {"rates": [
        {"region_code": "de", "price": 2500.0, "currency_code": "EUR"},
        {"region_code": "de", "price": 1300.0, "currency_code": "EUR"},
        "kaputt",
    ]}}
    assert parse_shipping_rates(payload) == 13.0
    assert parse_shipping_rates({"shipping": ["x"]}) is None
    assert parse_shipping_rates(["x"]) is None
    assert parse_commission(["x"]) == (None, None)
