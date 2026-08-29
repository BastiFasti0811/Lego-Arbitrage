"""Die Buchfuehrung des Bewertungslaufs.

Sie ist vom Task getrennt, damit sich pruefen laesst, dass jede Aussteige-
Stelle eine Zeile hinterlaesst — ohne einen einzigen Scraper zu starten.
"""

from app.models.valuation_run import ValuationOutcome, ValuationSkipReason
from app.services.valuation_log import (
    SourceProbe,
    ValuationRunRecorder,
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
