"""Settings API for DB-backed runtime configuration."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting, get_session
from app.runtime_settings import get_settings_map

router = APIRouter()


class SettingResponse(BaseModel):
    key: str
    value: str | None
    is_secret: bool
    category: str
    label: str | None
    description: str | None

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    key: str
    value: str | None


DEFAULT_SETTINGS = [
    {
        "key": "ebay_api_key",
        "category": "ebay",
        "label": "eBay API Key",
        "description": "eBay Developer App ID",
        "is_secret": True,
    },
    {
        "key": "ebay_api_secret",
        "category": "ebay",
        "label": "eBay API Secret",
        "description": "eBay Developer Cert ID",
        "is_secret": True,
    },
    {
        "key": "kleinanzeigen_email",
        "category": "kleinanzeigen",
        "label": "Kleinanzeigen E-Mail",
        "description": "Login E-Mail fuer Kleinanzeigen",
        "is_secret": False,
    },
    {
        "key": "kleinanzeigen_password",
        "category": "kleinanzeigen",
        "label": "Kleinanzeigen Passwort",
        "description": "Login Passwort",
        "is_secret": True,
    },
    {
        "key": "telegram_bot_token",
        "category": "telegram",
        "label": "Telegram Bot Token",
        "description": "Token von @BotFather",
        "is_secret": True,
    },
    {
        "key": "telegram_chat_id",
        "category": "telegram",
        "label": "Telegram Chat ID",
        "description": "Deine Chat ID fuer Notifications",
        "is_secret": False,
    },
    {
        "key": "telegram_alert_on_go_only",
        "category": "telegram",
        "label": "Nur GO Alerts",
        "description": "Nur GO/GO_STAR Benachrichtigungen senden",
        "is_secret": False,
        "value": "true",
    },
    {
        "key": "catawiki_cookie_header",
        "category": "catawiki",
        "label": "Cookie Header",
        "description": "Optionaler Cookie-Header fuer geschuetzte Catawiki-Requests",
        "is_secret": True,
    },
    {
        "key": "catawiki_user_agent",
        "category": "catawiki",
        "label": "User Agent",
        "description": "Optionaler User Agent fuer Catawiki-Scans",
        "is_secret": False,
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    },
    {
        "key": "catawiki_scan_urls",
        "category": "catawiki",
        "label": "Scan URLs",
        "description": "Eine Kategorie-URL pro Zeile",
        "is_secret": False,
    },
    {
        "key": "catawiki_max_results_per_url",
        "category": "catawiki",
        "label": "Max Ergebnisse pro URL",
        "description": "Begrenzt, wie viele Lots pro Kategorie verarbeitet werden",
        "is_secret": False,
        "value": "20",
    },
    {
        "key": "whatnot_cookie_header",
        "category": "whatnot",
        "label": "Cookie Header",
        "description": "Optionaler Cookie-Header fuer geschuetzte Whatnot-Requests",
        "is_secret": True,
    },
    {
        "key": "whatnot_user_agent",
        "category": "whatnot",
        "label": "User Agent",
        "description": "Optionaler User Agent fuer Whatnot-Scans",
        "is_secret": False,
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    },
    {
        "key": "whatnot_scan_urls",
        "category": "whatnot",
        "label": "Scan URLs",
        "description": "Eine Whatnot-Kategorie- oder Such-URL pro Zeile",
        "is_secret": False,
    },
    {
        "key": "whatnot_max_results_per_url",
        "category": "whatnot",
        "label": "Max Ergebnisse pro URL",
        "description": "Begrenzt, wie viele Lots pro Whatnot-URL verarbeitet werden",
        "is_secret": False,
        "value": "20",
    },
    {
        "key": "bricklink_cookie_header",
        "category": "bricklink",
        "label": "Cookie Header",
        "description": "Optionaler Cookie-Header fuer geschuetzte BrickLink-Requests",
        "is_secret": True,
    },
    {
        "key": "bricklink_user_agent",
        "category": "bricklink",
        "label": "User Agent",
        "description": "Optionaler User Agent fuer BrickLink-Scans",
        "is_secret": False,
        "value": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    },
    {
        "key": "bricklink_scan_urls",
        "category": "bricklink",
        "label": "Scan URLs",
        "description": "Eine BrickLink-Such- oder Store-URL pro Zeile",
        "is_secret": False,
    },
    {
        "key": "bricklink_max_results_per_url",
        "category": "bricklink",
        "label": "Max Ergebnisse pro URL",
        "description": "Begrenzt, wie viele Lots pro BrickLink-URL verarbeitet werden",
        "is_secret": False,
        "value": "20",
    },
]


@router.get("/", response_model=list[SettingResponse])
async def list_settings(category: str | None = None, session: AsyncSession = Depends(get_session)):
    """List all settings. Secret values are masked."""
    result = await session.execute(select(AppSetting))
    existing = result.scalars().all()
    existing_keys = {setting.key for setting in existing}
    missing_defaults = [setting for setting in DEFAULT_SETTINGS if setting["key"] not in existing_keys]

    if not existing or missing_defaults:
        for setting in missing_defaults or DEFAULT_SETTINGS:
            session.add(AppSetting(**setting))
        await session.commit()
        result = await session.execute(select(AppSetting))
        existing = result.scalars().all()

    response: list[SettingResponse] = []
    for setting in existing:
        if category and setting.category != category:
            continue
        setting_response = SettingResponse.model_validate(setting)
        if setting.is_secret and setting.value:
            setting_response.value = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
        response.append(setting_response)

    return response


@router.put("/", response_model=list[SettingResponse])
async def update_settings(updates: list[SettingUpdate], session: AsyncSession = Depends(get_session)):
    """Update one or more settings."""
    for update in updates:
        result = await session.execute(select(AppSetting).where(AppSetting.key == update.key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = update.value
        else:
            session.add(AppSetting(key=update.key, value=update.value))

    await session.commit()
    return await list_settings(session=session)


@router.post("/test-telegram")
async def test_telegram():
    """Send a test message via Telegram."""
    runtime_settings = await get_settings_map(["telegram_bot_token", "telegram_chat_id"])
    token = runtime_settings.get("telegram_bot_token")
    chat_id = runtime_settings.get("telegram_chat_id")

    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram Bot Token und Chat ID muessen gesetzt sein")

    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "LEGO Arbitrage Test - Verbindung erfolgreich!"},
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Telegram Fehler: {response.text}")

    return {"success": True, "message": "Test-Nachricht gesendet!"}
