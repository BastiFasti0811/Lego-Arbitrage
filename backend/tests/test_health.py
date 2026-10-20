"""/health: 200 nur mit erreichbarer DB und Redis, sonst 503; Version = Git-SHA."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import health
from app.config import settings
from app.main import app
from app.security import redis_store
from tests.fake_redis import FakeRedis


async def _ok() -> None:
    return None


async def _db_down() -> None:
    raise OSError("connection refused (test)")


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_store, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def client(monkeypatch, fake_redis):
    # Die echte DB-Pruefung braucht PostgreSQL; hier zaehlt nur die Auswertung.
    monkeypatch.setitem(health.CHECKS, "database", _ok)
    monkeypatch.setattr(settings, "git_sha", "")
    return TestClient(app)


def test_healthy_when_database_and_redis_answer(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["checks"] == {"database": "ok", "redis": "ok"}
    assert body["service"] == "lego-arbitrage-api"
    assert response.headers["cache-control"] == "no-store"


def test_unhealthy_with_503_when_database_is_down(client, monkeypatch):
    monkeypatch.setitem(health.CHECKS, "database", _db_down)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["checks"] == {"database": "error", "redis": "ok"}
    # Kein Fehlertext in der oeffentlichen Antwort (DSN, Hostnamen).
    assert "connection refused" not in response.text


def test_unhealthy_with_503_when_redis_is_down(client, fake_redis):
    fake_redis.fail = True

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}


def test_hanging_dependency_times_out_instead_of_blocking(client, monkeypatch):
    async def _hang() -> None:
        await asyncio.sleep(60)

    monkeypatch.setitem(health.CHECKS, "database", _hang)
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error"


def test_health_needs_no_login(client):
    # Docker, Deploy-Skript und Autopilot fragen ohne Cookie; der frische
    # TestClient hat keins.
    assert not client.cookies
    response = client.get("/health")

    assert response.status_code == 200


def test_version_is_git_sha_when_baked_into_the_image(client, monkeypatch):
    sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(settings, "git_sha", sha)

    assert client.get("/health").json()["version"] == sha
    assert client.get("/").json()["version"] == sha


def test_version_falls_back_to_app_version_without_git_sha(client):
    assert client.get("/health").json()["version"] == settings.app_version
