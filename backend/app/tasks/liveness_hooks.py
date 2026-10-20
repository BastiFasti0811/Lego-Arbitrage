"""Celery hooks that keep the liveness files of worker and beat fresh.

See ``app/liveness.py`` for the check side (Compose healthcheck).

- Worker: a consumer bootstep touches the alive file every
  ``WORKER_TOUCH_INTERVAL`` seconds while the consumer is connected. Consumer
  steps are stopped when the broker connection drops, so a worker that lost
  Redis stops touching and turns unhealthy. ``task_prerun``/``task_postrun``
  maintain the busy marker for long tasks (solo pool blocks the timer).
- Beat: ``LivenessScheduler`` touches the alive file on every tick; the beat
  loop sleeps at most ``BEAT_MAX_LOOP_INTERVAL`` seconds between ticks.
"""

from __future__ import annotations

from celery import bootsteps
from celery.beat import PersistentScheduler
from celery.signals import task_postrun, task_prerun

from app.liveness import WORKER_TOUCH_INTERVAL, clear_busy, mark_busy, touch_alive


class WorkerLivenessStep(bootsteps.StartStopStep):
    """Consumer step: touch the worker alive file while connected to the broker."""

    requires = {"celery.worker.consumer.tasks:Tasks"}

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.tref = None

    def start(self, parent):
        touch_alive("worker")
        self.tref = parent.timer.call_repeatedly(WORKER_TOUCH_INTERVAL, touch_alive, ("worker",), priority=10)

    def stop(self, parent):
        if self.tref is not None:
            self.tref.cancel()
            self.tref = None

    shutdown = stop


class LivenessScheduler(PersistentScheduler):
    """Default persistent beat scheduler plus a liveness touch per tick."""

    def tick(self, *args, **kwargs):
        touch_alive("beat")
        return super().tick(*args, **kwargs)


def install(app) -> None:
    """Register the worker step and the beat scheduler on ``app``."""
    app.steps["consumer"].add(WorkerLivenessStep)


@task_prerun.connect
def _on_task_prerun(**kwargs) -> None:
    mark_busy()


@task_postrun.connect
def _on_task_postrun(**kwargs) -> None:
    clear_busy()
    touch_alive("worker")
