"""Bridge async task bodies into sync Celery tasks.

Every Celery task runs its coroutine in a fresh event loop via
``asyncio.run``. The shared QueuePool engine in ``app.models.base`` must not
carry pooled asyncpg connections from one loop into the next — a connection
bound to a previous loop raises "got Future attached to a different loop" —
so the pool is disposed after every task run. The API process keeps its
long-lived loop and is unaffected.
"""

import asyncio

from app.models.base import engine


def run_async(coro):
    """Run async code inside sync Celery tasks, resetting the DB pool afterwards."""

    async def _with_pool_cleanup():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_with_pool_cleanup())
