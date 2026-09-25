"""Brute-Force-Bremse fuer den Dashboard-Login: Redis-Zaehler pro Client-IP.

Gezaehlt wird jeder Versuch VOR der Passwortpruefung, und zwar atomar
(INCR + EXPIRE NX in einer Transaktion). Ein erst nach dem Fehlschlag
erhoehter Zaehler liesse eine parallele Salve komplett durch, weil alle
Requests denselben alten Stand lesen. So bekommt jeder Versuch eine eigene
Nummer, und ab Nummer MAX_ATTEMPTS + 1 wird das Passwort gar nicht mehr
geprueft. Ein erfolgreicher Login loescht den Zaehler.

Das Fenster ist fest: Es beginnt mit dem ersten Versuch und wird durch
weitere Versuche nicht verlaengert (EXPIRE NX).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

import structlog
from starlette.requests import Request

from app.security import redis_store

logger = structlog.get_logger()

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60
ATTEMPT_KEY_PREFIX = "lego:login_attempts:"


class LoginLimitUnavailableError(RuntimeError):
    """Redis war nicht erreichbar; ohne Zaehler wird nicht geprueft."""


@dataclass(frozen=True)
class AttemptDecision:
    allowed: bool
    retry_after_seconds: int = 0


def client_ip(request: Request) -> str:
    """Client-IP hinter Caddy.

    Caddys `reverse_proxy` ignoriert eingehende X-Forwarded-*-Werte, solange
    kein `trusted_proxies` gesetzt ist (infra/Caddyfile setzt keins), und
    schreibt den Header neu mit der Adresse, von der die TCP-Verbindung kam.
    Hier kommt also genau ein Eintrag an, und den hat Caddy geschrieben.

    Genommen wird trotzdem der RECHTESTE Eintrag, nicht der linke: Das ist der
    Hop, den der Proxy direkt vor uns angehaengt hat. Alles links davon stammt
    vom Client, sobald irgendein Proxy anhaengt statt ersetzt, und liesse sich
    pro Request neu faelschen, um dem Zaehler auszuweichen.

    Ohne Header (Request kam nicht ueber Caddy) gilt die Peer-Adresse.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            logger.warning("auth.invalid_forwarded_for")
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _key(ip: str) -> str:
    return ATTEMPT_KEY_PREFIX + ip


async def register_attempt(ip: str) -> AttemptDecision:
    """Versuch zaehlen und entscheiden, ob das Passwort ueberhaupt geprueft wird."""
    redis = redis_store.get_redis()
    key = _key(ip)
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if int(count) <= MAX_ATTEMPTS:
            return AttemptDecision(allowed=True)
        ttl = int(await redis.ttl(key))
        if ttl == -1:
            # Key ohne Laufzeit wuerde die IP fuer immer sperren. Die
            # Transaktion oben laesst das nicht zu; das hier ist nur das Netz.
            await redis.expire(key, WINDOW_SECONDS)
            ttl = WINDOW_SECONDS
    except Exception as exc:  # noqa: BLE001 - ohne Zaehler kein Login (fail secure)
        logger.warning("auth.login_limit_unavailable", error_type=type(exc).__name__)
        raise LoginLimitUnavailableError from exc

    logger.warning("auth.login_rate_limited", attempts=int(count))
    # ttl == -2: Key ist zwischen INCR und TTL abgelaufen, das Fenster ist also vorbei.
    return AttemptDecision(allowed=False, retry_after_seconds=max(ttl, 1))


async def reset_attempts(ip: str) -> None:
    """Nach erfolgreichem Login. Ein Fehler hier darf den Login nicht kippen."""
    try:
        await redis_store.get_redis().delete(_key(ip))
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.login_limit_reset_failed", error_type=type(exc).__name__)
