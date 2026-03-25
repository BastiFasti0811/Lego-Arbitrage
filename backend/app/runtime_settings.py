"""Helpers for runtime settings stored in the database."""

from collections.abc import Iterable

from sqlalchemy import select

from app.config import settings as env_settings
from app.models.base import async_session
from app.models.settings import AppSetting


async def get_settings_map(keys: Iterable[str]) -> dict[str, str | None]:
    """Return DB-backed settings with env fallbacks for known keys."""
    requested_keys = list(dict.fromkeys(keys))
    if not requested_keys:
        return {}

    values: dict[str, str | None] = {}

    async with async_session() as session:
        result = await session.execute(select(AppSetting).where(AppSetting.key.in_(requested_keys)))
        for setting in result.scalars():
            values[setting.key] = setting.value

    fallback_map = {
        "telegram_bot_token": env_settings.telegram_bot_token,
        "telegram_chat_id": env_settings.telegram_chat_id,
        "telegram_alert_on_go_only": str(env_settings.telegram_alert_on_go_only).lower(),
    }

    for key in requested_keys:
        values.setdefault(key, fallback_map.get(key))

    return values


def as_bool(value: str | bool | None, default: bool = False) -> bool:
    """Parse a truthy runtime setting."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
