"""Jede Aussteige-Stelle des Bewertungslaufs muss eine Spur hinterlassen.

Der Lauf vom 25.08.2026 meldete `{'updated': 3, 'errors': 0}` bei 41
gehaltenen Sets. 38 Sets fielen durch `is_persistable_consensus` — jedes ueber
ein nacktes `continue`, das weder einen Zaehler erhoehte noch irgendwo ankam.
"""


from datetime import date
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.engine.market_consensus import calculate_consensus
from app.models.valuation_run import ValuationRunItem, ValuationRunStatus, ValuationSkipReason
from app.scrapers import BrickEconomyScraper, BrickMergeScraper, EbaySoldScraper
from app.scrapers.base import ScrapedPrice
from app.services.valuation_log import SourceProbe, ValuationRunRecorder
from app.tasks import update_inventory
from app.tasks.update_inventory import (
    _classify_consensus,
    _collect_prices,
    _resolve_skip_reason,
    _update_valuations_async,
)


def _price(source: str, value: float) -> ScrapedPrice:
    return ScrapedPrice(source=source, price_eur=value)


def test_a_single_source_is_a_named_skip():
    consensus = calculate_consensus([_price("BRICKMERGE", 14.34)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.SINGLE_SOURCE


def test_no_prices_at_all_is_its_own_reason():
    consensus = calculate_consensus([])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.NO_PRICES


def test_two_sources_far_apart_are_a_divergence_skip():
    # 30 % Streuung ist die Grenze in is_persistable_consensus.
    consensus = calculate_consensus([_price("BRICKMERGE", 20.0), _price("BRICKECONOMY", 60.0)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.DIVERGENCE


def test_two_agreeing_sources_are_valued():
    consensus = calculate_consensus([_price("BRICKMERGE", 55.0), _price("BRICKECONOMY", 58.0)])
    outcome, reason = _classify_consensus(consensus)
    assert outcome == "valued"
    assert reason is None


def test_the_recorder_sees_every_item_even_when_nothing_is_valued():
    rec = ValuationRunRecorder()
    for set_number in ("40793", "40800", "40795"):
        rec.record_skipped(
            item_id=None, set_number=set_number,
            reason=ValuationSkipReason.SINGLE_SOURCE,
            probes=[SourceProbe(source="BRICKMERGE", price_eur=9.99)],
        )
    counts = rec.counts()
    assert counts["total"] == 3
    assert counts["valued"] == 0
    assert counts["skipped"] == 3
    # Der alte Rueckgabewert haette hier errors: 0 gemeldet und sonst nichts.
    assert all(row["reason"] == "single_source" for row in rec.rows)


# ---------------------------------------------------------------------------
# NO_PRICES vs. IMPLAUSIBLE_PRICE: beide fuehren bei _classify_consensus zum
# gleichen num_sources == 0, sind aber verschiedene Handlungsanweisungen —
# "keine Quelle hat geantwortet" gegen "eine Quelle hat geantwortet, ihr
# Preis wurde verworfen". _classify_consensus sieht die Probes nicht und
# kann die Unterscheidung nicht treffen; _resolve_skip_reason zieht sie dort
# nach, wo die Probes vorliegen. Beide Faelle werden hier bis in die
# aufgezeichnete Zeile verfolgt, nicht nur bis zum Rueckgabewert.
# ---------------------------------------------------------------------------


def test_when_every_source_stays_silent_the_reason_is_no_prices():
    # Kanonische Quellennamen (PriceSource-Vokabular), nicht die Python-
    # Klassennamen der Scraper - siehe _SOURCE_NAME_BY_SCRAPER: dieselbe
    # Quelle darf im Log nicht unter zwei Namen auftauchen.
    probes = [
        SourceProbe(source="EBAY_SOLD", error="TimeoutError: 10s exceeded"),
        SourceProbe(source="BRICKECONOMY", error="kein Preis gefunden"),
        SourceProbe(source="BRICKMERGE", error="kein Preis gefunden"),
    ]
    consensus = calculate_consensus([])  # keine Probe lieferte einen Preis

    outcome, reason = _resolve_skip_reason(consensus, probes)

    rec = ValuationRunRecorder()
    rec.record_skipped(
        item_id=1, set_number="40800", reason=reason, probes=probes,
        consensus_price=consensus.consensus_price or None,
    )
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.NO_PRICES
    assert rec.rows[0]["reason"] == "no_prices"


def test_when_a_source_delivers_a_rejected_price_the_reason_is_implausible_price():
    probes = [
        # unplausibel gegen die UVP verworfen — price_eur UND error gesetzt,
        # das ist die Signatur aus dem Unplausibilitaets-Zweig in _collect_prices.
        SourceProbe(source="BRICKMERGE", price_eur=3.50, error="unplausibel gegen UVP 89.99"),
        SourceProbe(source="BRICKECONOMY", error="kein Preis gefunden"),
    ]
    consensus = calculate_consensus([])  # der verworfene Preis erreicht prices nie

    outcome, reason = _resolve_skip_reason(consensus, probes)

    rec = ValuationRunRecorder()
    rec.record_skipped(
        item_id=2, set_number="40795", reason=reason, probes=probes,
        consensus_price=consensus.consensus_price or None,
    )
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.IMPLAUSIBLE_PRICE
    assert rec.rows[0]["reason"] == "implausible_price"


def test_source_name_by_scraper_speaks_the_price_source_vocabulary():
    # Ohne diese Zuordnung faellt _collect_prices bei einer toten Quelle auf
    # scraper_cls.__name__ zurueck ("EbaySoldScraper") statt auf den Namen,
    # den ein erfolgreicher Abruf traegt ("EBAY_SOLD") - dieselbe Quelle
    # erschiene im Log unter zwei Namen, je nach Ausgang.
    assert update_inventory._SOURCE_NAME_BY_SCRAPER[EbaySoldScraper] == "EBAY_SOLD"
    assert update_inventory._SOURCE_NAME_BY_SCRAPER[BrickEconomyScraper] == "BRICKECONOMY"
    assert update_inventory._SOURCE_NAME_BY_SCRAPER[BrickMergeScraper] == "BRICKMERGE"


def test_an_outlier_dropped_price_is_named_implausible_not_no_prices():
    # is_plausible_price laesst 4,50 EUR durch (kein UVP hinterlegt, wie bei
    # 40 der 41 gehaltenen Sets), aber calculate_consensus' eigene 5-Euro-
    # Grenze wirft die Zahl danach still wieder raus - die Probe traegt
    # price_eur, aber nie error. Vor der Verbreiterung von _has_rejected_price
    # waere das hier NO_PRICES gewesen, waehrend die Zeile selbst
    # "BRICKMERGE: 4,50 EUR" zeigt - Grund und Beleg haetten sich widersprochen.
    probes = [SourceProbe(source="BRICKMERGE", price_eur=4.50)]
    consensus = calculate_consensus([_price("BRICKMERGE", 4.50)])
    assert consensus.num_sources == 0  # Testvoraussetzung: die Ausreisser-Grenze griff wirklich

    outcome, reason = _resolve_skip_reason(consensus, probes)

    rec = ValuationRunRecorder()
    rec.record_skipped(
        item_id=3, set_number="43230", reason=reason, probes=probes,
        consensus_price=consensus.consensus_price or None,
    )
    assert outcome == "skipped"
    assert reason is ValuationSkipReason.IMPLAUSIBLE_PRICE
    assert rec.rows[0]["reason"] == "implausible_price"


# ---------------------------------------------------------------------------
# Bis hier pruefen alle Tests nur _classify_consensus/_resolve_skip_reason
# gegen Zeilen, die der Test selbst per record_skipped erzeugt hat - das
# pinnt die Treue des Recorders, nicht die der Schleife. Ab hier wird
# _collect_prices und _update_valuations_async tatsaechlich angetrieben, mit
# Fake-Scrapern und einer Fake-Session (Vorbild: test_inventory_market_
# snapshot.py, test_valuation_log.py) - keine Datenbank.
# ---------------------------------------------------------------------------


class _FakeScraperBase:
    """Doppel fuer einen PRICE_SCRAPERS-Eintrag: async Context-Manager plus
    get_price(), ohne Konstruktor-Argumente wie eine echte Scraper-Klasse.
    Verhalten steckt in der Subklasse (siehe `_fake_scraper_cls`), damit
    `scraper_cls.__name__` fuer die Probes ohne gelieferten Preis noch
    etwas Sprechendes liefert.
    """

    _price = None
    _exc = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_price(self, set_number: str):
        if self._exc is not None:
            raise self._exc
        return self._price


def _fake_scraper_cls(name: str, *, price=None, exc=None):
    return type(name, (_FakeScraperBase,), {"_price": price, "_exc": exc})


@pytest.mark.asyncio
async def test_collect_prices_covers_all_four_outcomes_in_one_run(monkeypatch):
    # Treibt _collect_prices tatsaechlich an - bislang hatte die Funktion,
    # die die vier Aussteige-Stellen ueberhaupt erst erzeugt, keinen
    # einzigen Test. Faellt z.B. `note=price.notes` aus dem Unplausibilitaets-
    # Zweig wieder raus (Important A), schlaegt die letzte Assertion fehl.
    passed = ScrapedPrice(source="BRICKMERGE", price_eur=55.0)
    rejected = ScrapedPrice(
        source="BRICKECONOMY", price_eur=3.0,
        notes="Ersatzkurs - kein EZB-Kurs verfuegbar",
    )
    scrapers = [
        _fake_scraper_cls("FakePass", price=passed),
        _fake_scraper_cls("FakeNone", price=None),
        _fake_scraper_cls("FakeRaise", exc=TimeoutError("boom")),
        _fake_scraper_cls("FakeRejected", price=rejected),
    ]
    monkeypatch.setattr(update_inventory, "PRICE_SCRAPERS", scrapers)

    item = SimpleNamespace(set_number="76430")
    # UVP 50: 20 % davon sind 10 - der Preis von "FakeRejected" (3.0) faellt
    # darunter, der von "FakePass" (55.0) nicht.
    prices, probes = await _collect_prices(item, uvp=50.0)

    assert len(probes) == 4
    assert prices == [passed]  # nur die tatsaechlich nutzbare Zahl ueberlebt

    by_source = {p.source: p for p in probes}
    assert by_source["FakeNone"].error == "kein Preis gefunden"
    assert by_source["FakeNone"].price_eur is None
    assert "TimeoutError" in by_source["FakeRaise"].error
    assert "boom" in by_source["FakeRaise"].error

    rejected_probe = by_source["BRICKECONOMY"]
    assert rejected_probe.price_eur == 3.0
    assert rejected_probe.error is not None
    # Important A: der Vermerk (hier Stellvertreter fuer einen eingefrorenen
    # Wechselkurs) darf im Unplausibilitaets-Zweig nicht verloren gehen.
    assert rejected_probe.note == "Ersatzkurs - kein EZB-Kurs verfuegbar"

    passed_probe = by_source["BRICKMERGE"]
    assert passed_probe.price_eur == 55.0
    assert passed_probe.error is None


@pytest.mark.asyncio
async def test_collect_prices_looks_up_the_source_name_mapping_for_silent_scrapers(monkeypatch):
    # Prueft die Verdrahtung, nicht nur die Zuordnungstabelle selbst: faellt
    # _collect_prices bei einer stummen Quelle auf scraper_cls.__name__
    # zurueck, statt die Tabelle nachzuschlagen, zeigt die Probe den
    # Fake-Klassennamen statt des kanonischen Namens.
    silent = _fake_scraper_cls("FakeSilent", price=None)
    monkeypatch.setattr(update_inventory, "PRICE_SCRAPERS", [silent])
    monkeypatch.setattr(update_inventory, "_SOURCE_NAME_BY_SCRAPER", {silent: "TEST_SOURCE"})

    _, probes = await _collect_prices(SimpleNamespace(set_number="76430"), uvp=None)

    assert probes[0].source == "TEST_SOURCE"


class _FakeQueryResult:
    """Nachbau des SQLAlchemy-Ergebnisobjekts: .all() direkt (Set-Metadaten-
    und Stale-Run-Abfrage) oder ueber .scalars().all() (InventoryItem-
    Abfrage) - beide Aufrufformen landen hier.
    """

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeRunSession:
    """Faehrt _update_valuations_async ohne Datenbank. execute() liefert
    kanonische Ergebnisse in der Reihenfolge, in der die Funktion sie abruft:
    Set-Metadaten, Inventar-Items, veraltete Lauf-IDs (leer, damit
    delete_runs_older_than nach der SELECT-Abfrage aufhoert und kein DELETE
    ausloest). get()/add() bedienen recorder.flush() und den Laufabschluss
    aus einem einfachen id->Objekt-Speicher, wie _FakeFlushSession in
    test_valuation_log.py.
    """

    def __init__(self, *, set_rows, item_rows, run):
        self._results = [set_rows, item_rows, []]
        self._runs = {run.id: run}
        self.added: list = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _statement):
        return _FakeQueryResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def get(self, _model, id_):
        return self._runs.get(id_)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass


class _FakeFailureSession:
    """Die zweite, frische Session, die Important C fuer den failed-Schreib-
    zugriff oeffnet, unabhaengig von der Session, in der der Lauf scheiterte.
    """

    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, _model, id_):
        return self._run if id_ == self._run.id else None

    async def commit(self):
        pass


class _FakeBrickMerge:
    """Ersetzt den Peak-Check-Scraper: leere Historie, kein Netzwerk, kein Signal."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_price_history(self, set_number: str):
        return []


class _ScriptedScraperBase:
    """PRICE_SCRAPERS-Ersatz, dessen Antwort von der Set-Nummer abhaengt -
    damit ein einzelner Lauf mehrere Items mit unterschiedlicher Quellenlage
    durchspielen kann, wie ein echter Scraper es je nach Set auch taete.
    """

    script: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_price(self, set_number: str):
        outcome = self.script.get(set_number)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _scripted_scraper_cls(name: str, script: dict):
    return type(name, (_ScriptedScraperBase,), {"script": script})


@pytest.mark.asyncio
async def test_the_loop_writes_exactly_one_row_per_item_with_the_reason_the_inputs_imply(monkeypatch):
    # Der Test, der den urspruenglichen Bug gefangen haette: 41 gehaltene
    # Sets, 3 Zeilen. Faellt ein Item stumm durch ein wiederbelebtes nacktes
    # `continue` oder durch ein zurueckgedrehtes `:202`
    # (_resolve_skip_reason -> _classify_consensus), unterscheidet sich
    # len(session.added) von len(items), oder Item 5 landet faelschlich bei
    # no_prices statt implausible_price.
    scraper_one = _scripted_scraper_cls("ScraperOne", {
        "10280": ScrapedPrice(source="BRICKMERGE", price_eur=120.0),   # A: einer von zwei -> valued
        "40800": ScrapedPrice(source="BRICKMERGE", price_eur=14.34),   # B: einzige Quelle -> single_source
        "70620": ScrapedPrice(source="BRICKMERGE", price_eur=5.0),     # E: gegen UVP verworfen -> implausible_price
        "75192": ScrapedPrice(source="BRICKMERGE", price_eur=200.0),   # D: valued, faellt danach -> exception
    })
    scraper_two = _scripted_scraper_cls("ScraperTwo", {
        "10280": ScrapedPrice(source="BRICKECONOMY", price_eur=125.0),
        "75192": ScrapedPrice(source="BRICKECONOMY", price_eur=205.0),
        # 40800, 70620, 43230: kein Eintrag -> None (zweite Quelle schweigt)
        # 43230 fehlt komplett aus beiden Skripten -> no_prices
    })
    monkeypatch.setattr(update_inventory, "PRICE_SCRAPERS", [scraper_one, scraper_two])
    monkeypatch.setattr(update_inventory, "BrickMergeScraper", _FakeBrickMerge)

    items = [
        SimpleNamespace(id=1, set_number="10280", buy_price=100.0, buy_shipping=5.0, buy_date=date(2025, 1, 1)),
        SimpleNamespace(id=2, set_number="40800", buy_price=20.0, buy_shipping=0.0, buy_date=date(2025, 1, 1)),
        SimpleNamespace(id=3, set_number="43230", buy_price=15.0, buy_shipping=0.0, buy_date=date(2025, 1, 1)),
        SimpleNamespace(id=4, set_number="75192", buy_price=150.0, buy_shipping=0.0, buy_date=None),
        SimpleNamespace(id=5, set_number="70620", buy_price=60.0, buy_shipping=0.0, buy_date=date(2025, 1, 1)),
    ]
    set_rows = [SimpleNamespace(set_number="70620", release_year=2022, uvp_eur=90.0)]
    run = SimpleNamespace(
        id=99, items_total=-1, items_valued=-1, items_skipped=-1, items_failed=-1,
        finished_at=None, status=ValuationRunStatus.RUNNING.value,
    )
    session = _FakeRunSession(set_rows=set_rows, item_rows=items, run=run)
    monkeypatch.setattr(update_inventory, "async_session", lambda: session)

    result = await _update_valuations_async(run_id=99)

    assert len(session.added) == len(items) == 5
    assert all(isinstance(row, ValuationRunItem) for row in session.added)
    by_item = {row.item_id: row for row in session.added}
    assert by_item[1].outcome == "valued"
    assert by_item[1].reason is None
    assert by_item[2].outcome == "skipped"
    assert by_item[2].reason == "single_source"
    assert by_item[3].outcome == "skipped"
    assert by_item[3].reason == "no_prices"
    assert by_item[4].outcome == "failed"
    assert by_item[4].reason == "exception"
    # Minor 2: der Fehler traf NACH _collect_prices - die zwei schon
    # gesammelten Probes muessen record_failed erreichen, nicht probes=[].
    assert len(by_item[4].sources) == 2
    assert by_item[5].outcome == "skipped"
    assert by_item[5].reason == "implausible_price"

    assert result == {"run_id": 99, "total": 5, "valued": 1, "skipped": 3, "failed": 1}
    assert run.status == ValuationRunStatus.SUCCESS.value
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_a_crashed_run_is_marked_failed_not_left_running(monkeypatch):
    # Important C: ohne den aeusseren Handler bliebe die Zeile fuer immer
    # "running" - die Session, in der die Abfrage scheiterte, ist danach
    # nicht mehr vertrauenswuerdig, und ohne eine frische Session schreibt
    # niemand mehr etwas auf den Lauf.
    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def execute(self, _statement):
            raise RuntimeError("Datenbank nicht erreichbar")

    run = SimpleNamespace(id=99, status=ValuationRunStatus.RUNNING.value, finished_at=None, error=None)
    sessions = iter([_BoomSession(), _FakeFailureSession(run)])
    monkeypatch.setattr(update_inventory, "async_session", lambda: next(sessions))

    with pytest.raises(RuntimeError, match="Datenbank nicht erreichbar"):
        await _update_valuations_async(run_id=99)

    assert run.status == ValuationRunStatus.FAILED.value
    assert run.finished_at is not None
    assert "Datenbank nicht erreichbar" in run.error


@pytest.mark.asyncio
async def test_soft_time_limit_is_not_swallowed_and_marks_the_run_failed(monkeypatch):
    # soft_time_limit loest SoftTimeLimitExceeded aus, eine Exception-
    # Unterklasse - ohne die explizite Weiterreichung in _collect_prices UND
    # im Item-Handler wuerde sie als "failed"-Zeile verbucht und der Lauf
    # liefe normal weiter, statt sich zu beenden. Dieser Test schlaegt fehl,
    # wenn IRGENDEINE der beiden Stellen das Signal schluckt, oder wenn der
    # aeussere Handler (Important C) den Lauf danach nicht als failed markiert.
    boom = _fake_scraper_cls("BoomScraper", exc=SoftTimeLimitExceeded())
    monkeypatch.setattr(update_inventory, "PRICE_SCRAPERS", [boom])
    monkeypatch.setattr(update_inventory, "BrickMergeScraper", _FakeBrickMerge)

    item = SimpleNamespace(id=1, set_number="10280", buy_price=10.0, buy_shipping=0.0, buy_date=date(2025, 1, 1))
    run = SimpleNamespace(id=99, status=ValuationRunStatus.RUNNING.value, finished_at=None, error=None)
    main_session = _FakeRunSession(set_rows=[], item_rows=[item], run=run)
    sessions = iter([main_session, _FakeFailureSession(run)])
    monkeypatch.setattr(update_inventory, "async_session", lambda: next(sessions))

    with pytest.raises(SoftTimeLimitExceeded):
        await _update_valuations_async(run_id=99)

    assert main_session.added == []  # kein teilweise geschriebener Kram
    assert run.status == ValuationRunStatus.FAILED.value
