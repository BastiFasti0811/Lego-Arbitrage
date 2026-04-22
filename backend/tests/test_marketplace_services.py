from app.services.bricklink import parse_category_page as parse_bricklink_category_page
from app.services.bricklink import parse_listing_page as parse_bricklink_listing_page
from app.services.whatnot import parse_category_page as parse_whatnot_category_page
from app.services.whatnot import parse_listing_page as parse_whatnot_listing_page


def test_parse_whatnot_category_page_extracts_listing():
    html = """
    <html>
      <body>
        <article>
          <a href="/listing/abc123">LEGO 75313 AT-AT UCS</a>
          <div>Current bid EUR 499</div>
        </article>
      </body>
    </html>
    """

    lots = parse_whatnot_category_page(html, "https://www.whatnot.com/search?query=lego")

    assert len(lots) == 1
    assert lots[0].lot_id == "abc123"
    assert lots[0].current_bid == 499.0
    assert lots[0].set_numbers == ["75313"]


def test_parse_whatnot_listing_page_extracts_price_and_shipping():
    html = """
    <html>
      <body>
        <h1>LEGO 75313 AT-AT UCS</h1>
        <div>Current bid EUR 505</div>
        <div>Shipping EUR 8</div>
      </body>
    </html>
    """

    lot = parse_whatnot_listing_page(html, "https://www.whatnot.com/listing/abc123")

    assert lot.lot_id == "abc123"
    assert lot.current_bid == 505.0
    assert lot.shipping_eur == 8.0
    assert lot.set_numbers == ["75313"]


def test_parse_bricklink_category_page_extracts_set_offer():
    html = """
    <html>
      <body>
        <table>
          <tr>
            <td><a href="/v2/catalog/catalogitem.page?S=75313-1">LEGO Star Wars 75313 AT-AT</a></td>
            <td>EUR 489.95</td>
          </tr>
        </table>
      </body>
    </html>
    """

    lots = parse_bricklink_category_page(html, "https://www.bricklink.com/v2/search.page?q=75313")

    assert len(lots) == 1
    assert lots[0].current_bid == 489.95
    assert lots[0].set_numbers == ["75313"]


def test_parse_bricklink_listing_page_extracts_set_number_from_url():
    html = """
    <html>
      <body>
        <h1>LEGO Star Wars AT-AT</h1>
        <div>EUR 499.95</div>
      </body>
    </html>
    """

    lot = parse_bricklink_listing_page(
        html,
        "https://www.bricklink.com/v2/catalog/catalogitem.page?S=75313-1",
    )

    assert lot.current_bid == 499.95
    assert lot.set_numbers == ["75313"]
