"""Befunde 2-4 des Security-Reviews vom 23.09.2026: Session, Login-Limit, Cookie-Pfad.

Vorher war das Cookie HMAC(secret, passwort): bei jedem Login gleich, ohne
serverseitiges Ablaufdatum, und Logout loeschte nur das Cookie im Browser.
Ein kopiertes Cookie galt, bis jemand Passwort oder Secret aenderte.
"""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth
from app.config import Settings, settings
from app.main import app
from app.security import login_limit, redis_store, sessions
from tests.fake_redis import FakeRedis

PASSWORD = "richtig-langes-testpasswort"
SECRET = "test-secret-nicht-produktiv"
# Middleware laeuft vor dem Routing: ohne Session 401, mit Session 404.
PROTECTED = "/api/__auth_probe__"


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_store, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def client(monkeypatch, fake_redis):
    monkeypatch.setattr(settings, "dashboard_password", PASSWORD)
    monkeypatch.setattr(settings, "session_secret", SECRET)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    monkeypatch.setattr(settings, "session_cookie_path", "/")
    monkeypatch.setattr(settings, "debug", False)
    return TestClient(app)


def _login(client, password=PASSWORD, ip="203.0.113.7"):
    response = client.post("/api/auth/login", json={"password": password}, headers={"X-Forwarded-For": ip})
    # Jeder Test schickt Cookies ausdruecklich mit; der Jar des TestClients
    # wuerde sonst unbemerkt jede Folgeanfrage anmelden.
    client.cookies.clear()
    return response


def _session_id(response) -> str:
    return response.cookies[auth.COOKIE_NAME]


def _with_cookie(session_id: str) -> dict:
    return {"Cookie": f"{auth.COOKIE_NAME}={session_id}"}


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


# ── Befund 2: Session pro Login, serverseitig beendbar ──────────


def test_each_login_gets_a_fresh_random_session(client, fake_redis):
    first = _session_id(_login(client))
    second = _session_id(_login(client))

    assert first != second
    assert len(first) >= 43  # secrets.token_urlsafe(32)
    static_token = hmac.new(SECRET.encode(), PASSWORD.encode(), hashlib.sha256).hexdigest()
    assert static_token not in (first, second)


def test_redis_holds_only_a_hash_of_the_session_id_with_cookie_lifetime(client, fake_redis):
    session_id = _session_id(_login(client))

    session_keys = [k for k in fake_redis.keys() if k.startswith(sessions.SESSION_KEY_PREFIX)]
    assert len(session_keys) == 1
    assert session_id not in session_keys[0]
    assert session_keys[0].endswith(hashlib.sha256(session_id.encode()).hexdigest())
    assert fake_redis._expires_at[session_keys[0]] == fake_redis.now + auth.COOKIE_MAX_AGE


def test_valid_session_passes_middleware_and_check(client):
    session_id = _session_id(_login(client))

    assert client.get("/api/auth/check", headers=_with_cookie(session_id)).status_code == 200
    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 404
    assert client.get(PROTECTED).status_code == 401


def test_logout_kills_the_session_even_for_a_copied_cookie(client, fake_redis):
    session_id = _session_id(_login(client))

    response = client.post("/api/auth/logout", headers=_with_cookie(session_id))

    assert response.status_code == 200
    # Genau der Fall aus dem Befund: Das Cookie wurde vorher kopiert und wird
    # nach dem Logout erneut geschickt.
    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 401
    assert client.get("/api/auth/check", headers=_with_cookie(session_id)).status_code == 401
    assert not [k for k in fake_redis.keys() if k.startswith(sessions.SESSION_KEY_PREFIX)]


def test_session_expires_server_side_after_cookie_lifetime(client, fake_redis):
    session_id = _session_id(_login(client))

    fake_redis.advance(auth.COOKIE_MAX_AGE - 1)
    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 404
    fake_redis.advance(2)
    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 401


def test_old_static_hmac_cookie_no_longer_works(client):
    static_token = hmac.new(SECRET.encode(), PASSWORD.encode(), hashlib.sha256).hexdigest()

    assert client.get(PROTECTED, headers=_with_cookie(static_token)).status_code == 401


def test_password_change_invalidates_running_sessions(client, monkeypatch):
    # Die alte Loesung hatte diese Eigenschaft von selbst; sie soll bleiben.
    session_id = _session_id(_login(client))
    monkeypatch.setattr(settings, "dashboard_password", "neues-passwort-nach-leck")

    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 401


def test_unreachable_redis_means_not_authenticated(client, fake_redis):
    session_id = _session_id(_login(client))
    fake_redis.fail = True

    assert client.get(PROTECTED, headers=_with_cookie(session_id)).status_code == 401
    assert client.get("/api/auth/check", headers=_with_cookie(session_id)).status_code == 401
    assert client.get("/docs", headers=_with_cookie(session_id)).status_code == 401


def test_login_fails_closed_without_redis(client, fake_redis):
    fake_redis.fail = True

    response = _login(client)

    assert response.status_code == 503
    assert auth.COOKIE_NAME not in response.cookies


def test_logout_without_redis_reports_that_the_session_survived(client, fake_redis):
    session_id = _session_id(_login(client))
    fake_redis.fail = True

    response = client.post("/api/auth/logout", headers=_with_cookie(session_id))

    assert response.status_code == 503
    # Das Browser-Cookie wird trotzdem geloescht.
    assert any(h.startswith(f"{auth.COOKIE_NAME}=") and "Max-Age=0" in h for h in _set_cookie_headers(response))


def test_oversized_cookie_is_rejected_without_lookup(client, fake_redis):
    fake_redis.fail = True  # ein Lookup wuerde hier werfen

    assert client.get(PROTECTED, headers=_with_cookie("x" * 500)).status_code == 401


def test_non_ascii_password_is_a_plain_401_not_a_crash(client):
    # hmac.compare_digest auf str wirft TypeError bei Nicht-ASCII.
    assert _login(client, password="passwört").status_code == 401


# ── Befund 3: Brute-Force-Bremse am Login ───────────────────────


def test_sixth_attempt_within_window_is_refused_with_retry_after(client):
    for _ in range(login_limit.MAX_ATTEMPTS):
        assert _login(client, password="falsch").status_code == 401

    blocked = _login(client, password="falsch")

    assert blocked.status_code == 429
    retry_after = int(blocked.headers["Retry-After"])
    assert 0 < retry_after <= login_limit.WINDOW_SECONDS


def test_locked_ip_cannot_log_in_even_with_the_right_password(client):
    for _ in range(login_limit.MAX_ATTEMPTS):
        _login(client, password="falsch")

    response = _login(client)

    assert response.status_code == 429
    assert auth.COOKIE_NAME not in response.cookies


def test_lock_lifts_after_the_window(client, fake_redis):
    for _ in range(login_limit.MAX_ATTEMPTS + 1):
        _login(client, password="falsch")

    fake_redis.advance(login_limit.WINDOW_SECONDS + 1)

    assert _login(client).status_code == 200


def test_further_attempts_do_not_extend_the_window(client, fake_redis):
    for _ in range(login_limit.MAX_ATTEMPTS):
        _login(client, password="falsch")
    fake_redis.advance(login_limit.WINDOW_SECONDS - 10)
    for _ in range(20):
        assert _login(client, password="falsch").status_code == 429

    fake_redis.advance(11)

    assert _login(client).status_code == 200


def test_successful_login_resets_the_counter(client):
    for _ in range(login_limit.MAX_ATTEMPTS - 1):
        _login(client, password="falsch")
    assert _login(client).status_code == 200

    for _ in range(login_limit.MAX_ATTEMPTS):
        assert _login(client, password="falsch").status_code == 401
    assert _login(client, password="falsch").status_code == 429


def test_counter_is_per_client_ip(client):
    for _ in range(login_limit.MAX_ATTEMPTS + 1):
        _login(client, password="falsch", ip="203.0.113.7")

    assert _login(client, ip="198.51.100.20").status_code == 200


def test_spoofed_left_forwarded_for_entries_do_not_dodge_the_counter(client):
    # Caddy haengt die echte Adresse rechts an bzw. setzt nur sie. Was links
    # steht, schreibt der Client selbst und koennte jeden Request wechseln.
    for i in range(login_limit.MAX_ATTEMPTS):
        response = client.post(
            "/api/auth/login",
            json={"password": "falsch"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/auth/login",
        json={"password": "falsch"},
        headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.7"},
    )
    assert blocked.status_code == 429


def test_client_ip_uses_the_rightmost_forwarded_entry():
    request = _request_with({"x-forwarded-for": "1.1.1.1, 2001:db8::5"})
    assert login_limit.client_ip(request) == "2001:db8::5"


def test_client_ip_falls_back_to_peer_on_garbage_header():
    request = _request_with({"x-forwarded-for": "nicht-eine-ip"}, peer="172.18.0.4")
    assert login_limit.client_ip(request) == "172.18.0.4"


def test_client_ip_without_header_is_the_peer():
    assert login_limit.client_ip(_request_with({}, peer="172.18.0.4")) == "172.18.0.4"


def _request_with(headers: dict, peer: str = "172.18.0.2"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 12345),
    }
    return Request(scope)


# ── Befund 4: Cookie-Pfad ───────────────────────────────────────


def test_default_cookie_path_is_root_for_the_local_stack(client):
    headers = _set_cookie_headers(_login(client))

    session_cookies = [h for h in headers if h.startswith(f"{auth.COOKIE_NAME}=")]
    assert len(session_cookies) == 1
    assert "Path=/" in session_cookies[0]
    assert "HttpOnly" in session_cookies[0]


def test_prefixed_deploy_scopes_the_cookie_to_its_path(client, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_path", "/lego")

    headers = _set_cookie_headers(_login(client))

    live = [h for h in headers if "Max-Age=0" not in h]
    cleared = [h for h in headers if "Max-Age=0" in h]
    assert len(live) == 1 and "Path=/lego" in live[0]
    # Das Alt-Cookie an Path=/ wird beim Login entfernt. Sonst schickte der
    # Browser zwei lego_session, und Starlette behielte das alte.
    assert len(cleared) == 1 and "Path=/;" in cleared[0] + ";"


def test_logout_clears_the_cookie_at_the_configured_path(client, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_path", "/lego")
    session_id = _session_id(_login(client))

    headers = _set_cookie_headers(client.post("/api/auth/logout", headers=_with_cookie(session_id)))

    assert all("Max-Age=0" in h for h in headers)
    assert any("Path=/lego" in h for h in headers)
    assert any("Path=/;" in h + ";" for h in headers)


def test_duplicate_cookie_would_shadow_the_new_one():
    # Beleg fuer den Grund des Alt-Cookie-Loeschens: Starlette behaelt beim
    # doppelten Namen den letzten Wert, und der Browser schickt das Cookie mit
    # dem kuerzeren Pfad (/) zuletzt.
    from starlette.requests import cookie_parser

    assert cookie_parser("lego_session=neu; lego_session=alt")["lego_session"] == "alt"


@pytest.mark.parametrize("bad_path", ["lego", "/lego; Domain=evil", "/le go"])
def test_cookie_path_setting_rejects_header_injection(bad_path):
    with pytest.raises(ValueError):
        Settings(session_cookie_path=bad_path, _env_file=None)
