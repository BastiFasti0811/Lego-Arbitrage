"""Gemeinsamer Redis-Client fuer Sessions und Login-Limit.

Kurze Timeouts, weil die Auth-Middleware bei jedem /api-Request fragt: Ist
Redis weg, soll der Request nach Sekunden mit 401 enden und nicht haengen.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Lazily gebauter Client; Tests ersetzen diese Funktion durch einen Fake."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client
