from app.tasks.scrape_daily import _is_plausible_price


def test_price_far_below_uvp_is_rejected():
    # 76417: UVP 429,99 — ein "Preis" von 69,99 stammte von einem Fremdprodukt
    # auf derselben Seite, nicht vom Set.
    assert _is_plausible_price(69.99, uvp_eur=429.99) is False
    assert _is_plausible_price(6.99, uvp_eur=849.99) is False


def test_realistic_discounts_and_premiums_pass():
    assert _is_plausible_price(343.99, uvp_eur=429.99) is True  # 20 % Rabatt
    assert _is_plausible_price(120.0, uvp_eur=429.99) is True   # 72 % Rabatt, noch plausibel
    assert _is_plausible_price(701.36, uvp_eur=849.99) is True
    assert _is_plausible_price(1500.0, uvp_eur=849.99) is True  # EOL-Aufschlag


def test_unknown_uvp_never_blocks():
    assert _is_plausible_price(69.99, uvp_eur=None) is True
    assert _is_plausible_price(69.99, uvp_eur=0) is True
