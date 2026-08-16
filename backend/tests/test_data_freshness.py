from datetime import UTC, datetime, timedelta

from app.services.heartbeat import evaluate_data_freshness

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def test_no_problem_when_watchlist_empty():
    assert evaluate_data_freshness(None, 0, NOW) is None


def test_no_problem_when_prices_fresh():
    assert evaluate_data_freshness(NOW - timedelta(hours=7), 3, NOW) is None


def test_problem_when_prices_stale():
    problem = evaluate_data_freshness(NOW - timedelta(hours=27), 3, NOW)
    assert problem is not None
    assert problem.status == "stale"
    assert problem.task_name == "pipeline.price_data_freshness"


def test_problem_when_no_price_ever_written():
    problem = evaluate_data_freshness(None, 3, NOW)
    assert problem is not None
    assert problem.status == "stale"
    assert problem.age_seconds is None
