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
import re

import structlog

# httpcore loggt auf DEBUG Verbindungs- und Request-Details. Wer den Root auf
# DEBUG dreht, bekommt sie trotzdem nicht: Die Kinder-Logger erben WARNING.
QUIETED_LOGGERS = ("httpx", "httpcore")


# Bot-Token: <bot-id>:<35 Zeichen>. python-telegram-bot schreibt ihn bei 401
# woertlich in die Fehlermeldung ("The token `123:ABC` was rejected"), und die
# landet ueber `error=str(e)` im structlog-Event. Gesehen am 23.10.2026 im
# API-Log nach dem Deploy von PR #50.
_BOT_TOKEN = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}")
REDACTED = "<telegram-token>"


def redact_bot_tokens(_logger, _method, event_dict: dict) -> dict:
    """structlog-Prozessor: Bot-Tokens in allen String-Werten schwaerzen."""
    for key, value in event_dict.items():
        if isinstance(value, str) and ":" in value:
            event_dict[key] = _BOT_TOKEN.sub(REDACTED, value)
    return event_dict


def _install_token_redaction() -> None:
    processors = list(structlog.get_config()["processors"])
    if redact_bot_tokens in processors:
        return
    # Vor dem Renderer, damit er schon den geschwaerzten Text sieht.
    processors.insert(max(len(processors) - 1, 0), redact_bot_tokens)
    structlog.configure(processors=processors)


def quiet_http_client_loggers() -> None:
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _install_token_redaction()
