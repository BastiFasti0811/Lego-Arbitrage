"""CSRF-Schutz fuer schreibende API-Aufrufe: nur aus dem eigenen Origin.

Prod laeuft unter `http://<host>/lego` auf demselben Host wie andere Apps
(Security-Nachtlauf 21.10.2026, Ticket 1e3cd68a). Das Session-Cookie ist
`SameSite=Lax`: ein Formular-POST von einer Seite auf demselben Host, aber
anderem Port (`http://<host>:3000`) gilt dem Browser als same-site und schickt
das Cookie mit. Die Auth-Middleware sah bis dahin nur das Cookie.

Regel fuer POST/PUT/PATCH/DELETE:

1. Schickt der Browser `Sec-Fetch-Site` (alle aktuellen Browser tun das), gilt
   nur `same-origin` oder `none` (direkt eingegeben/Lesezeichen). `same-site`
   und `cross-site` kommen nur durch, wenn ihr `Origin` ausdruecklich in
   `CORS_ALLOWED_ORIGINS` steht.
2. Ohne `Sec-Fetch-Site`, aber mit `Origin` (aeltere Browser): der Origin muss
   in der Allowlist stehen oder als Host:Port genau dem `Host`-Header der
   Anfrage entsprechen. Den `Host` setzt der Browser aus der Ziel-URL, eine
   fremde Seite kann ihn nicht waehlen.
3. Weder noch: kein Browser (curl, Skripte, Heimrechner). Durchlassen -- ohne
   Browser gibt es kein fremd mitgeschicktes Cookie, also kein CSRF.

Was das NICHT abdeckt: Skript-Code, der auf demselben Origin laeuft (XSS in
einer Nachbar-App unter `http://<host>/...`). Fuer den Browser ist das
`same-origin`. Das schliesst erst ein eigener Origin fuer Lego (Teil 2 des
Tickets, haengt an HTTPS/Domain).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})


def normalize_origin(value: str) -> str:
    """`https://Example.org/` -> `https://example.org` (Vergleichsform)."""
    return value.strip().rstrip("/").lower()


def is_allowed_write(
    method: str,
    headers: Mapping[str, str],
    allowed_origins: Iterable[str],
) -> bool:
    """True, wenn die Anfrage nicht schreibt oder nachweislich nicht fremd ist.

    `headers` muss Gross-/Kleinschreibung ignorieren (Starlette-Headers tun das)
    oder kleingeschriebene Schluessel haben.
    """
    if method.upper() not in UNSAFE_METHODS:
        return True

    allowed = {normalize_origin(o) for o in allowed_origins if o.strip()}
    raw_origin = headers.get("origin")
    origin = normalize_origin(raw_origin) if raw_origin is not None else None
    fetch_site = headers.get("sec-fetch-site")

    if fetch_site is not None:
        if fetch_site.strip().lower() in TRUSTED_FETCH_SITES:
            return True
        return origin is not None and origin in allowed

    if origin is None:
        return True
    if origin in allowed:
        return True
    # `Origin: null` (sandboxed iframe, file://, Redirect-Ketten) hat kein netloc.
    origin_host = urlsplit(origin).netloc
    request_host = (headers.get("host") or "").strip().lower()
    return bool(origin_host) and origin_host == request_host
