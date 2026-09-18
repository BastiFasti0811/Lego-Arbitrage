import json

import pytest

from app.services.catawiki import CatawikiParseError, CatawikiScraper, parse_category_page, parse_lot_page


def test_parse_category_page_extracts_lot_and_bid():
    html = """
    <html>
      <body>
        <article>
          <a href="/de/l/102824557">LEGO 10282 Adidas Superstar</a>
          <div>Aktuelles Gebot 111 EUR</div>
        </article>
      </body>
    </html>
    """

    lots = parse_category_page(html, "https://www.catawiki.com/de/c/708-lego")

    assert len(lots) == 1
    assert lots[0].lot_id == "102824557"
    assert lots[0].title == "LEGO 10282 Adidas Superstar"
    assert lots[0].url == "https://www.catawiki.com/de/l/102824557"
    assert lots[0].current_bid == 111.0
    assert lots[0].set_numbers == ["10282"]


def test_parse_lot_page_extracts_bid_shipping_and_set_number():
    html = """
    <html>
      <body>
        <h1>LEGO 10282 Adidas Superstar</h1>
        <div>Aktuelles Gebot 125 EUR</div>
        <div>13 € aus: Belgien, Lieferung in 4-8 Tagen</div>
      </body>
    </html>
    """

    lot = parse_lot_page(html, "https://www.catawiki.com/de/l/102824557")

    assert lot.lot_id == "102824557"
    assert lot.title == "LEGO 10282 Adidas Superstar"
    assert lot.current_bid == 125.0
    assert lot.shipping_eur == 13.0
    assert lot.set_numbers == ["10282"]


@pytest.mark.parametrize("body", [
    "LEGO 10282 2021 731 Teile", "Schaetzwert 200 EUR Versand 13 EUR",
    "Aktuelles Gebot $ 150", "Aktuelles Gebot 150 USD", "Startgebot EUR 50 Aktuelles Gebot unbekannt",
])
def test_missing_bid_never_uses_set_number_shipping_or_foreign_currency(body):
    lot = parse_lot_page(f"<h1>LEGO 10282</h1><p>{body}</p>", "https://www.catawiki.com/de/l/102824557")
    assert lot.current_bid is None


@pytest.mark.parametrize(("text", "expected"), [
    ("Aktuelles Gebot 1.250 EUR", 1250), ("Current bid EUR 1,250.50", 1250.5),
    ("Aktuelles Gebot 1.250,50 EUR", 1250.5), ("Startgebot EUR 50 Aktuelles Gebot 150 EUR", 150),
])
def test_bid_formats_and_current_bid_precedence(text, expected):
    lot = parse_lot_page(f"<h1>LEGO 10282</h1><p>{text}</p>", "https://www.catawiki.com/de/l/102824557")
    assert lot.current_bid == expected


def test_tracking_and_locales_cannot_duplicate_a_lot_or_import_foreign_links():
    html = """<a href='/de/l/102824557-lego?utm_source=x'>LEGO 10282</a>
    <a href='/en/l/102824557-new-title'>LEGO 10282</a>
    <a href='https://evil.example/de/l/102824558'>LEGO 75313</a>"""
    lots = parse_category_page(html, "https://www.catawiki.com/de/c/708-lego")
    assert [lot.url for lot in lots] == ["https://www.catawiki.com/de/l/102824557"]


def test_json_fallback_is_structural_and_ignores_years_and_piece_counts():
    payload = {"props": {"lots": [{"id": 102824557, "title": "LEGO 10282 2021 7310 Teile"}]}}
    lots = parse_category_page(
        f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>', "https://www.catawiki.com/de/c/708-lego",
    )
    assert lots[0].set_numbers == ["10282"]
    assert lots[0].current_bid is None


def test_reads_actual_condition_fees_and_closed_state():
    html = """<main><h1>LEGO 10282 versiegelt</h1><p>Auktion beendet</p>
    <p>Aktuelles Gebot 125 EUR</p><p>13 € aus: Belgien</p>
    <p>Käuferschutzgebühr: 9% + €3</p></main><footer>Aktuelles Gebot 200 EUR</footer>"""
    lot = parse_lot_page(html, "https://www.catawiki.com/de/l/102824557")
    assert lot.condition == "NEW_SEALED"
    assert lot.is_closed and lot.details_verified
    assert (lot.buyer_fee_rate, lot.buyer_fee_fixed) == (0.09, 3)


async def test_unrecognized_page_is_failure_not_empty_success(monkeypatch):
    scraper = CatawikiScraper()
    async def fetch(_url):
        return "<html><body>Access denied</body></html>"
    monkeypatch.setattr(scraper, "_fetch", fetch)
    with pytest.raises(CatawikiParseError):
        await scraper.scan_category("https://www.catawiki.com/de/c/708-lego")
