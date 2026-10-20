"""Liveness files for the Celery worker and beat containers.

The Compose healthchecks of ``worker`` and ``beat`` run
``python -m app.liveness worker`` / ``python -m app.liveness beat`` inside the
container. Both processes touch a file while they are alive (see
``app/tasks/liveness_hooks.py``); the check fails when the file is older than
the allowed age, so a hung or broker-less process turns the container
``unhealthy`` instead of looking fine because PID 1 still exists.

The worker runs with ``--pool=solo``: a task blocks the consumer loop, so the
periodic touch pauses while a task runs. A busy marker written on task start
(and removed on task end) keeps the worker healthy for up to
``CELERY_LIVENESS_MAX_TASK_SECONDS`` per task; a task running longer than that
counts as stuck.

This module uses the standard library only: the healthcheck runs every
30 seconds and must not import the app settings, Celery or SQLAlchemy.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROLES = ("worker", "beat")

# Seconds between two touches of the alive file by the worker consumer timer.
WORKER_TOUCH_INTERVAL = 30
# Upper bound for the beat scheduler's sleep between two ticks (each tick touches).
BEAT_MAX_LOOP_INTERVAL = 60

DEFAULT_MAX_AGE = {"worker": 120, "beat": 180}
DEFAULT_MAX_TASK_SECONDS = 3600


def liveness_dir() -> Path:
    return Path(os.environ.get("CELERY_LIVENESS_DIR", "/tmp"))


def alive_path(role: str) -> Path:
    return liveness_dir() / f"celery-{role}.alive"


def busy_path() -> Path:
    return liveness_dir() / "celery-worker.busy"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def touch_alive(role: str) -> None:
    """Mark ``role`` as alive now. Never raises: liveness must not break work."""
    try:
        path = alive_path(role)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass


def mark_busy() -> None:
    try:
        path = busy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())))
    except OSError:
        pass


def clear_busy() -> None:
    try:
        busy_path().unlink(missing_ok=True)
    except OSError:
        pass


def _age(path: Path, now: float) -> float | None:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return None


def check(role: str, now: float | None = None) -> tuple[bool, str]:
    """Return (healthy, reason) for ``role``."""
    if role not in ROLES:
        return False, f"unknown role {role!r}"
    now = time.time() if now is None else now
    max_age = _int_env(f"CELERY_LIVENESS_MAX_AGE_{role.upper()}", DEFAULT_MAX_AGE[role])

    age = _age(alive_path(role), now)
    if age is not None and age <= max_age:
        return True, f"{role} alive {int(age)}s ago"

    if role == "worker":
        busy_age = _age(busy_path(), now)
        max_task = _int_env("CELERY_LIVENESS_MAX_TASK_SECONDS", DEFAULT_MAX_TASK_SECONDS)
        if busy_age is not None:
            if busy_age <= max_task:
                return True, f"worker busy with a task for {int(busy_age)}s"
            return False, f"worker task running for {int(busy_age)}s (limit {max_task}s)"

    if age is None:
        return False, f"{role} never reported alive ({alive_path(role)} missing)"
    return False, f"{role} last alive {int(age)}s ago (limit {max_age}s)"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ROLES:
        print(f"usage: python -m app.liveness {{{'|'.join(ROLES)}}}", file=sys.stderr)
        return 2
    healthy, reason = check(args[0])
    print(reason, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
