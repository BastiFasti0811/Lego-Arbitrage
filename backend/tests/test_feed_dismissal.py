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

    def test_the_same_url_on_another_platform_stays(self):
        # Die Identität enthält die Plattform. Eine Abwahl auf AMAZON darf ein
        # gleichnamiges eBay-Inserat nicht mitnehmen.
        rows = _rows(_offer(CANONICAL))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("EBAY", CANONICAL)})

        assert len(response.deals) == 1

    def test_other_offers_of_the_same_set_stay(self):
        rows = _rows(_offer(CANONICAL, roi=90.0), _offer(OTHER, roi=20.0))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert len(response.deals) == 1
        assert response.deals[0].set_number == SET_NUMBER

    def test_an_empty_dismissal_set_changes_nothing(self):
        rows = _rows(_offer(CANONICAL), _offer(OTHER))

        response = scout.build_feed(rows, _request(), dismissed=set())

        assert len(response.deals) == 2

    def test_a_dismissed_listing_does_not_consume_a_feed_slot(self):
        # Sonst wäre das Ausblenden wirkungslos: der Deckel griffe vor dem
        # Filter, und die abgewählte Karte hielte ihren Platz einfach leer.
        offers = [_offer(f"https://www.amazon.de/dp/B09QFSCW{i:02d}", roi=100.0 - i) for i in range(21)]
        top = offers[0]

        response = scout.build_feed(
            _rows(*offers), _request(), dismissed={offer_identity("AMAZON", top.offer_url)}
        )

        assert len(response.deals) == 20
        assert top.offer_url not in {deal.offer_url for deal in response.deals}

    def test_dismissed_listings_still_count_as_scanned(self):
        # "gescannt" beschreibt, was geprüft wurde. Eine Abwahl ändert die
        # Fundlage nicht, nur die Anzeige.
        rows = _rows(_offer(CANONICAL), _offer(OTHER))

        response = scout.build_feed(rows, _request(), dismissed={offer_identity("AMAZON", CANONICAL)})

        assert response.total_scanned == 2


class TestScanTimestampReachesTheFeed:
    def test_last_scan_at_is_passed_through(self):
        response = scout.build_feed(_rows(_offer(CANONICAL)), _request(), last_scan_at=NOW)

        assert response.last_scan_at == NOW

    def test_without_a_scan_it_stays_empty(self):
        response = scout.build_feed(_rows(_offer(CANONICAL)), _request())

        assert response.last_scan_at is None


class TestDismissalRecord:
    def test_the_stored_identity_is_canonical(self):
        # Abgewählt wird von einer Karte aus, deren URL den Token des Laufs
        # trägt, der sie gefunden hat. Gespeichert werden muss die Identität.
        payload = scout.DismissRequest(platform="AMAZON", offer_url=WITH_TOKEN, offer_title=TITLE, price_eur=289.0)

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_identity"] == offer_identity("AMAZON", CANONICAL)

    def test_the_card_is_copied_for_the_dismissed_list(self):
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

    def test_the_linkable_url_is_kept(self):
        # Zum Wiederfinden im Browser taugt die kanonische URL, nicht der Token.
        payload = scout.DismissRequest(platform="AMAZON", offer_url=WITH_TOKEN, offer_title=TITLE, price_eur=289.0)

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_url"] == CANONICAL


class TestDismissalIsIdempotent:
    """Zweimal abwählen darf kein Fehler sein.

    Der Upsert ist Postgres-spezifisch und läuft in keinem Test gegen eine
    echte DB — hier wird wenigstens festgehalten, dass er auf dem Unique-Index
    aufsetzt, den die Migration anlegt. Ohne `ON CONFLICT` liefe der zweite
    Klick in einen IntegrityError und damit in einen 500er.
    """

    def test_the_upsert_absorbs_the_second_click(self):
        from sqlalchemy.dialects import postgresql

        payload = scout.DismissRequest(platform="AMAZON", offer_url=CANONICAL, offer_title=TITLE, price_eur=289.0)

        # Die Anweisung, die die Route absetzt — nicht eine im Test gebaute
        # Kopie. Sonst bliebe der Test gruen, waehrend die Route laengst auf
        # ein nacktes INSERT umgestellt waere und der zweite Klick 500 gaebe.
        sql = str(scout.dismissal_statement(payload, NOW).compile(dialect=postgresql.dialect()))

        assert "ON CONFLICT (offer_identity) DO NOTHING" in sql


class TestFeedWiring:
    """Die drei Abfragen muessen an den richtigen Parametern landen.

    build_feed ist rein und gut abgedeckt, aber die Verdrahtung darum war es
    nicht: `last_scan_at=heartbeats` statt `latest_scan_success(heartbeats)`
    haette die ganze Suite bestanden und waere erst auf Prod als 500
    aufgeschlagen. Genauso ein verlorenes `.order_by(last_seen_at.desc())`,
    von dem die "frischeste Zeile gewinnt"-Regel stillschweigend abhaengt.
    """

    class _DispatchingSession:
        """Antwortet je Abfrage verschieden — anhand dessen, was sie selektiert."""

        def __init__(self, rows, dismissed_identities, heartbeats):
            self.rows = rows
            self.dismissed_identities = dismissed_identities
            self.heartbeats = heartbeats
            self.queries = []

        async def execute(self, query):
            self.queries.append(query)
            selected = query.column_descriptions[0]["name"]
            if selected == "offer_identity":
                return _Result(self.dismissed_identities)
            if selected == "TaskHeartbeat":
                return _Result(self.heartbeats)
            return _Result(self.rows)

    async def test_dismissals_and_scan_time_reach_the_feed(self):
        from app.models.heartbeat import TaskHeartbeat

        scan = TaskHeartbeat(task_name="app.tasks.scrape_daily.scrape_all_watched_sets")
        scan.last_success_at = NOW
        session = self._DispatchingSession(
            rows=_rows(_offer(CANONICAL), _offer(OTHER)),
            dismissed_identities=[offer_identity("AMAZON", CANONICAL)],
            heartbeats=[scan],
        )

        response = await scout._cached_scout_deals(_request(), session)

        assert [deal.offer_url for deal in response.deals] == [OTHER], "die Abwahl kam nicht an"
        assert response.last_scan_at == NOW, "der Scan-Zeitpunkt kam nicht an"

    async def test_offers_are_loaded_newest_seen_first(self):
        # Die Dedupe-Regel "frischeste Zeile gewinnt" haengt an dieser
        # Sortierung — ohne sie entscheidet die Heap-Reihenfolge.
        session = self._DispatchingSession(_rows(_offer(CANONICAL)), [], [])

        await scout._cached_scout_deals(_request(), session)

        offer_query = str(session.queries[0])
        assert "ORDER BY offers.last_seen_at DESC" in offer_query

    async def test_only_active_and_analysed_offers_are_loaded(self):
        session = self._DispatchingSession(_rows(_offer(CANONICAL)), [], [])

        await scout._cached_scout_deals(_request(), session)

        offer_query = str(session.queries[0])
        assert "offers.status =" in offer_query
        assert "offers.recommendation IS NOT NULL" in offer_query


class _Result:
    """Minimales Ergebnisobjekt fuer die Fake-Session."""

    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return self._items

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class TestOfferFreshnessIsReportedSeparately:
    """Wann zuletzt ein Angebot bestaetigt wurde — unabhaengig vom Heartbeat.

    Der Heartbeat sagt nur, dass der Celery-Task zurueckgekehrt ist. Beide
    Scrape-Tasks fangen einen 403 ab, setzen `aborted: True` und kehren normal
    zurueck — Celery feuert `task_success`, `last_success_at` wandert nach
    vorn. Kleinanzeigen kann den Scraper zwei Tage sperren, und der Header
    meldete trotzdem "vor 8 Min.". Diese Zahl kann das nicht: sie kommt aus
    den Angeboten selbst.
    """

    def test_the_newest_confirmed_offer_wins(self):
        older = _offer(OTHER)
        older.last_seen_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

        response = scout.build_feed(_rows(_offer(CANONICAL), older), _request())

        assert response.last_offer_seen_at == NOW

    def test_dismissed_offers_still_count_towards_freshness(self):
        # Sie sind ja gescannt worden. Sonst spraenge die Frischeanzeige
        # zurueck, nur weil man eine Karte weggeklickt hat.
        fresh = _offer(CANONICAL)
        older = _offer(OTHER)
        older.last_seen_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

        response = scout.build_feed(
            _rows(fresh, older), _request(), dismissed={offer_identity("AMAZON", CANONICAL)}
        )

        assert response.last_offer_seen_at == NOW

    def test_without_offers_it_stays_empty(self):
        response = scout.build_feed([], _request())

        assert response.last_offer_seen_at is None


class TestDismissalStaysWithinTheColumns:
    """Was nicht in die Spalte passt, darf keinen 500er ausloesen.

    `canonical_offer_url` gibt unparsbare URLs unveraendert zurueck und
    behaelt bei unbekannten Hosts den vollen Pfad. Gemessen: 725 Zeichen bei
    einem relativen Link mit langem Pfad — die Spalte fasst 600. Ungekuerzt
    wirft asyncpg StringDataRightTruncation, und das Inserat liesse sich nie
    abwaehlen.
    """

    def test_an_overlong_identity_is_clipped(self):
        payload = scout.DismissRequest(
            platform="KLEINANZEIGEN", offer_url="/s-anzeige/" + "x" * 700, offer_title=TITLE
        )

        values = scout.dismissal_values(payload, NOW)

        assert len(values["offer_identity"]) <= 600

    def test_the_stored_url_is_not_clipped(self):
        # offer_url ist Text, unbegrenzt. Gekuerzt waere der Link kaputt, und
        # genau zum Wiederfinden im Browser steht er da.
        long_url = "https://www.kleinanzeigen.de/s-anzeige/" + "x" * 700
        payload = scout.DismissRequest(platform="KLEINANZEIGEN", offer_url=long_url)

        values = scout.dismissal_values(payload, NOW)

        assert values["offer_url"] == long_url

    def test_a_clipped_dismissal_still_filters_the_feed(self):
        # Der eigentliche Punkt: gekuerzt gespeichert, ungekuerzt gerechnet —
        # dann stuende die Abwahl in der Liste und die Karte weiter im Feed,
        # ohne dass irgendwas den Widerspruch erklaert.
        long_url = "https://www.kleinanzeigen.de/s-anzeige/" + "x" * 700
        payload = scout.DismissRequest(platform="KLEINANZEIGEN", offer_url=long_url)
        stored = scout.dismissal_values(payload, NOW)["offer_identity"]

        offer = _offer(long_url)
        offer.platform = "KLEINANZEIGEN"

        response = scout.build_feed(_rows(offer), _request(), dismissed={stored})

        assert response.deals == []

    def test_an_overlong_title_is_clipped(self):
        payload = scout.DismissRequest(platform="AMAZON", offer_url=CANONICAL, offer_title="L" * 900)

        values = scout.dismissal_values(payload, NOW)

        assert len(values["offer_title"]) <= 500

    def test_clipping_is_deterministic(self):
        # Zweimal dieselbe lange URL muss dieselbe Identitaet ergeben, sonst
        # greift die Abwahl beim naechsten Lauf nicht mehr.
        long_url = "/s-anzeige/" + "x" * 700
        payload = scout.DismissRequest(platform="KLEINANZEIGEN", offer_url=long_url)

        first = scout.dismissal_values(payload, NOW)
        second = scout.dismissal_values(payload, NOW)

        assert first["offer_identity"] == second["offer_identity"]

    def test_an_unknown_platform_is_rejected(self):
        # Sonst speichert ein Tippfehler eine Abwahl, die nie greift: sie
        # steht in der Liste der Ausgeblendeten UND im Feed.
        import pytest

        with pytest.raises(ValueError):
            scout.DismissRequest(platform="AMAZONE", offer_url=CANONICAL)
