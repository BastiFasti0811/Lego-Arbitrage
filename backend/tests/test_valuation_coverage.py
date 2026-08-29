"""Grün soll wieder heißen, dass Werte entstehen.

Der Lauf vom 25.08.2026 übersprang 32 von 41 Sets und meldete `success`.
Der Watchdog misst Task-Erfolg, nicht Nutzarbeit — dieselbe Lücke, die
evaluate_data_freshness für die Preisdaten bereits schließt.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.heartbeat import evaluate_valuation_coverage

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _run(total, valued, skipped, failed=0):
    return SimpleNamespace(
        id=1, started_at=NOW, items_total=total, items_valued=valued,
        items_skipped=skipped, items_failed=failed,
    )


def test_no_run_yet_is_not_a_problem():
    assert evaluate_valuation_coverage(None, NOW) is None


def test_a_healthy_run_reports_nothing():
    assert evaluate_valuation_coverage(_run(41, 38, 3), NOW) is None


def test_the_production_run_is_flagged():
    health = evaluate_valuation_coverage(_run(41, 9, 32), NOW)
    assert health is not None
    assert health.status == "stale"
    assert "32 von 41" in health.detail


def test_an_empty_inventory_is_not_a_problem():
    assert evaluate_valuation_coverage(_run(0, 0, 0), NOW) is None


def test_a_tiny_inventory_is_not_flagged_even_at_full_skip():
    # Drei von drei sind eine Quote von 100 % — weit über der Schwelle. Nur
    # die Mindestgröße hält hier zurück, nicht der Anteil selbst: ohne
    # MIN_ITEMS_FOR_COVERAGE_CHECK würde dieselbe Eingabe eine Meldung auslösen.
    assert evaluate_valuation_coverage(_run(3, 0, 3), NOW) is None


def test_exactly_half_skipped_is_still_fine():
    # Andere Grenze als oben: hier liegt die Quote (nicht die Anzahl) genau
    # auf der Schwelle — zehn Sets sind längst über dem Minimum.
    assert evaluate_valuation_coverage(_run(10, 5, 5), NOW) is None
