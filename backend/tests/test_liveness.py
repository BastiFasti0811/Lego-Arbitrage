"""Liveness files behind the worker/beat Compose healthchecks."""

import os
import time
from unittest.mock import MagicMock

import pytest

from app import liveness
from app.tasks import liveness_hooks


@pytest.fixture(autouse=True)
def liveness_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CELERY_LIVENESS_DIR", str(tmp_path))
    for name in (
        "CELERY_LIVENESS_MAX_AGE_WORKER",
        "CELERY_LIVENESS_MAX_AGE_BEAT",
        "CELERY_LIVENESS_MAX_TASK_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _age_file(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


@pytest.mark.parametrize("role", ["worker", "beat"])
def test_missing_file_is_unhealthy(role):
    healthy, reason = liveness.check(role)
    assert not healthy
    assert "never reported alive" in reason


@pytest.mark.parametrize("role", ["worker", "beat"])
def test_fresh_touch_is_healthy(role):
    liveness.touch_alive(role)
    assert liveness.check(role)[0]


def test_stale_worker_is_unhealthy():
    liveness.touch_alive("worker")
    _age_file(liveness.alive_path("worker"), 121)
    healthy, reason = liveness.check("worker")
    assert not healthy
    assert "limit 120s" in reason


def test_beat_allows_three_minutes():
    liveness.touch_alive("beat")
    _age_file(liveness.alive_path("beat"), 170)
    assert liveness.check("beat")[0]
    _age_file(liveness.alive_path("beat"), 190)
    assert not liveness.check("beat")[0]


def test_max_age_env_override(monkeypatch):
    monkeypatch.setenv("CELERY_LIVENESS_MAX_AGE_WORKER", "600")
    liveness.touch_alive("worker")
    _age_file(liveness.alive_path("worker"), 300)
    assert liveness.check("worker")[0]


def test_busy_worker_stays_healthy_while_task_runs():
    liveness.touch_alive("worker")
    _age_file(liveness.alive_path("worker"), 900)
    liveness.mark_busy()
    _age_file(liveness.busy_path(), 600)
    healthy, reason = liveness.check("worker")
    assert healthy
    assert "busy" in reason


def test_stuck_task_turns_worker_unhealthy(monkeypatch):
    monkeypatch.setenv("CELERY_LIVENESS_MAX_TASK_SECONDS", "60")
    liveness.mark_busy()
    _age_file(liveness.busy_path(), 61)
    healthy, reason = liveness.check("worker")
    assert not healthy
    assert "limit 60s" in reason


def test_busy_marker_does_not_cover_beat():
    liveness.mark_busy()
    assert not liveness.check("beat")[0]


def test_clear_busy_is_idempotent():
    liveness.clear_busy()
    liveness.mark_busy()
    liveness.clear_busy()
    assert not liveness.busy_path().exists()


def test_touch_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("CELERY_LIVENESS_DIR", str(blocker / "sub"))
    liveness.touch_alive("worker")
    liveness.mark_busy()
    liveness.clear_busy()


def test_cli_exit_codes(capsys):
    assert liveness.main(["beat"]) == 1
    liveness.touch_alive("beat")
    assert liveness.main(["beat"]) == 0
    assert liveness.main([]) == 2
    assert liveness.main(["api"]) == 2


def test_task_signals_maintain_busy_marker():
    liveness_hooks._on_task_prerun()
    assert liveness.busy_path().exists()
    liveness_hooks._on_task_postrun()
    assert not liveness.busy_path().exists()
    assert liveness.check("worker")[0]


def test_worker_step_touches_and_schedules_timer():
    step = liveness_hooks.WorkerLivenessStep(MagicMock())
    consumer = MagicMock()
    step.start(consumer)
    assert liveness.check("worker")[0]
    consumer.timer.call_repeatedly.assert_called_once()
    interval, fn, args = consumer.timer.call_repeatedly.call_args.args
    assert interval == liveness.WORKER_TOUCH_INTERVAL
    assert fn is liveness.touch_alive and args == ("worker",)
    tref = consumer.timer.call_repeatedly.return_value
    step.stop(consumer)
    tref.cancel.assert_called_once()
    step.shutdown(consumer)  # second stop is harmless
    tref.cancel.assert_called_once()


def test_beat_scheduler_touches_on_tick(monkeypatch):
    monkeypatch.setattr(liveness_hooks.PersistentScheduler, "tick", lambda self, *a, **k: 42.0)
    scheduler = liveness_hooks.LivenessScheduler.__new__(liveness_hooks.LivenessScheduler)
    assert scheduler.tick() == 42.0
    assert liveness.check("beat")[0]


def test_celery_app_wires_liveness():
    from app.tasks.celery_app import celery_app

    assert celery_app.conf.beat_scheduler == "app.tasks.liveness_hooks:LivenessScheduler"
    assert celery_app.conf.beat_max_loop_interval == liveness.BEAT_MAX_LOOP_INTERVAL
    assert liveness_hooks.WorkerLivenessStep in celery_app.steps["consumer"]
