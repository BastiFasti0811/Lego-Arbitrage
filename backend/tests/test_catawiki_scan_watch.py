"""Watchdog fuer den Catawiki-Heimscanner: evaluate_catawiki_scan (rein, ohne DB)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.services.heartbeat import CatawikiScanWatch, evaluate_catawiki_scan

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=30)

HEALTHY = CatawikiScanWatch(
    home_runner=True,
    token_configured=True,
    scan_urls_configured=True,
    frequency="daily",
    config_changed_at=LONG_AGO,
    runner_seen_at=NOW - timedelta(minutes=10),
    last_scan_at=NOW - timedelta(hours=3, minutes=20),  # heute 08:40
    last_scan_status="OK",
    last_scan_lots=12,
    last_scan_errors=[],
)


def _names(watch: CatawikiScanWatch) -> dict[str, str]:
    return {p.task_name: p.status for p in evaluate_catawiki_scan(watch, NOW)}


def test_healthy_scanner_is_quiet():
    assert evaluate_catawiki_scan(HEALTHY, NOW) == []


def test_without_scan_urls_nothing_is_set_up_and_nothing_alerts():
    # Prod-Stand 20.10.2026: weder Token noch URLs.
    watch = replace(HEALTHY, token_configured=False, scan_urls_configured=False,
                    runner_seen_at=None, last_scan_at=None)
    assert evaluate_catawiki_scan(watch, NOW) == []


def test_silent_runner_alerts_after_one_hour():
    assert _names(replace(HEALTHY, runner_seen_at=NOW - timedelta(minutes=55))) == {}
    problems = evaluate_catawiki_scan(replace(HEALTHY, runner_seen_at=NOW - timedelta(hours=2)), NOW)
    assert [(p.task_name, p.status) for p in problems] == [("pipeline.catawiki_home_runner", "stale")]
    assert "meldet sich nicht mehr" in problems[0].detail and "systemctl --user" in problems[0].detail
    assert problems[0].age_seconds == pytest.approx(7200)


def test_runner_that_never_reported_gets_the_grace_period_after_setup():
    fresh = replace(HEALTHY, runner_seen_at=None, last_scan_at=None, config_changed_at=NOW - timedelta(minutes=20))
    assert _names(fresh) == {}
    stale = replace(fresh, config_changed_at=NOW - timedelta(hours=3))
    problems = evaluate_catawiki_scan(stale, NOW)
    assert problems[0].task_name == "pipeline.catawiki_home_runner"
    assert "noch nie gemeldet" in problems[0].detail


def test_runner_silence_is_not_checked_when_prod_scans_itself():
    watch = replace(HEALTHY, home_runner=False, runner_seen_at=None)
    assert _names(watch) == {}


@pytest.mark.parametrize(("frequency", "age_h", "alert"), [
    ("daily", 25, False),
    ("daily", 27, True),
    (" Daily ", 27, True),
    ("weekly", 160, False),
    ("weekly", 171, True),
    ("off", 999, False),
    (None, 999, False),
    ("unsinn", 999, False),
])
def test_scan_age_against_schedule(frequency, age_h, alert):
    watch = replace(HEALTHY, frequency=frequency, last_scan_at=NOW - timedelta(hours=age_h))
    assert ("pipeline.catawiki_scan" in _names(watch)) is alert


def test_stale_scan_detail_names_the_age():
    problems = evaluate_catawiki_scan(replace(HEALTHY, last_scan_at=NOW - timedelta(hours=30)), NOW)
    assert problems[0].detail == "Letzter Catawiki-Scan vor 30.0 h"


def test_recent_config_change_resets_the_scan_deadline():
    # Zeitplan gestern Abend eingeschaltet, erster Scan erst heute 08:40: kein Alarm.
    watch = replace(HEALTHY, last_scan_at=None, config_changed_at=NOW - timedelta(hours=15))
    assert _names(watch) == {}
    problems = evaluate_catawiki_scan(replace(watch, config_changed_at=NOW - timedelta(hours=27)), NOW)
    assert problems[0].detail == "Noch nie ein Catawiki-Scan angekommen"


def test_schedule_without_token_says_why_nothing_arrives():
    watch = replace(HEALTHY, token_configured=False, runner_seen_at=None,
                    last_scan_at=NOW - timedelta(days=3))
    problems = evaluate_catawiki_scan(watch, NOW)
    assert [p.task_name for p in problems] == ["pipeline.catawiki_scan"]
    assert "kein Heimrechner-Token" in problems[0].detail


def test_fresh_but_empty_failed_scan_alerts_as_failing():
    watch = replace(HEALTHY, last_scan_status="FAILED", last_scan_lots=0,
                    last_scan_errors=["Heimrechner: .../de/c/375-lego nicht lesbar (HTTPStatusError)"])
    problems = evaluate_catawiki_scan(watch, NOW)
    assert [(p.task_name, p.status) for p in problems] == [("pipeline.catawiki_scan", "failing")]
    assert "HTTPStatusError" in problems[0].detail


def test_partial_failures_with_lots_do_not_alert():
    watch = replace(HEALTHY, last_scan_status="FAILED", last_scan_lots=8,
                    last_scan_errors=["Heimrechner: Los 1 nicht lesbar (HTTPStatusError)"])
    assert evaluate_catawiki_scan(watch, NOW) == []


def test_naive_timestamps_are_treated_as_utc():
    watch = replace(HEALTHY, runner_seen_at=(NOW - timedelta(hours=2)).replace(tzinfo=None),
                    last_scan_at=(NOW - timedelta(hours=1)).replace(tzinfo=None),
                    config_changed_at=LONG_AGO.replace(tzinfo=None))
    assert _names(watch) == {"pipeline.catawiki_home_runner": "stale"}
