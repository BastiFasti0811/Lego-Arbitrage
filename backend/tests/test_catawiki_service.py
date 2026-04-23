from app.services.catawiki import parse_category_page, parse_lot_page


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
