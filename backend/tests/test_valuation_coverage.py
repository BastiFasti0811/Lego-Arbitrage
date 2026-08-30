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


def test_exactly_five_items_all_skipped_is_flagged():
    # Fünf ist die Grenze selbst, nicht "eine darunter" — der Vergleich muss
    # < sein, nicht <=. Mit <= würde items_total < MIN_ITEMS_FOR_COVERAGE_CHECK
    # bei total=5 falsch (der Lauf gälte als "unter dem Minimum"), und dieser
    # Lauf mit Anteil 1,0 bliebe ungemeldet, obwohl kein einziges Set einen
    # Marktwert bekam.
    health = evaluate_valuation_coverage(_run(5, 0, 5), NOW)
    assert health is not None
    assert health.status == "stale"


def test_an_all_failed_run_is_flagged():
    # items_skipped bleibt 0, items_failed trägt die ganze Last. Vor diesem
    # Fix sah share = items_skipped / items_total nur Übersprünge: ein Lauf,
    # in dem jede Bewertung mit einer Exception abbricht, hätte Anteil 0,0
    # gemeldet und wäre grün geblieben, obwohl kein Set einen Marktwert bekam.
    health = evaluate_valuation_coverage(_run(41, 0, 0, failed=41), NOW)
    assert health is not None
    assert health.status == "stale"
    assert "41 von 41" in health.detail
    assert "0 übersprungen" in health.detail
    assert "41 fehlgeschlagen" in health.detail


def test_skipped_and_failed_together_cross_the_line():
    # Für sich je unter der Schwelle (3/10 = 0,3 übersprungen, 3/10 = 0,3
    # fehlgeschlagen), zusammen darüber (6/10 = 0,6): der Anteil muss beide
    # Ausstiege addieren, nicht nur den höheren der beiden isoliert prüfen.
    # Die beiden Zahlen bleiben im Detailtext unterscheidbar, damit sich
    # Übersprung und Fehlschlag beim Lesen des Protokolls trennen lassen.
    health = evaluate_valuation_coverage(_run(10, 4, 3, failed=3), NOW)
    assert health is not None
    assert health.status == "stale"
    assert "6 von 10" in health.detail
    assert "3 übersprungen" in health.detail
    assert "3 fehlgeschlagen" in health.detail
