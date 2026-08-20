import pytest

from app.scrapers.base import parse_de_price
from app.scrapers.brickmerge import extract_uvp_from_title
from app.scrapers.lego_com import _extract_uvp
from app.tasks.scrape_daily import _is_plausible_price


def test_shared_de_price_parser_handles_all_german_formats():
    assert parse_de_price("701,36 EUR".replace("EUR", "€")) == 701.36
    assert parse_de_price("1.099,99 €") == 1099.99
    assert parse_de_price("1499,99 €") == 1499.99  # kein Match mitten in der Zahl
    assert parse_de_price("849 €") == 849.0
    assert parse_de_price("kein Preis") is None


def test_lego_com_uvp_comes_from_structured_data():
    html = (
        '<html><head><meta property="product:price:amount" content="429.99"></head>'
        '<body>Versand 0,40 €</body></html>'
    )
    assert _extract_uvp(html) == 429.99


def test_lego_com_uvp_reads_json_ld():
    html = '<html><script type="application/ld+json">{"@type":"Product","offers":{"price":"34.99"}}</script></html>'
    assert _extract_uvp(html) == 34.99


def test_lego_com_uvp_is_none_without_structured_price():
    # Produktionsbefund: der erste preisartige Treffer der Seite landete als
    # UVP (0,40 € / 0,11 €). Auch die Gegenrichtung ist gefaehrlich: ein zu
    # hoher Rateversuch (Cross-Sell, Geschenkkarte) blockiert ueber den
    # Plausibilitaetscheck dauerhaft alle korrekten Preise des Sets.
    # Ohne belastbare Quelle also lieber keine UVP.
    html = "<html><body>Versand ab 0,40 € Geschenkkarte 100,00 € Cross-Sell 849,99 €</body></html>"
    assert _extract_uvp(html) is None


def test_brickmerge_uvp_comes_from_title_not_related_products():
    # Related-Bloecke auf derselben Seite tragen fremde UVPs.
    html = (
        '<html><head><title>LEGO® Harry Potter 76417 Gringotts UVP: 429,99 €</title></head>'
        '<body><div class="related">LEGO 76425 Hedwig UVP 69,99 €</div></body></html>'
    )
    assert extract_uvp_from_title(html) == 429.99


def test_brickmerge_uvp_none_when_title_has_no_uvp():
    html = "<html><head><title>LEGO 42143 Ferrari ab 376,99 €</title></head><body>UVP 69,99 €</body></html>"
    assert extract_uvp_from_title(html) is None


@pytest.mark.parametrize("bad_uvp", [0.4, 0.11, 3.0])
def test_plausibility_guard_ignores_implausible_uvp_anchors(bad_uvp):
    # Zirkularitaetsschutz: eine kaputte UVP darf niemals korrekte Preise
    # verwerfen — sie wird als Anker verworfen, nicht der Preis.
    assert _is_plausible_price(69.99, uvp_eur=bad_uvp) is True
