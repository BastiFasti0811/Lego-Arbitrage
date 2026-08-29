"""Jede Aussteige-Stelle des Bewertungslaufs muss eine Spur hinterlassen.

Der Lauf vom 25.08.2026 meldete `{'updated': 3, 'errors': 0}` bei 41
gehaltenen Sets. 38 Sets fielen durch `is_persistable_consensus` — jedes ueber
ein nacktes `continue`, das weder einen Zaehler erhoehte noch irgendwo ankam.
"""


from app.engine.market_consensus import calculate_consensus
from app.models.valuation_run import ValuationSkipReason
from app.scrapers.base import ScrapedPrice
from app.services.valuation_log import SourceProbe, ValuationRunRecorder
from app.tasks.update_inventory import _classify_consensus, _resolve_skip_reason


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
    probes = [
        SourceProbe(source="EbaySoldScraper", error="TimeoutError: 10s exceeded"),
        SourceProbe(source="BrickEconomyScraper", error="kein Preis gefunden"),
        SourceProbe(source="BrickMergeScraper", error="kein Preis gefunden"),
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
        SourceProbe(source="BrickEconomyScraper", error="kein Preis gefunden"),
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
