"""Ein abgewähltes Inserat bleibt abgewählt — auch nach dem nächsten Scan.

Der Feed sortiert nach `opportunity_score` und kappt bei 20. Ein Fund mit
+121 % ROI stand damit dauerhaft auf Platz 1, Lauf für Lauf, ohne dass man ihn
loswerden konnte. Die Abwahl hängt deshalb nicht an der Offer-Zeile — die
Dedupe-Bereinigung löscht Zeilen und der nächste Scrape legt sie neu an —,
sondern an der Identität des Inserats aus `offer_identity()`.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.api.routes import scout
from app.domain.offer_url import offer_identity

SET_NUMBER = "42143"
TITLE = "LEGO Technic 42143 Ferrari Daytona SP3 Modellauto"

CANONICAL = "https://www.amazon.de/dp/B09QFSCWD9"
# Dieselbe Anzeige, wie der nächste Scrape-Lauf sie zurückgibt.
WITH_TOKEN = "https://www.amazon.de/42143-Technic/dp/B09QFSCWD9/ref=sr_1_3?dib=eyJ2IjoiMSJ9.ZZZ&qid=1755647777"
OTHER = "https://www.amazon.de/dp/B09XVMSWJC"

NOW = datetime(2026, 8, 25, 4, 12, tzinfo=UTC)


def _lego_set():
    return SimpleNamespace(
        id=1,
        set_number=SET_NUMBER,
        set_name="Ferrari Daytona SP3",
        theme="Technic",
        current_market_price=376.99,
        uvp_eur=399.99,
    )


def _offer(url, *, roi=30.0, price=289.0):
    return SimpleNamespace(
        platform="AMAZON",
        offer_url=url,
        offer_title=TITLE,
        price_eur=price,
        shipping_eur=0.0,
        estimated_roi=roi,
        risk_score=4,
        recommendation="GO",
        analysis_notes="ok",
        condition="NEW_SEALED",
        box_damage=False,
        seller_location=None,
        last_seen_at=NOW,
    )


def _rows(*offers):
    lego_set = _lego_set()
    return [(offer, lego_set) for offer in offers]


def _request(**overrides):
    params = {"set_numbers": [SET_NUMBER], "min_roi": 0, "cached_only": True}
    params.update(overrides)
    return scout.ScoutRequest(**params)


class TestDismissedOffersLeaveTheFeed:
    def test_a_dismissed_listing_is_gone(self):
        rows = _rows(_offer(CANONICAL), _offer(OTHER))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert [deal.offer_url for deal in response.deals] == [OTHER]

    def test_it_stays_gone_when_the_next_scan_appends_a_tracking_token(self):
        # Der Grund, warum die Abwahl auf der kanonischen Identität sitzt:
        # Amazon liefert bei jedem Lauf einen frischen dib/qid-Token, die rohe
        # URL taugt nicht als Schlüssel.
        rows = _rows(_offer(WITH_TOKEN))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert response.deals == []

    def test_dieselbe_url_auf_einer_anderen_plattform_bleibt(self):
        # Die Identität enthält die Plattform. Eine Abwahl auf AMAZON darf ein
        # gleichnamiges eBay-Inserat nicht mitnehmen.
        rows = _rows(_offer(CANONICAL))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("EBAY", CANONICAL)})

        assert len(response.deals) == 1

    def test_andere_angebote_desselben_sets_bleiben(self):
        rows = _rows(_offer(CANONICAL, roi=90.0), _offer(OTHER, roi=20.0))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert len(response.deals) == 1
        assert response.deals[0].set_number == SET_NUMBER

    def test_ohne_abwahl_aendert_sich_nichts(self):
        rows = _rows(_offer(CANONICAL), _offer(OTHER))

        response = scout.build_feed(rows, _request(), dismissed=set())

        assert len(response.deals) == 2

    def test_ein_abgewaehltes_inserat_belegt_keinen_der_20_plaetze(self):
        # Sonst wäre das Ausblenden wirkungslos: der Deckel griffe vor dem
        # Filter, und die abgewählte Karte hielte ihren Platz einfach leer.
        offers = [_offer(f"https://www.amazon.de/dp/B09QFSCW{i:02d}", roi=100.0 - i) for i in range(21)]
        top = offers[0]

        response = scout.build_feed(
            _rows(*offers), _request(), dismissed={offer_identity("AMAZON", top.offer_url)}
        )

        assert len(response.deals) == 20
        assert top.offer_url not in {deal.offer_url for deal in response.deals}

    def test_abgewaehlte_zaehlen_weiter_als_gescannt(self):
        # "gescannt" beschreibt, was geprüft wurde. Eine Abwahl ändert die
        # Fundlage nicht, nur die Anzeige.
        rows = _rows(_offer(CANONICAL), _offer(OTHER))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert response.total_scanned == 2


class TestScanTimestampReachesTheFeed:
    def test_last_scan_at_wird_durchgereicht(self):
        response = scout.build_feed(_rows(_offer(CANONICAL)), _request(), last_scan_at=NOW)

        assert response.last_scan_at == NOW

    def test_ohne_scan_bleibt_es_leer(self):
        response = scout.build_feed(_rows(_offer(CANONICAL)), _request())

        assert response.last_scan_at is None


class TestDismissalRecord:
    def test_die_gespeicherte_identitaet_ist_kanonisch(self):
        # Abgewählt wird von einer Karte aus, deren URL den Token des Laufs
        # trägt, der sie gefunden hat. Gespeichert werden muss die Identität.
        payload = scout.DismissRequest(platform="AMAZON", offer_url=WITH_TOKEN, offer_title=TITLE, price_eur=289.0)

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_identity"] == offer_identity("AMAZON", CANONICAL)

    def test_die_karte_wird_fuer_die_liste_mitgeschrieben(self):
        # Die Offer-Zeile kann verschwinden. Ohne diese Kopien stünde in der
        # Liste der Ausgeblendeten nur noch eine URL.
        payload = scout.DismissRequest(
            platform="AMAZON",
            offer_url=WITH_TOKEN,
            offer_title=TITLE,
            price_eur=289.0,
            set_number=SET_NUMBER,
        )

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_title"] == TITLE
        assert values["price_eur"] == 289.0
        assert values["set_number"] == SET_NUMBER
        assert values["dismissed_at"] == NOW

    def test_die_angezeigte_url_bleibt_erhalten(self):
        # Zum Wiederfinden im Browser taugt die kanonische URL, nicht der Token.
        payload = scout.DismissRequest(platform="AMAZON", offer_url=WITH_TOKEN, offer_title=TITLE, price_eur=289.0)

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_url"] == CANONICAL
