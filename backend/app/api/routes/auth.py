"""Authentication routes — simple password auth with signed cookie."""

import hashlib
import hmac

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.config import settings

router = APIRouter()

COOKIE_NAME = "lego_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds


def _auth_ready() -> bool:
    """Require explicit auth configuration from the environment."""
    return bool(settings.dashboard_password and settings.session_secret)


def _make_token() -> str:
    """Create HMAC-SHA256 token from password + secret key."""
    if not _auth_ready():
        raise RuntimeError("Dashboard auth is not configured")
    return hmac.new(
        settings.session_secret.encode(),
        settings.dashboard_password.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_cookie(cookie_value: str | None) -> bool:
    """Return True if the cookie value matches the expected token."""
    if not cookie_value or not _auth_ready():
        return False
    return hmac.compare_digest(cookie_value, _make_token())


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    if not _auth_ready():
        return Response(
            content='{"detail":"Dashboard auth is not configured"}',
            status_code=503,
            media_type="application/json",
        )
    if not hmac.compare_digest(body.password, settings.dashboard_password):
        return Response(
            content='{"detail":"Invalid password"}',
            status_code=401,
            media_type="application/json",
        )
    token = _make_token()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=False,  # set True behind HTTPS in production
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME)
    return {"authenticated": False}


@router.get("/check")
async def check(request: Request):
    if not _auth_ready():
        return Response(
            content='{"detail":"Dashboard auth is not configured"}',
            status_code=503,
            media_type="application/json",
        )
    cookie = request.cookies.get(COOKIE_NAME)
    if verify_cookie(cookie):
        return {"authenticated": True}
    return Response(
        content='{"authenticated":false}',
        status_code=401,
        media_type="application/json",
    )
