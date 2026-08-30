"""Der Knopf darf keinen zweiten Lauf starten — und ein toter Lauf darf ihn
nicht fuer immer sperren.

Ein Lauf dauert gemessen elf Minuten. Zwei parallele Laeufe wuerden dieselben
Quellen doppelt befragen und den Block beschleunigen. Ein abgestuerzter Lauf
bleibt dagegen auf `running` stehen und wuerde die Bewertung dauerhaft
blockieren, wenn ihn nichts abloest.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.api.routes.inventory import STALE_RUN_MINUTES, is_run_blocking

NOW = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _running(minutes_ago: int):
    return SimpleNamespace(
        id=7, status="running", started_at=NOW - timedelta(minutes=minutes_ago)
    )


def test_no_run_does_not_block():
    assert is_run_blocking(None, NOW) is False


def test_a_fresh_running_run_blocks():
    assert is_run_blocking(_running(5), NOW) is True


def test_a_run_at_the_limit_still_blocks():
    assert is_run_blocking(_running(STALE_RUN_MINUTES - 1), NOW) is True


def test_a_stale_running_run_no_longer_blocks():
    # Sonst sperrt ein abgestuerzter Worker den Knopf fuer immer.
    assert is_run_blocking(_running(STALE_RUN_MINUTES + 1), NOW) is False


def test_a_finished_run_does_not_block():
    finished = SimpleNamespace(id=7, status="success", started_at=NOW - timedelta(minutes=2))
    assert is_run_blocking(finished, NOW) is False
