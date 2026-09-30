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
