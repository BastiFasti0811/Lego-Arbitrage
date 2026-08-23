import pytest

from app.domain.identity import is_set_offer, mentions_other_set_numbers

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


# ── Feldbefunde 2026-08-21: drei Angebote, die als Deal im Feed standen ──


class TestPartOfSetIsNotTheSet:
    """Ein Teil aus dem Set traegt dessen Nummer, ist aber nicht das Set."""

    def test_single_minifigure_from_the_set_is_rejected(self):
        # Amazon, 21,81 EUR gegen 169,99 EUR Marktpreis: der Anbieter verkauft
        # nur Grogu aus 75292, mit der Setnummer im Titel.
        assert is_set_offer(
            "LEGO Star Wars The Mandalorian Minifigure - The Child (Grogu) Baby Yoda 75292",
            "75292",
            price_eur=21.81,
            reference_price=169.99,
            set_name="The Mandalorian Transporter des Kopfgeldjaegers",
        ) is False

    def test_german_minifigure_wording_is_rejected(self):
        assert is_set_offer(
            "Lego 75292 Minifigur Kopfgeldjaeger einzeln", "75292", price_eur=15.0, reference_price=169.99
        ) is False

    def test_set_advertising_its_minifigures_stays_a_set(self):
        # Der Marker darf ein echtes Set nicht ausschliessen, nur weil es mit
        # seinen Minifiguren wirbt — der Preis entscheidet.
        assert is_set_offer(
            "LEGO Star Wars 75292 Transporter des Kopfgeldjaegers mit 4 Minifiguren, OVP",
            "75292",
            price_eur=155.0,
            reference_price=169.99,
        ) is True


class TestCustomPartsAreNotTheSet:
    """MOC-Teile werden fuer ein Set verkauft, sind aber nicht von LEGO."""

    def test_moc_extension_floors_are_rejected(self):
        # Kleinanzeigen, 89 EUR gegen 270,99 EUR: Zusatzetagen fuer vier Sets.
        assert is_set_offer(
            "LEGO MOC Zusatzetagen für 10326, 10350, 10297, 76294 NEU&OVP",
            "10326",
            price_eur=89.0,
            reference_price=270.99,
            set_name="Natural History Museum",
        ) is False

    def test_accessory_advertised_for_a_set_number_is_rejected(self):
        assert is_set_offer(
            "Beleuchtung für 10326 Naturkundemuseum", "10326", price_eur=39.0, reference_price=270.99
        ) is False


class TestSeveralSetNumbersMakeThePriceAmbiguous:
    """Nennt ein Titel mehrere Sets, gehoert der Preis nicht einem davon."""

    def test_listing_naming_four_sets_is_rejected(self):
        assert is_set_offer(
            "Verkaufe LEGO 10326, 10350, 10297 und 76294 neuwertig",
            "10326",
            price_eur=89.0,
            reference_price=270.99,
        ) is False

    def test_release_year_is_not_mistaken_for_a_set_number(self):
        assert is_set_offer(
            "LEGO Icons 10326 Naturkundemuseum von 2023, neu und versiegelt",
            "10326",
            price_eur=240.0,
            reference_price=270.99,
        ) is True

    def test_piece_count_is_not_mistaken_for_a_set_number(self):
        assert is_set_offer(
            "LEGO 10326 Naturkundemuseum, 4014 Teile, komplett", "10326", price_eur=240.0, reference_price=270.99
        ) is True


class TestPostcodesAreNotSetNumbers:
    """Fuenfstellige Zahlen im Titel sind auf Kleinanzeigen meist die PLZ.

    Solche Angebote wurden nicht nur aus dem Feed gehalten, sondern in
    _upsert_offers gar nicht erst gespeichert.
    """

    def test_a_pickup_postcode_does_not_disqualify(self):
        for title in (
            "Lego Eisvogel 10331 Abholung 40233 Duesseldorf",
            "LEGO 10331 Eisvogel - Selbstabholung 22307 Hamburg",
            "LEGO 10331 Eisvogel, Versand 5 EUR oder Abholung 80331",
        ):
            assert mentions_other_set_numbers(title, "10331") is False, title

    def test_a_phone_number_does_not_disqualify(self):
        assert mentions_other_set_numbers("LEGO 10331 Eisvogel, Tel 0176 12345678", "10331") is False

    def test_a_negotiable_price_is_a_price_not_a_set(self):
        assert mentions_other_set_numbers("LEGO 75192 Millennium Falcon fuer 1000 VB", "75192") is False

    def test_a_real_bundle_is_still_caught(self):
        assert mentions_other_set_numbers("LEGO Konvolut 10326, 10350, 10297, 76294", "10326") is True
        assert mentions_other_set_numbers("LEGO 10331 und 10326 zusammen", "10331") is True


class TestPriceInTheTitleIsNotASetNumber:
    """Vierstellige Preise treffen die Klasse, in der Arbitrage lohnt.

    Das abschliessende \\b galt fuer die ganze Alternation und verlangte damit
    auch hinter "€" ein Wortzeichen — "1500 € VB" galt als zweite Setnummer
    und das Angebot wurde gar nicht erst gespeichert.
    """

    def test_a_euro_sign_is_a_unit(self):
        for title in (
            "LEGO 10326 Rathaus, 1500 € VB",
            "LEGO 10326 Rathaus, 1500€ VB",
            "LEGO 10326 Rathaus, 1500 EUR VB",
        ):
            assert mentions_other_set_numbers(title, "10326") is False, title

    def test_a_price_keyword_in_front_of_the_number(self):
        assert mentions_other_set_numbers("LEGO 10326 Rathaus, Festpreis 1500", "10326") is False

    def test_a_real_second_set_is_still_caught(self):
        assert mentions_other_set_numbers("LEGO 10326 Rathaus und 75192 Falcon", "10326") is True


class TestGermanPriceNotation:
    """"1500,- VB" ist die Normalschreibweise; jedes Trennzeichen zwischen
    Ziffern und Einheit brach den Match, und das Angebot wurde nie gespeichert."""

    def test_decimal_and_dash_notations(self):
        for suffix in ("1500,- VB", "1500.-", "1500,00 €", "1500,00", "1.500 €"):
            title = f"LEGO Icons 10326 Notre Dame {suffix}"
            assert mentions_other_set_numbers(title, "10326") is False, suffix

    def test_price_words_without_a_currency(self):
        for suffix in ("1500 verhandelbar", "1500 Fixpreis", "1500 VB"):
            title = f"LEGO Icons 10326 Notre Dame {suffix}"
            assert mentions_other_set_numbers(title, "10326") is False, suffix

    def test_a_price_after_fuer_is_not_an_accessory_reference(self):
        # "fuer 1200 VB" ist ein Preis; das Zubehoer-Muster verwarf es hart.
        for suffix in ("für 1200 VB", "für 1200,-", "für 1400 Festpreis", "für 1200,00 €"):
            title = f"LEGO Icons 10326 Notre Dame {suffix}"
            assert is_set_offer(title, "10326", price_eur=1200.0, reference_price=1300.0) is True, suffix

    def test_real_accessories_are_still_rejected(self):
        for title in ("Wandhalterung für Lego 10326", "LocoLee Led Licht Set für Lego 10326"):
            assert is_set_offer(title, "10326", price_eur=25.0, reference_price=800.0) is False, title


class TestSetNumbersWithoutASpaceBetweenThem:
    def test_a_comma_list_is_not_a_decimal_price(self):
        # Ohne (?!\\d) frisst die Dezimalnotation die ersten zwei Ziffern der
        # zweiten Setnummer — und das Konvolut wird zum Einzelset-Schnaeppchen.
        for title in ("LEGO 10326,10350 zu verkaufen", "LEGO 10326.10350"):
            assert mentions_other_set_numbers(title, "10350") is True, title

    def test_real_decimal_prices_are_still_prices(self):
        for suffix in ("1500,00", "1500,- VB", "1.500 €"):
            title = f"LEGO Icons 10326 Notre Dame {suffix}"
            assert mentions_other_set_numbers(title, "10326") is False, suffix
