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
# Kein \\b davor: in `.../bot<TOKEN>/getUpdates` haengt der Token direkt an
# "bot" (Wortzeichen an Wortzeichen). Nur eine fuehrende Ziffer schliesst aus.
_BOT_TOKEN = re.compile(r"(?<![0-9])\d{5,}:[A-Za-z0-9_-]{30,}")
REDACTED = "<telegram-token>"
_PLAIN = (int, float, bool, type(None))


def redact_text(text: str) -> str:
    return _BOT_TOKEN.sub(REDACTED, text) if ":" in text else text


def _redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, _PLAIN):
        return value
    # Exceptions, dicts, Listen: als Text pruefen; nur wenn ein Token drin
    # steht, wird der Wert durch seinen geschwaerzten Text ersetzt.
    text = str(value)
    redacted = redact_text(text)
    return value if redacted == text else redacted


def redact_bot_tokens(_logger, _method, event_dict: dict) -> dict:
    """structlog-Prozessor: Bot-Tokens in allen Werten schwaerzen."""
    for key, value in event_dict.items():
        event_dict[key] = _redact_value(value)
    return event_dict


def _install_token_redaction() -> None:
    processors = list(structlog.get_config()["processors"])
    if redact_bot_tokens in processors:
        return
    # Vor dem Renderer: Tracebacks (exc_info, logger.exception) erst zu Text
    # machen, damit auch die Exception-Meldung geschwaerzt wird.
    at = max(len(processors) - 1, 0)
    head = [] if structlog.processors.format_exc_info in processors else [structlog.processors.format_exc_info]
    processors[at:at] = [*head, redact_bot_tokens]
    structlog.configure(processors=processors)


def _install_stdlib_redaction() -> None:
    """Auch Records der stdlib-Logger (PTB, httpx, uvicorn, Celery)."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_redacts_tokens", False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            return record
        redacted = redact_text(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return record

    factory._redacts_tokens = True
    logging.setLogRecordFactory(factory)


def quiet_http_client_loggers() -> None:
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _install_token_redaction()
    _install_stdlib_redaction()
