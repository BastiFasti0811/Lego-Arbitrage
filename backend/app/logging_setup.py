"""Logging-Vorgaben, die API, Celery-Worker und Beat gleichermassen laden.

httpx schreibt jeden Request auf INFO als `HTTP Request: POST <url> ...`.
python-telegram-bot und der Telegram-Test in den Settings rufen
`https://api.telegram.org/bot<TOKEN>/sendMessage` auf; der Token steht also im
Pfad und damit im Log, sobald der Root-Logger auf INFO steht. Genau das tut
`celery worker --loglevel=info`.

Das Level wird an den benannten Loggern gesetzt, nicht am Root: So bleibt es
bestehen, egal was Celery oder uvicorn spaeter am Root einstellen. Fehler
(WARNING und hoeher) kommen weiter durch.
"""

from __future__ import annotations

import logging

# httpcore loggt auf DEBUG Verbindungs- und Request-Details. Wer den Root auf
# DEBUG dreht, bekommt sie trotzdem nicht: Die Kinder-Logger erben WARNING.
QUIETED_LOGGERS = ("httpx", "httpcore")


def quiet_http_client_loggers() -> None:
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
