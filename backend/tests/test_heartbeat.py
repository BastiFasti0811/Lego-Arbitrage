from datetime import UTC, datetime, timedelta

from app.models.heartbeat import TaskHeartbeat
from app.services.heartbeat import MONITORED_TASKS, TaskHealth, evaluate_health, filter_unthrottled

NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _hb(task_name: str, *, success_age_h: float | None, status: str = "success", run_age_h: float | None = None):
    """Build a TaskHeartbeat at a given age without touching the DB."""
    run_age = run_age_h if run_age_h is not None else success_age_h
    hb = TaskHeartbeat(task_name=task_name)
    hb.last_run_at = NOW - timedelta(hours=run_age) if run_age is not None else None
    hb.last_success_at = NOW - timedelta(hours=success_age_h) if success_age_h is not None else None
    hb.last_status = status
    hb.consecutive_failures = 0 if status == "success" else 1
    hb.last_detail = None
    return hb


def _fresh_all() -> list[TaskHeartbeat]:
    """A recent successful heartbeat for every monitored task."""
    return [_hb(name, success_age_h=0.1) for name in MONITORED_TASKS]


def test_all_fresh_is_healthy():
    report = evaluate_health(_fresh_all(), NOW)
    assert report.healthy is True
    assert report.problems == []
    assert {t.status for t in report.tasks} == {"ok"}


def test_missing_heartbeat_is_pending_not_alerted():
    # No heartbeats at all → every task pending, but pending must NOT alert.
    report = evaluate_health([], NOW)
    assert report.healthy is True
    assert report.problems == []
    assert {t.status for t in report.tasks} == {"pending"}


def test_stale_success_is_flagged():
    name = "app.tasks.analyze_new.analyze_new_offers"  # max age 2h
    heartbeats = _fresh_all()
    heartbeats = [hb for hb in heartbeats if hb.task_name != name]
    heartbeats.append(_hb(name, success_age_h=5))  # older than 2h
    report = evaluate_health(heartbeats, NOW)
    assert report.healthy is False
    assert [p.task_name for p in report.problems] == [name]
    assert report.problems[0].status == "stale"


def test_recent_run_within_threshold_is_ok():
    name = "app.tasks.scrape_daily.scrape_all_watched_sets"  # max age 8h
    heartbeats = [hb for hb in _fresh_all() if hb.task_name != name]
    heartbeats.append(_hb(name, success_age_h=7))  # under 8h
    report = evaluate_health(heartbeats, NOW)
    assert report.healthy is True


def test_failing_status_is_flagged_even_if_recent():
    name = "app.tasks.catawiki_scan.scan_configured_categories"
    heartbeats = [hb for hb in _fresh_all() if hb.task_name != name]
    heartbeats.append(_hb(name, success_age_h=None, status="error", run_age_h=0.1))
    report = evaluate_health(heartbeats, NOW)
    assert report.healthy is False
    problem = next(p for p in report.problems if p.task_name == name)
    assert problem.status == "failing"


# ── filter_unthrottled (re-alert window) ──────────────────────────────

CUTOFF = NOW - timedelta(hours=6)


def _problem(name: str) -> TaskHealth:
    return TaskHealth(name, "stale", None, NOW, "success", 99999, 7200, None)


def test_never_alerted_problem_is_eligible():
    name = "app.tasks.analyze_new.analyze_new_offers"
    hb = _hb(name, success_age_h=5)
    hb.last_alerted_at = None
    eligible = filter_unthrottled([_problem(name)], {name: hb}, CUTOFF)
    assert [p.task_name for p in eligible] == [name]


def test_recently_alerted_problem_is_throttled():
    name = "app.tasks.analyze_new.analyze_new_offers"
    hb = _hb(name, success_age_h=5)
    hb.last_alerted_at = NOW - timedelta(hours=1)  # within the 6h window
    eligible = filter_unthrottled([_problem(name)], {name: hb}, CUTOFF)
    assert eligible == []


def test_stale_alert_outside_window_is_eligible_again():
    name = "app.tasks.analyze_new.analyze_new_offers"
    hb = _hb(name, success_age_h=20)
    hb.last_alerted_at = NOW - timedelta(hours=8)  # older than the 6h window
    eligible = filter_unthrottled([_problem(name)], {name: hb}, CUTOFF)
    assert [p.task_name for p in eligible] == [name]
