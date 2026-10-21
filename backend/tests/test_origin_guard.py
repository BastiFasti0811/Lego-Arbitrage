"""CSRF-Schutz: schreibende API-Aufrufe nur aus dem eigenen Origin.

Security-Nachtlauf 21.10.2026, Ticket 1e3cd68a (Punkt 2, Pfad B): Lego teilt
sich `http://<host>` mit anderen Apps. Ein Formular-POST von `http://<host>:3000`
gilt dem Browser als same-site und schickte das Lax-Cookie mit; die
Middleware pruefte nur das Cookie.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth
from app.config import Settings, settings
from app.main import app
from app.security import redis_store
from app.security.origin_guard import is_allowed_write
from tests.fake_redis import FakeRedis

PASSWORD = "richtig-langes-testpasswort"
TARGET = "/api/remote-scan/request"
HOST = "testserver"  # Host-Header des TestClients
OWN_ORIGIN = f"http://{HOST}"
FOREIGN_PORT = f"http://{HOST}:3000"


# ── Entscheidungsregel ──────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reading_methods_are_never_blocked(method):
    assert is_allowed_write(method, {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}, [])


@pytest.mark.parametrize(("headers", "allowed"), [
    # Aktuelle Browser: Sec-Fetch-Site entscheidet.
    ({"sec-fetch-site": "same-origin", "origin": OWN_ORIGIN, "host": HOST}, True),
    ({"sec-fetch-site": "none", "host": HOST}, True),
    ({"sec-fetch-site": "same-site", "origin": FOREIGN_PORT, "host": HOST}, False),
    ({"sec-fetch-site": "cross-site", "origin": "https://evil.example", "host": HOST}, False),
    # Sec-Fetch-Site schlaegt einen passenden Origin nicht: fremd bleibt fremd.
    ({"sec-fetch-site": "cross-site", "origin": OWN_ORIGIN, "host": HOST}, False),
    ({"sec-fetch-site": "cross-site", "host": HOST}, False),
    # Aeltere Browser ohne Sec-Fetch-Site: Origin gegen Host.
    ({"origin": OWN_ORIGIN, "host": HOST}, True),
    ({"origin": "HTTP://TestServer/", "host": HOST}, True),
    ({"origin": FOREIGN_PORT, "host": HOST}, False),
    ({"origin": "https://evil.example", "host": HOST}, False),
    ({"origin": "null", "host": HOST}, False),
    ({"origin": "", "host": HOST}, False),
    # Kein Browser (curl, Skripte, Heimrechner): kein fremdes Cookie, kein CSRF.
    ({"host": HOST}, True),
    ({}, True),
])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_writing_methods_need_the_own_origin(method, headers, allowed):
    assert is_allowed_write(method, headers, []) is allowed


def test_allowlisted_foreign_origin_may_write_cross_site():
    partner = "https://lego.example.org"
    headers = {"sec-fetch-site": "cross-site", "origin": partner, "host": HOST}
    assert is_allowed_write("POST", headers, [f"{partner}/"]) is True
    assert is_allowed_write("POST", headers, ["https://other.example.org", " "]) is False


def test_cors_allowlist_comes_from_the_setting():
    assert Settings(cors_allowed_origins="").cors_origin_list == []
    parsed = Settings(cors_allowed_origins=" https://a.example/ ,,https://b.example:8443 ").cors_origin_list
    assert parsed == ["https://a.example", "https://b.example:8443"]


def test_old_hardcoded_cors_origins_are_gone():
    assert "http://localhost:3000" not in Settings().cors_origin_list
    assert "https://lego-arbitrage.de" not in Settings().cors_origin_list


# ── Middleware ──────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_store, "get_redis", lambda: fake)
    monkeypatch.setattr(settings, "dashboard_password", PASSWORD)
    monkeypatch.setattr(settings, "session_secret", "test-secret-nicht-produktiv")
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    monkeypatch.setattr(settings, "session_cookie_path", "/")
    monkeypatch.setattr(settings, "cors_allowed_origins", "")
    return TestClient(app)


@pytest.fixture
def session_cookie(client):
    response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == 200
    session_id = response.cookies[auth.COOKIE_NAME]
    client.cookies.clear()
    return {"Cookie": f"{auth.COOKIE_NAME}={session_id}"}


@pytest.mark.parametrize("foreign", [
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-site", "Origin": FOREIGN_PORT},
    {"Origin": FOREIGN_PORT},
])
def test_foreign_post_to_remote_scan_request_is_403_even_with_session(client, session_cookie, foreign):
    response = client.post(TARGET, headers={**session_cookie, **foreign})

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin write rejected"}


def test_same_origin_post_reaches_the_route_as_before(client, session_cookie, monkeypatch):
    # Bis zur Route kommt die Anfrage: ohne DB-Zugriff mit einem Stub antworten.
    from app.api.routes import remote_scan as routes

    async def _status(session):
        return {"token_configured": False}

    monkeypatch.setattr(routes, "scan_status", _status)

    async def _no_db():
        yield None

    from app.models import get_session

    app.dependency_overrides[get_session] = _no_db
    try:
        for own in (
            {"Sec-Fetch-Site": "same-origin", "Origin": OWN_ORIGIN},
            {"Origin": OWN_ORIGIN},
            {},
        ):
            response = client.post(TARGET, headers={**session_cookie, **own})
            # 409 = Route lief (kein Token eingerichtet), also weder 401 noch 403.
            assert response.status_code == 409, own
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_foreign_login_and_logout_are_rejected(client):
    foreign = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    assert client.post("/api/auth/login", json={"password": PASSWORD}, headers=foreign).status_code == 403
    assert client.post("/api/auth/logout", headers=foreign).status_code == 403


def test_foreign_get_is_still_only_a_cookie_question(client, session_cookie):
    # Lesende Aufrufe schuetzt die Same-Origin-Policy (Antwort nicht lesbar);
    # der Guard laesst sie durch, die Middleware prueft weiter nur das Cookie.
    foreign = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    assert client.get("/api/__auth_probe__", headers=foreign).status_code == 401
    assert client.get("/api/__auth_probe__", headers={**session_cookie, **foreign}).status_code == 404


def test_runner_paths_are_not_subject_to_the_origin_guard(client, monkeypatch):
    # Token-geschuetzt, ohne Cookie: ein Origin-Header aendert daran nichts,
    # die Route antwortet selbst mit 401 (falsches Token), nicht der Guard mit 403.
    from unittest.mock import AsyncMock

    from app.api.routes import remote_scan as routes

    monkeypatch.setattr(routes, "get_settings_map", AsyncMock(return_value={"remote_scan_token": "x" * 32}))
    response = client.post(
        "/api/remote-scan/runner/results",
        json={},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example", "Authorization": "Bearer falsch"},
    )
    assert response.status_code == 401
