"""Die Buchfuehrung des Bewertungslaufs.

Sie ist vom Task getrennt, damit sich pruefen laesst, dass jede Aussteige-
Stelle eine Zeile hinterlaesst — ohne einen einzigen Scraper zu starten.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.dml import Delete

from app.models.valuation_run import ValuationOutcome, ValuationRunItem, ValuationSkipReason
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
    delete_runs_older_than,
    describe_sources,
)


def test_a_probe_keeps_price_and_reason_apart():
    ok = SourceProbe(source="BRICKMERGE", price_eur=29.74)
    blocked = SourceProbe(source="EBAY_SOLD", error="403 Forbidden")
    assert ok.as_dict() == {"source": "BRICKMERGE", "price_eur": 29.74, "error": None, "note": None}
    assert blocked.as_dict()["price_eur"] is None
    assert blocked.as_dict()["error"] == "403 Forbidden"


def test_sources_are_described_for_a_human():
    text = describe_sources([
        SourceProbe(source="BRICKECONOMY", error="kein Treffer"),
        SourceProbe(source="EBAY_SOLD", error="403 Forbidden"),
        SourceProbe(source="BRICKMERGE", price_eur=29.74),
    ])
    assert text == "BRICKECONOMY: kein Treffer · EBAY_SOLD: 403 Forbidden · BRICKMERGE: 29,74 €"


def test_a_note_is_appended_to_the_price():
    text = describe_sources([
        SourceProbe(source="BRICKECONOMY", price_eur=51.20, note="Ersatzkurs — kein EZB-Kurs verfuegbar"),
    ])
    assert text == "BRICKECONOMY: 51,20 € (Ersatzkurs — kein EZB-Kurs verfuegbar)"


def test_a_note_is_appended_to_an_error_too():
    # Eine Quelle kann gleichzeitig scheitern UND einen Vermerk tragen (z.B.
    # eine 403-Antwort, waehrend der FX-Kurs schon veraltet war). Der Vermerk
    # darf auf dem Fehler-Zweig genauso wenig verloren gehen wie beim Preis.
    text = describe_sources([
        SourceProbe(
            source="EBAY_SOLD", error="403 Forbidden",
            note="Kurs veraltet — zuletzt am 2026-03-01 von der EZB bestaetigt",
        ),
    ])
    assert text == "EBAY_SOLD: 403 Forbidden (Kurs veraltet — zuletzt am 2026-03-01 von der EZB bestaetigt)"


def test_counts_match_the_recorded_rows():
    rec = ValuationRunRecorder()
    rec.record_valued(item_id=1, set_number="76430", consensus_price=56.98, probes=[])
    rec.record_skipped(
        item_id=2, set_number="40800",
        reason=ValuationSkipReason.SINGLE_SOURCE, probes=[],
    )
    rec.record_skipped(
        item_id=3, set_number="43230",
        reason=ValuationSkipReason.NO_PRICES, probes=[],
    )
    rec.record_failed(item_id=4, set_number="10280", detail="boom", probes=[])

    assert rec.counts() == {"total": 4, "valued": 1, "skipped": 2, "failed": 1}


def test_a_skip_always_carries_a_reason():
    rec = ValuationRunRecorder()
    rec.record_skipped(
        item_id=2, set_number="40800",
        reason=ValuationSkipReason.SINGLE_SOURCE,
        probes=[SourceProbe(source="BRICKMERGE", price_eur=14.34)],
    )
    row = rec.rows[0]
    assert row["outcome"] == ValuationOutcome.SKIPPED.value
    assert row["reason"] == "single_source"
    # Die Quellenlage ist die Diagnose — ohne sie ist die Zeile wertlos.
    assert row["sources"] == [
        {"source": "BRICKMERGE", "price_eur": 14.34, "error": None, "note": None}
    ]
    assert row["detail"] == "BRICKMERGE: 14,34 €"


def test_a_failure_records_its_message():
    rec = ValuationRunRecorder()
    rec.record_failed(item_id=9, set_number="75251", detail="TimeoutError", probes=[])
    row = rec.rows[0]
    assert row["outcome"] == ValuationOutcome.FAILED.value
    assert row["reason"] == ValuationSkipReason.EXCEPTION.value
    assert row["detail"] == "TimeoutError"


# ---------------------------------------------------------------------------
# flush() und delete_runs_older_than() schreiben tatsaechlich — dafuer reicht
# keine reine In-Memory-Pruefung mehr. Die Fakes hier folgen der Form aus
# test_inventory_market_snapshot.py: ein Objekt, das nur die paar Methoden
# nachbaut, die der Code tatsaechlich aufruft — keine echte Datenbank.
# ---------------------------------------------------------------------------


class _FakeFlushSession:
    """Nachbau von session.add()/session.get(), wie flush() sie braucht."""

    def __init__(self, run):
        self._run = run
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def get(self, _model, _id):
        return self._run


def _fake_run():
    # -1 als Startwert, damit ein Test, der vergisst zu pruefen, nicht durch
    # Zufall auf einem bereits richtigen 0 landet.
    return SimpleNamespace(items_total=-1, items_valued=-1, items_skipped=-1, items_failed=-1)


@pytest.mark.asyncio
async def test_flush_writes_rows_and_sets_the_run_counters_to_match_counts():
    rec = ValuationRunRecorder()
    rec.record_valued(item_id=1, set_number="76430", consensus_price=56.98, probes=[])
    rec.record_skipped(
        item_id=2, set_number="40800",
        reason=ValuationSkipReason.SINGLE_SOURCE, probes=[],
    )
    rec.record_failed(item_id=3, set_number="10280", detail="boom", probes=[])
    run = _fake_run()
    session = _FakeFlushSession(run)

    await rec.flush(session, run_id=42)

    assert len(session.added) == 3
    assert all(isinstance(item, ValuationRunItem) for item in session.added)
    assert all(item.run_id == 42 for item in session.added)
    # Nicht nur, dass Zeilen ankamen — die Zaehler auf dem Lauf muessen exakt
    # das sein, was counts() fuer denselben Recorder meldet. Ein flush(), das
    # von counts() abweicht, waere genau die unehrliche Meldung, die dieses
    # Feature beenden soll.
    counts = rec.counts()
    assert (run.items_total, run.items_valued, run.items_skipped, run.items_failed) == (
        counts["total"], counts["valued"], counts["skipped"], counts["failed"],
    )
    assert (run.items_total, run.items_valued, run.items_skipped, run.items_failed) == (3, 1, 1, 1)


@pytest.mark.asyncio
async def test_flush_with_no_rows_adds_nothing_and_zeroes_the_counters():
    rec = ValuationRunRecorder()
    run = _fake_run()
    session = _FakeFlushSession(run)

    await rec.flush(session, run_id=42)

    assert session.added == []
    assert (run.items_total, run.items_valued, run.items_skipped, run.items_failed) == (0, 0, 0, 0)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDeleteSession:
    """Nachbau von session.execute(), wie delete_runs_older_than() ihn braucht.

    Der erste execute()-Aufruf ist immer die SELECT-Abfrage der veralteten
    IDs; ein zweiter faellt nur an, wenn es ueberhaupt etwas zu loeschen gibt.
    `executed` haelt beide fest, damit ein Test pruefen kann, OB ueberhaupt
    ein DELETE ausgeloest wurde — nicht nur, was es zurueckgibt.
    """

    def __init__(self, stale_ids):
        self._stale_rows = [(id_,) for id_ in stale_ids]
        self.executed: list = []

    async def execute(self, statement):
        self.executed.append(statement)
        if len(self.executed) == 1:
            return _FakeResult(self._stale_rows)
        return _FakeResult([])


@pytest.mark.asyncio
async def test_delete_runs_older_than_deletes_nothing_when_none_are_stale():
    session = _FakeDeleteSession(stale_ids=[])

    deleted = await delete_runs_older_than(session, cutoff=datetime(2026, 1, 1))

    assert deleted == 0
    # Nur die SELECT-Abfrage lief — kein zweiter execute()-Aufruf, also kein
    # DELETE. Die Kaskade selbst (ON DELETE CASCADE) ist eine DB-Garantie und
    # wird bereits von test_migration_valuation_runs.py geprueft, nicht hier.
    assert len(session.executed) == 1


@pytest.mark.asyncio
async def test_delete_runs_older_than_deletes_the_stale_runs_and_returns_how_many():
    session = _FakeDeleteSession(stale_ids=[1, 2, 3])

    deleted = await delete_runs_older_than(session, cutoff=datetime(2026, 1, 1))

    assert deleted == 3
    assert len(session.executed) == 2
    delete_statement = session.executed[1]
    assert isinstance(delete_statement, Delete)
    # Nicht nur, DASS ein DELETE lief, sondern dass genau die drei IDs aus
    # der SELECT-Antwort darin landen — die eigentliche Verdrahtung, die
    # diese Funktion leistet. Ob started_at < cutoff auf der DB-Seite korrekt
    # ausgewertet wird, ist SQLAlchemy-/DB-Vergleichssemantik, kein Verhalten
    # dieser Funktion, und wird hier bewusst nicht nachgestellt.
    compiled = str(delete_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "IN (1, 2, 3)" in compiled
