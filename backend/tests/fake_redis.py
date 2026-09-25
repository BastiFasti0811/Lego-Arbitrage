"""Kleiner In-Memory-Ersatz fuer redis.asyncio.Redis.

Deckt genau die Befehle ab, die Sessions und Login-Limit nutzen, mit
Redis-Semantik fuer TTLs (ttl: -2 = kein Key, -1 = ohne Laufzeit). Die Uhr
ist von aussen verstellbar, damit Ablauf-Tests nicht warten muessen.
"""

from __future__ import annotations


class FakeRedis:
    def __init__(self) -> None:
        self.now = 0.0
        self._data: dict[str, str] = {}
        self._expires_at: dict[str, float] = {}
        self.fail = False

    # ── Hilfen fuer Tests ───────────────────────────────
    def advance(self, seconds: float) -> None:
        self.now += seconds

    def keys(self) -> list[str]:
        self._purge()
        return sorted(self._data)

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down (test)")

    def _purge(self) -> None:
        for key, deadline in list(self._expires_at.items()):
            if deadline <= self.now:
                self._data.pop(key, None)
                self._expires_at.pop(key, None)

    # ── Redis-Befehle ───────────────────────────────────
    async def get(self, key: str) -> str | None:
        self._check()
        self._purge()
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._check()
        self._data[key] = str(value)
        self._expires_at.pop(key, None)
        if ex is not None:
            self._expires_at[key] = self.now + ex
        return True

    async def delete(self, *keys: str) -> int:
        self._check()
        self._purge()
        removed = 0
        for key in keys:
            if key in self._data:
                removed += 1
            self._data.pop(key, None)
            self._expires_at.pop(key, None)
        return removed

    async def incr(self, key: str) -> int:
        self._check()
        self._purge()
        value = int(self._data.get(key, "0")) + 1
        self._data[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self._check()
        self._purge()
        if key not in self._data or (nx and key in self._expires_at):
            return False
        self._expires_at[key] = self.now + seconds
        return True

    async def ttl(self, key: str) -> int:
        self._check()
        self._purge()
        if key not in self._data:
            return -2
        if key not in self._expires_at:
            return -1
        return int(self._expires_at[key] - self.now)

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    def incr(self, key: str) -> _FakePipeline:
        self._queued.append(("incr", (key,), {}))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> _FakePipeline:
        self._queued.append(("expire", (key, seconds), {"nx": nx}))
        return self

    async def execute(self) -> list:
        self._redis._check()
        results = []
        for name, args, kwargs in self._queued:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queued.clear()
        return results
