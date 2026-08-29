"""Gruen soll wieder heissen, dass Werte entstehen.

Der Lauf vom 25.08.2026 uebersprang 32 von 41 Sets und meldete `success`.
Der Watchdog misst Task-Erfolg, nicht Nutzarbeit — dieselbe Luecke, die
evaluate_data_freshness fuer die Preisdaten bereits schliesst.
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


def test_a_tiny_inventory_does_not_trip_the_share():
    # Bei zwei Sets ist ein Uebersprung schon ueber der Haelfte, sagt aber nichts.
    assert evaluate_valuation_coverage(_run(2, 1, 1), NOW) is None


def test_exactly_half_skipped_is_still_fine():
    assert evaluate_valuation_coverage(_run(10, 5, 5), NOW) is None
