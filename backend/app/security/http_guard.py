"""httpx-Clients, die jede ausgehende URL pruefen, auch jeden Redirect-Hop.

Vorher pruefte der Code nur die Start-URL und nach dem Abruf `response.url`.
Mit `follow_redirects=True` hatte httpx die Zwischen-Hops da schon abgerufen:
Ein Redirect auf `http://redis:6379/` oder `http://169.254.169.254/` ging
raus, und die Pruefung danach meldete nur noch, was schon passiert war.

Der Request-Event-Hook von httpx laeuft innerhalb der Redirect-Schleife vor
jedem einzelnen Request, also auch vor jedem Hop. Wirft er, wird der Hop nicht
mehr gesendet. So bleibt `follow_redirects=True` (mit allem, was httpx dabei
richtig macht: relative Locations, Cookies, Header bei Host-Wechsel), und die
Pruefung sitzt trotzdem vor dem Abruf.

Bekanntes Rest-Risiko: DNS-Rebinding. Die Pruefung loest den Namen auf, httpx
loest ihn beim Verbinden noch einmal auf. Ein Angreifer mit eigenem
DNS-Server kann dazwischen die Antwort wechseln. Sauber loesen liesse sich
das nur, indem die Verbindung auf die geprueften IPs festgenagelt wird
(eigener Transport, SNI/Host-Header getrennt von der Ziel-IP); das ist hier
bewusst nicht eingebaut. Die Host-Allowlist pro Plattform begrenzt den
Angriff auf Domains der Marktplaetze selbst.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

MAX_REDIRECTS = 5

UrlValidator = Callable[[str], object]


def url_validation_hook(validate: UrlValidator) -> Callable[[httpx.Request], Awaitable[None]]:
    """Request-Hook, der `validate` auf jede ausgehende URL anwendet."""

    async def _validate_request(request: httpx.Request) -> None:
        validate(str(request.url))

    return _validate_request


def guarded_async_client(validate: UrlValidator, **kwargs: Any) -> httpx.AsyncClient:
    """AsyncClient mit Redirects, bei dem jeder Hop vor dem Abruf geprueft wird.

    Weitere Request-Hooks aus `event_hooks` bleiben erhalten und laufen nach
    der Pruefung.
    """
    event_hooks = dict(kwargs.pop("event_hooks", None) or {})
    event_hooks["request"] = [url_validation_hook(validate), *event_hooks.get("request", [])]
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("max_redirects", MAX_REDIRECTS)
    return httpx.AsyncClient(event_hooks=event_hooks, **kwargs)
