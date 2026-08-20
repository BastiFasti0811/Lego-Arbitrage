import pytest

from app.domain.identity import is_set_offer

SET = "42143"
REFERENCE = 400.0

# Echte Titel aus der Produktions-DB (Stand 2026-08-19).
ACCESSORIES = [
    "LocoLee Led Licht Set Kompatibel mit LEGO Ferrari Daytona SP3 42143",
    "Wandhalterung Haken für Lego Ferrari Daytona SP3 42143",
    "Acryl-Display-Vitrine Kompatibel mit Lego Ferrari Daytona SP3 42143",
    "Led Licht Set für Lego 42143 Ferrari Daytona SP3 (Kein Lego)",
]

REAL_SETS = [
    "Lego 42143 OVP",
    "LEGO Technic 42143 Ferrari Daytona SP3 - neu und original verpackt",
    "Lego Technic Ferrari 42143",
    "LEGO Technic 42143 Ferrari Daytona SP3 Bauspielzeug-Set für Erwachsene",
]


@pytest.mark.parametrize("title", ACCESSORIES)
def test_accessories_are_not_the_set(title):
    # Diese Titel erzeugten GO_STAR mit bis zu 869 % ROI, weil eine 9,99-EUR-
    # Wandhalterung gegen den Setpreis von ~400 EUR gerechnet wurde.
    assert is_set_offer(title, SET, price_eur=9.99, reference_price=REFERENCE) is False


@pytest.mark.parametrize("title", REAL_SETS)
def test_real_set_listings_pass(title):
    assert is_set_offer(title, SET, price_eur=320.0, reference_price=REFERENCE) is True


def test_unknown_accessory_type_caught_by_price_floor():
    # Zubehör ohne bekanntes Stichwort: 5 % des Referenzpreises kann das Set
    # nicht sein.
    assert is_set_offer("Lego 42143 Zubehoerteil XY", SET, price_eur=19.99, reference_price=REFERENCE) is False


def test_price_floor_is_skipped_without_reference():
    assert is_set_offer("Lego 42143 OVP", SET, price_eur=19.99, reference_price=None) is True


def test_bargain_within_reason_still_passes():
    # Ein echtes Schnaeppchen (40 % unter Markt) darf nicht wegen des Preises
    # aussortiert werden — genau das soll das System ja finden.
    assert is_set_offer("Lego 42143 OVP", SET, price_eur=240.0, reference_price=REFERENCE) is True


def test_offer_for_a_different_set_is_rejected():
    assert is_set_offer("LEGO Star Wars 75192 UCS Millennium Falke", SET, price_eur=550.0) is False


def test_empty_box_listings_are_not_the_set():
    # Aus der Produktion: "Lego City 60337 - Leerkarton -" fuer 10 EUR.
    assert is_set_offer("Lego City 60337 - Leerkarton -", "60337", price_eur=10.0) is False
    assert is_set_offer("LEGO 75192 nur die Verpackung", "75192", price_eur=25.0) is False


def test_every_offer_ingest_path_applies_the_filter():
    # Review-Finding H4: der Live-Scout rief analyze_deal direkt auf und
    # meldete weiterhin Wandhalterungen als Deals. Alle Pfade, die Angebote
    # bewerten, muessen durch dieselbe Pruefung.
    import inspect

    from app.api.routes import scout
    from app.tasks import scrape_daily

    for module in (scrape_daily, scout):
        assert "is_set_offer" in inspect.getsource(module), module.__name__


def test_genuine_bargain_is_not_filtered_out():
    # Review-Finding H2: ein 400-EUR-Set fuer 90 EUR ist der Fund, fuer den
    # die Pipeline existiert — der Preisboden darf ihn nicht verwerfen.
    assert is_set_offer(
        "LEGO 42143 Ferrari Daytona SP3 - Nachlassaufloesung", SET, price_eur=90.0, reference_price=400.0
    ) is True


def test_set_with_bonus_lighting_is_still_the_set():
    # Review-Finding H3: "mit LED-Beleuchtung" ist die verbreitetste Art, ein
    # bereits beleuchtetes Set anzubieten — wertvoller, nicht wertlos.
    assert is_set_offer(
        "LEGO Technic 42143 Ferrari Daytona SP3 mit LED Licht Beleuchtung",
        SET, price_eur=380.0, reference_price=400.0,
    ) is True


def test_listing_without_set_number_matches_via_set_name():
    # Review-Finding H1: Privatverkaeufer nennen den Namen, nicht die Nummer.
    assert is_set_offer(
        "LEGO Technic Ferrari Daytona SP3 Neu OVP", SET, price_eur=320.0,
        reference_price=400.0, set_name="Ferrari Daytona SP3",
    ) is True


def test_multi_set_lot_is_not_valued_as_one_set():
    assert is_set_offer("LEGO Konvolut 42143 + 42115 Sammlung", SET, price_eur=700.0) is False
