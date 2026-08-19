import pytest

from app.scrapers.base import parse_de_price
from app.scrapers.lego_com import _extract_uvp
from app.tasks.scrape_daily import _is_plausible_price


def test_shared_de_price_parser_handles_all_german_formats():
    assert parse_de_price("701,36 €") == 701.36
    assert parse_de_price("1.099,99 €") == 1099.99
    assert parse_de_price("1499,99 €") == 1499.99  # kein Match mitten in der Zahl
    assert parse_de_price("849 €") == 849.0
    assert parse_de_price("kein Preis") is None


def test_lego_com_uvp_ignores_unrelated_small_amounts():
    # Produktionsbefund: der erste preisartige Treffer der Seite landete als
    # UVP in der DB — Anakins Interceptor bekam "0,40 €", der Mandalorian-
    # Transporter "0,11 €". Solche Beträge sind nie eine Set-UVP.
    page = "Versandkosten ab 0,40 € Preis pro Teil 0,11 € Jetzt kaufen 34,99 € inkl. MwSt."
    assert _extract_uvp(page) == 34.99


def test_lego_com_uvp_returns_none_without_plausible_price():
    assert _extract_uvp("Dieses Produkt ist nicht verfügbar. Versand 0,40 €") is None


@pytest.mark.parametrize("bad_uvp", [0.4, 0.11, 3.0])
def test_plausibility_guard_ignores_implausible_uvp_anchors(bad_uvp):
    # Zirkularitätsschutz: eine kaputte UVP darf niemals korrekte Preise
    # verwerfen — sie wird als Anker verworfen, nicht der Preis.
    assert _is_plausible_price(69.99, uvp_eur=bad_uvp) is True
