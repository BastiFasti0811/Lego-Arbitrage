"""Serverseitige Dashboard-Sessions in Redis.

Jeder Login bekommt eine eigene Zufalls-ID. Redis haelt sie mit derselben
Laufzeit wie das Cookie; Logout loescht den Eintrag, damit ein kopiertes
Cookie danach nichts mehr wert ist.

Im Key steht nur der SHA-256 der ID, nicht die ID selbst: Wer einen
Redis-Dump liest, bekommt keine gueltigen Cookies.

Der Wert ist ein Fingerabdruck aus Passwort und Secret. Das haelt die
Eigenschaft des alten HMAC-Tokens: Aendert sich eines von beiden, sind alle
laufenden Sessions sofort ungueltig, ohne Redis anzufassen.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import structlog

from app.config import settings
from app.security import redis_store

logger = structlog.get_logger()

SESSION_KEY_PREFIX = "lego:session:"
# token_urlsafe(32) ergibt 43 Zeichen. Laengere Cookie-Werte sind nie von uns
# und muessen nicht erst gehasht und nachgeschlagen werden.
_MAX_SESSION_ID_LENGTH = 128


class SessionStoreUnavailableError(RuntimeError):
    """Redis war nicht erreichbar; der Aufrufer entscheidet, wie er scheitert."""


def _session_key(session_id: str) -> str:
    return SESSION_KEY_PREFIX + hashlib.sha256(session_id.encode()).hexdigest()


def _credential_fingerprint() -> str:
    if not (settings.session_secret and settings.dashboard_password):
        raise RuntimeError("Dashboard auth is not configured")
    return hmac.new(
        settings.session_secret.encode(),
        b"lego-session-v2:" + settings.dashboard_password.encode(),
        hashlib.sha256,
    ).hexdigest()


async def create_session(ttl_seconds: int) -> str:
    """Neue Session anlegen und ihre ID fuer das Cookie zurueckgeben."""
    session_id = secrets.token_urlsafe(32)
    try:
        await redis_store.get_redis().set(_session_key(session_id), _credential_fingerprint(), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 - jede Redis-Stoerung heisst: keine Session
        logger.warning("auth.session_store_unavailable", op="create", error_type=type(exc).__name__)
        raise SessionStoreUnavailableError from exc
    return session_id


async def is_valid_session(session_id: str | None) -> bool:
    """True nur, wenn Redis die Session kennt. Jede Stoerung ergibt False (fail secure)."""
    if not session_id or len(session_id) > _MAX_SESSION_ID_LENGTH:
        return False
    try:
        stored = await redis_store.get_redis().get(_session_key(session_id))
    except Exception as exc:  # noqa: BLE001 - lieber 401 als ungepruefter Durchlass
        logger.warning("auth.session_store_unavailable", op="verify", error_type=type(exc).__name__)
        return False
    if stored is None:
        return False
    return hmac.compare_digest(str(stored), _credential_fingerprint())


async def delete_session(session_id: str | None) -> None:
    """Session serverseitig beenden. Scheitert Redis, bleibt nur das Cookie-Loeschen."""
    if not session_id or len(session_id) > _MAX_SESSION_ID_LENGTH:
        return
    try:
        await redis_store.get_redis().delete(_session_key(session_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.session_store_unavailable", op="delete", error_type=type(exc).__name__)
        raise SessionStoreUnavailableError from exc
