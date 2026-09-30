"""Authentication routes — password login with a server-side session in Redis."""

import hmac

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.security import login_limit, sessions

router = APIRouter()

COOKIE_NAME = "lego_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds

_STORE_UNAVAILABLE = {"detail": "Anmeldung voruebergehend nicht moeglich (Session-Speicher nicht erreichbar)"}


def _auth_ready() -> bool:
    """Require explicit auth configuration from the environment."""
    return bool(settings.dashboard_password and settings.session_secret)


async def verify_cookie(cookie_value: str | None) -> bool:
    """True, wenn das Cookie auf eine lebende Session in Redis zeigt.

    Ist Redis nicht erreichbar, ist das Ergebnis False: Der Request gilt als
    nicht angemeldet, statt ungeprueft durchzugehen.
    """
    if not cookie_value or not _auth_ready():
        return False
    return await sessions.is_valid_session(cookie_value)


def _cookie_flags() -> dict:
    return {
        "httponly": True,
        "samesite": settings.session_cookie_samesite,
        "secure": settings.session_cookie_secure,
    }


def _clear_session_cookies(response: Response) -> None:
    """Cookie am konfigurierten Pfad loeschen und ein Alt-Cookie an `/` gleich mit.

    Vor SESSION_COOKIE_PATH lag das Cookie an `/`. Bleibt es liegen, schickt
    der Browser unter /lego zwei `lego_session` mit, und Starlette behaelt
    beim Parsen den zuletzt genannten, also das ungueltige Alt-Cookie: Der
    Nutzer kaeme aus der Login-Schleife nicht mehr heraus.
    """
    response.delete_cookie(key=COOKIE_NAME, path=settings.session_cookie_path, **_cookie_flags())
    if settings.session_cookie_path != "/":
        response.delete_cookie(key=COOKIE_NAME, path="/", **_cookie_flags())


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    if not _auth_ready():
        return JSONResponse(status_code=503, content={"detail": "Dashboard auth is not configured"})

    ip = login_limit.client_ip(request)
    try:
        decision = await login_limit.register_attempt(ip)
    except login_limit.LoginLimitUnavailableError:
        return JSONResponse(status_code=503, content=_STORE_UNAVAILABLE)
    if not decision.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Zu viele Fehlversuche. Bitte spaeter erneut versuchen."},
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    if not hmac.compare_digest(body.password.encode(), settings.dashboard_password.encode()):
        return JSONResponse(status_code=401, content={"detail": "Invalid password"})

    try:
        session_id = await sessions.create_session(COOKIE_MAX_AGE)
    except sessions.SessionStoreUnavailableError:
        return JSONResponse(status_code=503, content=_STORE_UNAVAILABLE)
    await login_limit.reset_attempts(ip)

    if settings.session_cookie_path != "/":
        response.delete_cookie(key=COOKIE_NAME, path="/", **_cookie_flags())
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        path=settings.session_cookie_path,
        **_cookie_flags(),
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    _clear_session_cookies(response)
    try:
        await sessions.delete_session(request.cookies.get(COOKIE_NAME))
    except sessions.SessionStoreUnavailableError:
        # Das Cookie ist im Browser weg, die Session in Redis aber nicht. Ohne
        # ehrlichen Fehler glaubte der Nutzer, ein kopiertes Cookie sei tot.
        failed = JSONResponse(
            status_code=503,
            content={"detail": "Abgemeldet, aber die Session konnte serverseitig nicht beendet werden"},
        )
        _clear_session_cookies(failed)
        return failed
    return {"authenticated": False}


@router.get("/check")
async def check(request: Request):
    if not _auth_ready():
        return JSONResponse(status_code=503, content={"detail": "Dashboard auth is not configured"})
    if await verify_cookie(request.cookies.get(COOKIE_NAME)):
        return {"authenticated": True}
    return JSONResponse(status_code=401, content={"authenticated": False})
