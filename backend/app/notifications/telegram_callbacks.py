"""Klicks auf die Knöpfe "Gekauft"/"Abgelehnt" abholen (getUpdates, Long-Polling).

Ein Webhook bräuchte eine von außen per HTTPS erreichbare Adresse; Prod läuft
auf victus hinter Caddy auf :80. Darum fragt der API-Prozess selbst bei
Telegram nach, eine Schleife im Lifespan (uvicorn läuft mit einem Prozess).

Telegram erlaubt je Bot nur einen getUpdates-Abrufer. Läuft beim Deploy kurz
ein zweiter Container, antwortet Telegram 409 (Conflict); die Schleife wartet
dann und versucht es erneut. Ein Update, das zweimal ankommt, ist harmlos:
Entscheidungen sind idempotent.

Solange die Schleife läuft, liefert ein händisches getUpdates nur 409. Wer die
Chat-ID sucht (Runbook in scripts/install-uptime-check.sh), schreibt dem Bot
deshalb /id; er antwortet mit der ID des Chats, aus dem die Nachricht kam.
"""

import asyncio
from datetime import UTC, datetime

import structlog
from telegram import Bot, CallbackQuery
from telegram.error import BadRequest, Conflict, Forbidden, InvalidToken, NetworkError, RetryAfter

from app.models.base import async_session
from app.notifications.deal_actions import answer_text, is_legacy, parse_callback, with_decision
from app.runtime_settings import get_settings_map
from app.services.offer_decisions import record_decision, undo_decision

logger = structlog.get_logger()

LONG_POLL_SECONDS = 25
_IDLE_SECONDS = 60  # ohne Token: so oft nachsehen, ob einer eingetragen wurde
_BROKEN_TOKEN_SECONDS = 600
_TOKEN_RECHECK_SECONDS = 30
_CONFLICT_SECONDS = 30
_ERROR_SECONDS = 15

LEGACY_ANSWER = "Dieser Knopf stammt aus einem älteren Alert und tut nichts mehr. Neue Alerts haben „Im Checker“."
FOREIGN_CHAT_ANSWER = "Nicht freigegeben."
GONE_ANSWER = "Das Angebot ist nicht mehr in der Datenbank."


def is_own_chat(chat, configured: str | None) -> bool:
    """Nur Klicks aus dem eingetragenen Chat zählen (numerische ID oder @name)."""
    if chat is None or not configured:
        return False
    configured = configured.strip()
    if str(chat.id) == configured:
        return True
    username = getattr(chat, "username", None)
    return bool(username) and configured.lower() == f"@{username}".lower()


async def handle_callback(bot: Bot, query: CallbackQuery, chat_id: str | None, now: datetime | None = None) -> str:
    """Einen Klick verarbeiten; liefert den Text, mit dem der Knopf antwortet."""
    message = query.message
    chat = getattr(message, "chat", None)
    if not is_own_chat(chat, chat_id):
        logger.warning("telegram.callback_foreign_chat", chat=getattr(chat, "id", None))
        text = FOREIGN_CHAT_ANSWER
    elif is_legacy(query.data):
        text = LEGACY_ANSWER
    elif (action := parse_callback(query.data)) is None:
        logger.warning("telegram.callback_unknown", data=(query.data or "")[:64])
        text = LEGACY_ANSWER
    else:
        async with async_session() as session:
            if action.decision is None:
                outcome = await undo_decision(session, action.offer_id)
            else:
                outcome = await record_decision(session, action.offer_id, action.decision, now or datetime.now(UTC))
        if not outcome.found:
            text = GONE_ANSWER
        else:
            text = answer_text(outcome.decision, outcome.previous, outcome.set_number)
            logger.info(
                "telegram.decision_saved",
                offer_id=action.offer_id,
                decision=outcome.decision,
                previous=outcome.previous,
            )
            await _swap_keyboard(bot, message, action.offer_id, outcome.decision)

    try:
        await bot.answer_callback_query(query.id, text=text[:200])
    except BadRequest as e:
        # "query is too old": die Entscheidung ist trotzdem gespeichert.
        logger.info("telegram.callback_answer_failed", error=str(e))
    return text


CHAT_ID_COMMANDS = ("/id", "/start")


async def answer_chat_id(bot: Bot, message) -> bool:
    """/id → die ID dieses Chats. Verrät nur dem Fragenden seine eigene ID."""
    text = (getattr(message, "text", None) or "").strip().split("@")[0].lower()
    if text not in CHAT_ID_COMMANDS:
        return False
    await bot.send_message(
        chat_id=message.chat.id,
        text=f"Chat-ID dieses Chats: {message.chat.id}\nIn der App unter Einstellungen → Telegram Chat ID eintragen.",
    )
    return True


async def _swap_keyboard(bot: Bot, message, offer_id: int, decision: str | None) -> None:
    if message is None or not hasattr(message, "message_id"):
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=with_decision(getattr(message, "reply_markup", None), offer_id, decision),
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.info("telegram.callback_edit_failed", error=str(e))


FAILURE_ANSWER = "Speichern fehlgeschlagen, bitte nochmal tippen."


async def _answer_failure(bot: Bot, query) -> None:
    """Der Offset ist schon weiter: ohne Antwort dreht der Knopf, und der Klick
    waere still verloren."""
    if query is None:
        return
    try:
        await bot.answer_callback_query(query.id, text=FAILURE_ANSWER)
    except Exception as e:
        logger.info("telegram.callback_answer_failed", error=str(e))


async def _poll_with(bot: Bot, token: str, offset: int | None) -> int | None:
    """Abholen, bis sich der Token ändert. Liefert den letzten Offset."""
    while True:
        updates = await bot.get_updates(
            offset=offset,
            timeout=LONG_POLL_SECONDS,
            allowed_updates=["callback_query", "message"],
        )
        settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
        for update in updates:
            offset = update.update_id + 1
            try:
                if update.message is not None:
                    await answer_chat_id(bot, update.message)
                    continue
                if update.callback_query is None:
                    continue
                await handle_callback(bot, update.callback_query, settings.get("telegram_chat_id"))
            except Exception as e:  # ein kaputter Klick darf die Schleife nicht beenden
                logger.error("telegram.callback_failed", error=str(e), update_id=update.update_id)
                await _answer_failure(bot, update.callback_query)
        if settings.get("telegram_bot_token") != token:
            return offset


async def poll_callbacks(stop: asyncio.Event) -> None:
    """Endlosschleife für den Lifespan der API; endet erst mit `stop`."""
    offset: int | None = None
    offset_token: str | None = None
    logger.info("telegram.callback_polling_started")
    while not stop.is_set():
        token = await _current_token()
        if not token:
            await _sleep(stop, _IDLE_SECONDS)
            continue
        if token != offset_token:
            # Update-IDs gelten je Bot: ein Offset vom alten Token liesse den
            # neuen Bot schweigen, bis seine IDs aufgeholt haben.
            offset, offset_token = None, token
        try:
            async with Bot(token=token) as bot:
                offset = await _poll_with(bot, token, offset)
        except asyncio.CancelledError:
            raise
        except (InvalidToken, Forbidden) as e:
            # Stand 21.10.: der Prod-Token liefert 401. Nicht minütlich anklopfen.
            logger.warning("telegram.callback_polling_token_rejected", error=str(e))
            await _sleep_until_token_changes(stop, token, _BROKEN_TOKEN_SECONDS)
        except Conflict:
            logger.warning("telegram.callback_polling_conflict")
            await _sleep(stop, _CONFLICT_SECONDS)
        except RetryAfter as e:
            await _sleep(stop, _seconds(e.retry_after))
        except NetworkError as e:
            logger.info("telegram.callback_polling_network", error=str(e))
            await _sleep(stop, _ERROR_SECONDS)
        except Exception as e:
            logger.error("telegram.callback_polling_failed", error=str(e))
            await _sleep(stop, _ERROR_SECONDS)
    logger.info("telegram.callback_polling_stopped")


async def _current_token() -> str | None:
    return (await get_settings_map(["telegram_bot_token"])).get("telegram_bot_token")


async def _sleep_until_token_changes(stop: asyncio.Event, token: str, seconds: float) -> None:
    """Lange warten, aber einen neu eingetragenen Token binnen 30 s aufgreifen."""
    waited = 0.0
    while waited < seconds and not stop.is_set():
        await _sleep(stop, _TOKEN_RECHECK_SECONDS)
        waited += _TOKEN_RECHECK_SECONDS
        if await _current_token() != token:
            return


def _seconds(value) -> float:
    # PTB liefert je nach Version int oder timedelta.
    return value.total_seconds() if hasattr(value, "total_seconds") else float(value)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
