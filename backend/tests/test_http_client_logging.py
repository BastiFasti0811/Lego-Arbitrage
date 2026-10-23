"""Befund 5: Der Telegram-Bot-Token darf nicht ueber httpx-INFO-Logs ins Log.

`celery worker --loglevel=info` hebt den Root-Logger auf INFO, und httpx
loggt dann `HTTP Request: POST https://api.telegram.org/bot<TOKEN>/...`.
"""

import logging

import httpx
import pytest

from app.logging_setup import QUIETED_LOGGERS, quiet_http_client_loggers

TELEGRAM_URL = "https://api.telegram.org/bot123456:NICHT-ECHTER-TOKEN/sendMessage"


@pytest.fixture
def restore_levels():
    saved = {name: logging.getLogger(name).level for name in QUIETED_LOGGERS}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


async def _send_telegram_like_request() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(TELEGRAM_URL, json={"chat_id": "1", "text": "x"})


@pytest.mark.asyncio
async def test_without_the_fix_httpx_logs_the_token_url(caplog, restore_levels):
    # Gegenprobe: Sonst beweist der eigentliche Test nichts.
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    caplog.set_level(logging.INFO)

    await _send_telegram_like_request()

    assert "/bot" in caplog.text


@pytest.mark.asyncio
async def test_quieted_httpx_keeps_the_token_url_out_of_info_logs(caplog, restore_levels):
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    quiet_http_client_loggers()
    caplog.set_level(logging.INFO)  # Root auf INFO, wie celery --loglevel=info

    await _send_telegram_like_request()

    assert "/bot" not in caplog.text
    assert "api.telegram.org" not in caplog.text


@pytest.mark.asyncio
async def test_root_on_debug_does_not_bring_the_url_back(caplog, restore_levels):
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    quiet_http_client_loggers()
    caplog.set_level(logging.DEBUG)

    await _send_telegram_like_request()

    assert "/bot" not in caplog.text


def test_api_and_celery_entrypoints_apply_the_quiet_levels(restore_levels):
    import app.main  # noqa: F401 - API-Prozess
    import app.tasks.celery_app  # noqa: F401 - Worker und Beat

    for name in QUIETED_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_celery_logging_setup_signal_reapplies_the_levels(restore_levels):
    from celery.signals import after_setup_logger

    import app.tasks.celery_app  # noqa: F401 - verbindet den Signal-Handler

    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)

    after_setup_logger.send(sender=None, logger=logging.getLogger(), loglevel=logging.INFO)

    for name in QUIETED_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_warnings_from_httpx_still_get_through(caplog, restore_levels):
    quiet_http_client_loggers()
    caplog.set_level(logging.INFO)

    logging.getLogger("httpx").warning("verbindung abgebrochen")

    assert "verbindung abgebrochen" in caplog.text


def test_bot_token_in_a_telegram_error_is_redacted(capsys):
    # PTB meldet einen abgelehnten Token woertlich; seit PR #50 loggt die
    # Abholschleife diese Meldung. Gesehen im Prod-Log am 23.10.2026.
    import structlog

    from app.logging_setup import REDACTED

    quiet_http_client_loggers()
    quiet_http_client_loggers()  # zweimal: der Prozessor darf nur einmal haengen

    token = "8656640958:AAFeLkDkfWetdML06hgp7BsrBU1N-WHg1NX"
    structlog.get_logger().warning("telegram.x", error=f"The token `{token}` was rejected by the server.")

    out = capsys.readouterr().out
    assert token not in out
    assert REDACTED in out
    from app.logging_setup import redact_bot_tokens

    assert structlog.get_config()["processors"].count(redact_bot_tokens) == 1


def test_ordinary_values_with_colons_stay_untouched():
    from app.logging_setup import redact_bot_tokens

    event = {"event": "x", "at": "2026-10-23 19:29:11", "url": "https://example.org:8443/a"}
    assert redact_bot_tokens(None, None, dict(event)) == event


TOKEN = "8656640958:AAFeLkDkfWetdML06hgp7BsrBU1N-WHg1NX"


def test_token_inside_a_telegram_url_is_redacted():
    # Review-Finding #51: mit \b davor rutschte /bot<TOKEN>/ durch.
    from app.logging_setup import REDACTED, redact_text

    text = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert redact_text(text) == f"https://api.telegram.org/bot{REDACTED}/getUpdates"


def test_exception_objects_and_tracebacks_are_redacted(capsys):
    import structlog

    quiet_http_client_loggers()
    log = structlog.get_logger()
    log.warning("telegram.obj", error=ValueError(f"The token `{TOKEN}` was rejected"))
    try:
        raise RuntimeError(f"The token `{TOKEN}` was rejected")
    except RuntimeError:
        log.exception("telegram.tb")

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert out.count("<telegram-token>") >= 2


def test_stdlib_records_are_redacted():
    import logging

    quiet_http_client_loggers()
    factory = logging.getLogRecordFactory()
    record = factory("telegram.ext", logging.WARNING, __file__, 1, "url %s", (f"/bot{TOKEN}/x",), None)
    assert TOKEN not in record.getMessage()
